import io
import os
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from log_text import TextDumpError, open_text_dump


class _BrokenStream:
    def __init__(self) -> None:
        self.closed = False

    def read(self, size: int) -> bytes:
        raise OSError('pipe read failed')

    def close(self) -> None:
        self.closed = True


class _ChunkStream:
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = iter(chunks)
        self.read_sizes: list[int] = []
        self.closed = False

    def read(self, size: int) -> bytes:
        self.read_sizes.append(size)
        return next(self.chunks)

    def close(self) -> None:
        self.closed = True


class _FakeProcess:
    def __init__(self, stdout, return_code: int, *, running: bool = False) -> None:
        self.stdout = stdout
        self.return_code = return_code
        self.running = running
        self.terminated = False
        self.killed = False
        self.wait_calls = 0

    def poll(self):
        return None if self.running else self.return_code

    def wait(self, timeout=None):
        self.wait_calls += 1
        self.running = False
        return self.return_code

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True
        self.running = False


class _StubbornProcess(_FakeProcess):
    def wait(self, timeout=None):
        self.wait_calls += 1
        if timeout is not None and not self.killed:
            raise subprocess.TimeoutExpired('mavlogdump.py', timeout)
        self.running = False
        return self.return_code


class LogTextTests(unittest.TestCase):
    def test_read_failure_before_output_raises_adapter_error_and_reaps_process(self):
        stream = _BrokenStream()
        process = _FakeProcess(stream, 1, running=True)

        with patch('log_text.subprocess.Popen', return_value=process):
            with self.assertRaisesRegex(TextDumpError, 'conversion failed'):
                open_text_dump(Path('/tmp/source.bin'))

        self.assertTrue(stream.closed)
        self.assertTrue(process.terminated)
        self.assertGreaterEqual(process.wait_calls, 1)

    def test_failure_after_output_appends_marker_without_eagerly_reading_rest(self):
        stream = _ChunkStream([b'first chunk', b'second chunk', b''])
        process = _FakeProcess(stream, 7)

        with patch('log_text.subprocess.Popen', return_value=process):
            dump = open_text_dump(Path('/tmp/source.bin'))
            self.assertEqual(stream.read_sizes, [64 * 1024])
            with self.assertLogs('log_text', level='ERROR'):
                chunks = list(dump)

        self.assertEqual(
            chunks,
            [
                b'first chunk',
                b'second chunk',
                b'\n[TEXT DUMP INCOMPLETE: conversion failed]\n',
            ],
        )
        self.assertEqual(stream.read_sizes, [64 * 1024] * 3)
        self.assertTrue(stream.closed)
        self.assertGreaterEqual(process.wait_calls, 1)

    def test_close_terminates_and_reaps_an_actual_converter_process(self):
        with TemporaryDirectory(prefix='text-dump-process-') as sandbox:
            scripts = Path(sandbox)
            pid_file = scripts / 'converter.pid'
            (scripts / 'mavlogdump.py').write_text(
                'import os, pathlib, sys, time\n'
                f'pathlib.Path({str(pid_file)!r}).write_text(str(os.getpid()))\n'
                'sys.stdout.buffer.write(b"x" * 65536)\n'
                'sys.stdout.buffer.flush()\n'
                'time.sleep(30)\n',
                encoding='utf-8',
            )
            with patch('log_text.sysconfig.get_path', return_value=str(scripts)):
                dump = open_text_dump(Path('/tmp/source.bin'))
            pid = int(pid_file.read_text(encoding='utf-8'))

            dump.close()

            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)

    def test_close_kills_child_that_does_not_terminate(self):
        stream = _ChunkStream([b'first chunk'])
        process = _StubbornProcess(stream, -9, running=True)
        with patch('log_text.subprocess.Popen', return_value=process):
            dump = open_text_dump(Path('/tmp/source.bin'))

        dump.close()

        self.assertTrue(stream.closed)
        self.assertTrue(process.terminated)
        self.assertTrue(process.killed)
        self.assertEqual(process.wait_calls, 2)

    def test_missing_converter_has_explicit_error(self):
        with TemporaryDirectory(prefix='text-dump-missing-') as sandbox:
            with patch('log_text.sysconfig.get_path', return_value=sandbox):
                with self.assertRaisesRegex(TextDumpError, 'not installed'):
                    open_text_dump(Path('/tmp/source.bin'))


if __name__ == '__main__':
    unittest.main()
