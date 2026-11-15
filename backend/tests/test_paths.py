import os
import unittest
from unittest.mock import patch

from app.core.config import settings
from app.core.paths import resolve_storage_path


class StoragePathMigrationTests(unittest.TestCase):
    def test_legacy_docker_path_is_rebased(self):
        with patch.object(settings, "STORAGE_DIR", "/new/root/storage"):
            self.assertEqual(
                resolve_storage_path("/data/storage/screenshots/job/000.jpg"),
                os.path.join("/new/root/storage", "screenshots", "job", "000.jpg"),
            )

    def test_windows_path_from_another_computer_is_rebased(self):
        with patch.object(settings, "STORAGE_DIR", "/new/root/storage"):
            self.assertEqual(
                resolve_storage_path(r"C:\Users\Ali\video-workflow\storage\runs\1\fail.png"),
                os.path.join("/new/root/storage", "runs", "1", "fail.png"),
            )

    def test_current_storage_path_is_kept(self):
        with patch.object(settings, "STORAGE_DIR", "/new/root/storage"):
            value = os.path.join("/new/root/storage", "videos", "a.mp4")
            self.assertEqual(resolve_storage_path(value), value)

    def test_unrelated_path_is_kept(self):
        with patch.object(settings, "STORAGE_DIR", "/new/root/storage"):
            self.assertEqual(resolve_storage_path("/tmp/other.mp4"), "/tmp/other.mp4")

    def test_empty_path(self):
        self.assertIsNone(resolve_storage_path(None))
        self.assertEqual(resolve_storage_path(""), "")


if __name__ == "__main__":
    unittest.main()
