import io
import os
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

import catalog
from catalog import CatalogStore
from catalog_routes import init_catalog
import python_backend


class UploadSecurityTests(unittest.TestCase):
    def setUp(self):
        self.sandbox = TemporaryDirectory(prefix="upload-tests-")
        self.sandbox_path = Path(self.sandbox.name).resolve()
        self.upload_folder = self.sandbox_path / "uploads" / "incoming"
        self.upload_folder.mkdir(parents=True)
        self.old_upload_folder = python_backend.app.config.get("UPLOAD_FOLDER")
        self.old_testing = python_backend.app.config.get("TESTING")
        self.old_public_origin = python_backend.app.config.get("PUBLIC_ORIGIN")
        self.old_catalog_dir = python_backend.app.config.get("CATALOG_DIR")
        self.old_catalog = python_backend.app.extensions.get("catalog")
        python_backend.app.config.update(
            UPLOAD_FOLDER=str(self.upload_folder),
            TESTING=True,
            PUBLIC_ORIGIN="http://localhost",
        )
        self.store = CatalogStore(self.sandbox_path / "catalog")
        init_catalog(python_backend.app, store=self.store)
        self.client = python_backend.app.test_client()

    def tearDown(self):
        python_backend.app.config.update(
            UPLOAD_FOLDER=self.old_upload_folder,
            TESTING=self.old_testing,
            PUBLIC_ORIGIN=self.old_public_origin,
            CATALOG_DIR=self.old_catalog_dir,
        )
        if self.old_catalog is None:
            python_backend.app.extensions.pop("catalog", None)
        else:
            python_backend.app.extensions["catalog"] = self.old_catalog
        self.sandbox.cleanup()

    def _assert_inside_sandbox(self, path):
        path = Path(path).resolve()
        self.assertEqual(os.path.commonpath((self.sandbox_path, path)), str(self.sandbox_path))

    def _post_upload(self, filename, content=b"log bytes"):
        return self.client.post(
            "/api/upload",
            headers={"Origin": "http://localhost"},
            data={"file": (io.BytesIO(content), filename)},
            content_type="multipart/form-data",
        )

    def test_bin_and_log_uploads_keep_original_name_and_retain_temporary_file(self):
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
            self.assertTrue(path.exists())
            self.assertEqual(path.parent, self.store.files)
            self.assertIn("log_id", response.get_json())
            self.assertIn("expires_at", response.get_json())

    def test_analysis_failure_still_removes_temp_file_and_directory(self):
        observed = []

        def analyze(filepath, _filename):
            observed.append(Path(filepath))
            self.assertTrue(Path(filepath).exists())
            raise RuntimeError("analysis failed")

        with self.assertLogs(python_backend.app.logger.name, level="ERROR"):
            with patch.object(python_backend, "run_analysis", side_effect=analyze):
                response = self._post_upload("failure.bin")

        self.assertEqual(response.status_code, 500)
        self.assertEqual(len(observed), 1)
        self._assert_inside_sandbox(observed[0])
        self.assertFalse(observed[0].exists())
        self.assertEqual(list(self.store.files.iterdir()), [])

    def test_partial_copy_failure_still_removes_partial_file(self):
        def partial_copy(_source, destination, *args, **kwargs):
            destination.write(b"partial")
            raise OSError("disk full")

        with self.assertLogs(python_backend.app.logger.name, level="ERROR"):
            with patch.object(catalog.shutil, "copyfileobj", partial_copy):
                response = self._post_upload("partial.bin")

        self.assertEqual(response.status_code, 500)
        self.assertEqual(list(self.store.files.iterdir()), [])

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
        self.assertTrue(all(path.exists() for path in observed))

    def test_upload_limits_and_extension_allowlist_remain(self):
        self.assertEqual(python_backend.app.config["MAX_CONTENT_LENGTH"], 500 * 1024 * 1024)
        with patch.object(python_backend, "run_analysis") as analyze:
            response = self._post_upload("flight.zip")
        self.assertEqual(response.status_code, 400)
        analyze.assert_not_called()

    def test_oversized_upload_returns_413(self):
        old_limit = python_backend.app.config["MAX_CONTENT_LENGTH"]
        self.addCleanup(
            python_backend.app.config.__setitem__, "MAX_CONTENT_LENGTH", old_limit
        )
        python_backend.app.config["MAX_CONTENT_LENGTH"] = 256

        response = self._post_upload("flight.bin", b"x" * 1024)

        self.assertEqual(response.status_code, 413)
        self.assertEqual(
            response.get_json(),
            {"error": "File too large. Maximum size: 500MB"},
        )

    def test_legacy_analysis_cannot_escape_upload_folder(self):
        outside = self.sandbox_path / "outside.bin"
        outside.write_bytes(b"secret")
        (self.upload_folder / "escape.bin").symlink_to(outside)

        with patch.object(python_backend, "run_analysis") as analyze:
            response = self.client.get("/api/analyze/escape.bin")

        self.assertEqual(response.status_code, 404)
        analyze.assert_not_called()

    def test_load_log_closes_parser_on_eof(self):
        parser = MagicMock()
        parser.recv_match.return_value = None
        with redirect_stdout(io.StringIO()):
            with patch.object(python_backend.mavutil, "mavlink_connection", return_value=parser):
                result = python_backend.load_log("ignored.bin")

        self.assertEqual(result, {})
        parser.close.assert_called_once_with()

    def test_load_log_closes_parser_when_parser_raises(self):
        parser = MagicMock()
        parser.recv_match.side_effect = RuntimeError("parser failed")
        with redirect_stdout(io.StringIO()):
            with patch.object(python_backend.mavutil, "mavlink_connection", return_value=parser):
                with self.assertRaisesRegex(RuntimeError, "parser failed"):
                    python_backend.load_log("ignored.bin")

        parser.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
