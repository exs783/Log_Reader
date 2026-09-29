from __future__ import annotations

import logging
import subprocess
import sys
import sysconfig
from pathlib import Path
from typing import IO, Iterator


logger = logging.getLogger(__name__)

_CHUNK_SIZE = 64 * 1024
_INCOMPLETE_MARKER = b'\n[TEXT DUMP INCOMPLETE: conversion failed]\n'


class TextDumpError(Exception):
    pass


class TextDump(Iterator[bytes]):
    def __init__(
        self,
        process: subprocess.Popen[bytes],
        stdout: IO[bytes],
        first_chunk: bytes,
    ) -> None:
        self._process = process
        self._stdout = stdout
        self._first_chunk: bytes | None = first_chunk or None
        self._emitted_output = bool(first_chunk)
        self._closed = False

    def __iter__(self) -> TextDump:
        return self

    def __next__(self) -> bytes:
        if self._closed:
            raise StopIteration
        if self._first_chunk is not None:
            chunk = self._first_chunk
            self._first_chunk = None
            return chunk
        try:
            chunk = self._stdout.read(_CHUNK_SIZE)
        except BaseException:
            self.close()
            raise
        if chunk:
            self._emitted_output = True
            return chunk

        return_code = self._process.wait()
        if return_code and self._emitted_output:
            logger.error('Log text conversion failed with exit code %s', return_code)
            self.close()
            return _INCOMPLETE_MARKER
        self.close()
        raise StopIteration

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._stdout.close()
        finally:
            if self._process.poll() is None:
                self._process.terminate()
                try:
                    self._process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self._process.kill()
                    self._process.wait()
            else:
                self._process.wait()


def open_text_dump(path: Path) -> TextDump:
    converter = Path(sysconfig.get_path('scripts')) / 'mavlogdump.py'
    if not converter.is_file():
        raise TextDumpError('Log text converter is not installed')

    command = [sys.executable, '-u', str(converter), str(path)]
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except OSError as error:
        raise TextDumpError('Log text converter could not start') from error
    if process.stdout is None:
        process.terminate()
        process.wait()
        raise TextDumpError('Log text converter did not provide output')
    try:
        first_chunk = process.stdout.read(_CHUNK_SIZE)
        if not first_chunk:
            return_code = process.wait()
            if return_code:
                process.stdout.close()
                raise TextDumpError('Log text conversion failed')
        return TextDump(process, process.stdout, first_chunk)
    except BaseException as error:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        else:
            process.wait()
        process.stdout.close()
        if isinstance(error, Exception) and not isinstance(error, TextDumpError):
            raise TextDumpError('Log text conversion failed') from error
        raise
