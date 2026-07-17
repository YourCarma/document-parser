import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from modules.parser.v1.utils import (
    UPLOAD_CHUNK_SIZE,
    delete_file,
    file_cleanup_task,
    save_file,
)


class ChunkedUpload:
    def __init__(self, filename: str, chunks: list[bytes]):
        self.filename = filename
        self._chunks = iter(chunks)
        self.read_sizes = []

    async def read(self, size: int = -1) -> bytes:
        self.read_sizes.append(size)
        return next(self._chunks, b"")


class ParserFileUtilsTest(unittest.IsolatedAsyncioTestCase):
    async def test_client_filename_cannot_escape_temp_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            upload = ChunkedUpload("../../outside.pdf", [b"safe content"])
            with patch("modules.parser.v1.utils.tempfile.gettempdir", return_value=temp_dir):
                path = await save_file(upload)

            try:
                self.assertEqual(path.parent, Path(temp_dir))
                self.assertEqual(path.suffix, ".pdf")
                self.assertEqual(path.read_bytes(), b"safe content")
            finally:
                await delete_file(path)

    async def test_identically_named_uploads_get_distinct_paths(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            first = ChunkedUpload("report.pdf", [b"first"])
            second = ChunkedUpload("report.pdf", [b"second"])
            with patch("modules.parser.v1.utils.tempfile.gettempdir", return_value=temp_dir):
                first_path, second_path = await asyncio.gather(
                    save_file(first),
                    save_file(second),
                )

            try:
                self.assertNotEqual(first_path, second_path)
                self.assertEqual(first_path.read_bytes(), b"first")
                self.assertEqual(second_path.read_bytes(), b"second")
            finally:
                await delete_file(first_path)
                await delete_file(second_path)

    async def test_upload_is_read_in_bounded_chunks(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            upload = ChunkedUpload("large.bin", [b"one", b"two", b"three"])
            with patch("modules.parser.v1.utils.tempfile.gettempdir", return_value=temp_dir):
                path = await save_file(upload)

            try:
                self.assertEqual(path.read_bytes(), b"onetwothree")
                self.assertGreater(len(upload.read_sizes), 1)
                self.assertTrue(all(size == UPLOAD_CHUNK_SIZE for size in upload.read_sizes))
            finally:
                await delete_file(path)

    async def test_cleanup_is_idempotent_and_background_task_removes_file(self):
        await delete_file(None)

        with tempfile.NamedTemporaryFile(delete=False) as temporary_file:
            path = Path(temporary_file.name)

        await file_cleanup_task(path)()
        self.assertFalse(path.exists())

        await delete_file(path)


if __name__ == "__main__":
    unittest.main()
