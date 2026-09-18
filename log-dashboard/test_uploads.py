import io
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from werkzeug.datastructures import FileStorage

import python_backend


class UploadSecurityTests(unittest.TestCase):
    def setUp(self):
        self.sandbox = TemporaryDirectory(prefix="upload-tests-")
        self.sandbox_path = Path(self.sandbox.name).resolve()
        self.upload_folder = self.sandbox_path / "uploads" / "incoming"
        self.upload_folder.mkdir(parents=True)
        self.old_upload_folder = python_backend.app.config.get("UPLOAD_FOLDER")
        self.old_testing = python_backend.app.config.get("TESTING")
        python_backend.app.config.update(
            UPLOAD_FOLDER=str(self.upload_folder),
            TESTING=True,
        )
        self.tempdir_patch = patch.object(
            python_backend,
            "TemporaryDirectory",
            side_effect=lambda prefix: TemporaryDirectory(
                prefix=prefix, dir=str(self.sandbox_path)
            ),
        )
        self.tempdir_patch.start()
        self.client = python_backend.app.test_client()

    def tearDown(self):
        python_backend.app.config["UPLOAD_FOLDER"] = self.old_upload_folder
        python_backend.app.config["TESTING"] = self.old_testing
        self.tempdir_patch.stop()
        self.sandbox.cleanup()

    def _assert_inside_sandbox(self, path):
        path = Path(path).resolve()
        self.assertEqual(os.path.commonpath((self.sandbox_path, path)), str(self.sandbox_path))

    def _post_upload(self, filename, content=b"log bytes"):
        return self.client.post(
            "/api/upload",
            data={"file": (io.BytesIO(content), filename)},
            content_type="multipart/form-data",
        )

    def test_bin_and_log_uploads_keep_original_name_and_cleanup_temp_file(self):
        for filename in ("flight.BIN", "flight.log"):
            observed = []

            def analyze(filepath, original_name):
                path = Path(filepath)
                observed.append((path, original_name, path.read_bytes()))
                return {"success": True, "filename": original_name}

            with patch.object(python_backend, "run_analysis", side_effect=analyze):
                response = self._post_upload(filename, b"expected content")

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.get_json()["filename"], filename)
            self.assertEqual(len(observed), 1)
            path, original_name, content = observed[0]
            self._assert_inside_sandbox(path)
            self.assertEqual(original_name, filename)
            self.assertEqual(content, b"expected content")
            self.assertFalse(path.exists())
            self.assertFalse(path.parent.exists())

    def test_analysis_failure_still_removes_temp_file_and_directory(self):
        observed = []

        def analyze(filepath, _filename):
            observed.append(Path(filepath))
            self.assertTrue(Path(filepath).exists())
            raise RuntimeError("analysis failed")

        with patch.object(python_backend, "run_analysis", side_effect=analyze):
            response = self._post_upload("failure.bin")

        self.assertEqual(response.status_code, 500)
        self.assertEqual(len(observed), 1)
        self._assert_inside_sandbox(observed[0])
        self.assertFalse(observed[0].exists())
        self.assertFalse(observed[0].parent.exists())

    def test_partial_save_failure_still_removes_partial_file_and_directory(self):
        def partial_save(_file, destination, *args, **kwargs):
            Path(destination).write_bytes(b"partial")
            raise OSError("disk full")

        with patch.object(FileStorage, "save", partial_save):
            response = self._post_upload("partial.bin")

        self.assertEqual(response.status_code, 500)
        self.assertEqual(list(self.sandbox_path.iterdir()), [self.sandbox_path / "uploads"])
        self.assertEqual(list(self.upload_folder.iterdir()), [])

    def test_unsafe_names_cannot_overwrite_sentinel(self):
        sentinel = self.sandbox_path / "sentinel.bin"
        sentinel.write_bytes(b"do not overwrite")

        for filename in ("../../sentinel.bin", str(sentinel)):
            observed = []

            def analyze(filepath, _filename):
                observed.append(Path(filepath))
                return {"success": True}

            with patch.object(python_backend, "run_analysis", side_effect=analyze):
                response = self._post_upload(filename)

            self.assertEqual(response.status_code, 200)
            self.assertEqual(sentinel.read_bytes(), b"do not overwrite")
            self.assertEqual(len(observed), 1)
            self._assert_inside_sandbox(observed[0])
            self.assertNotEqual(observed[0].resolve(), sentinel.resolve())

    def test_repeated_names_use_independent_temp_paths_and_preserve_existing_upload(self):
        existing = self.upload_folder / "same.bin"
        existing.write_bytes(b"existing upload")
        observed = []

        def analyze(filepath, _filename):
            observed.append(Path(filepath))
            return {"success": True}

        with patch.object(python_backend, "run_analysis", side_effect=analyze):
            first = self._post_upload("same.bin", b"first")
            second = self._post_upload("same.bin", b"second")

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(len(observed), 2)
        self.assertNotEqual(observed[0], observed[1])
        self.assertEqual(existing.read_bytes(), b"existing upload")
        self.assertTrue(all(not path.exists() and not path.parent.exists() for path in observed))

    def test_upload_limits_and_extension_allowlist_remain(self):
        self.assertEqual(python_backend.app.config["MAX_CONTENT_LENGTH"], 500 * 1024 * 1024)
        with patch.object(python_backend, "run_analysis") as analyze:
            response = self._post_upload("flight.zip")
        self.assertEqual(response.status_code, 400)
        analyze.assert_not_called()

    def test_load_log_closes_parser_on_eof(self):
        parser = MagicMock()
        parser.recv_match.return_value = None
        with patch.object(python_backend.mavutil, "mavlink_connection", return_value=parser):
            result = python_backend.load_log("ignored.bin")

        self.assertEqual(result, {})
        parser.close.assert_called_once_with()

    def test_load_log_closes_parser_when_parser_raises(self):
        parser = MagicMock()
        parser.recv_match.side_effect = RuntimeError("parser failed")
        with patch.object(python_backend.mavutil, "mavlink_connection", return_value=parser):
            with self.assertRaisesRegex(RuntimeError, "parser failed"):
                python_backend.load_log("ignored.bin")

        parser.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
