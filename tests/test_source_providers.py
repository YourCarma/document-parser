import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from modules.translator.v2.sources import LocalUploadSource, WatchtowerSource
from modules.watchtower.exceptions import FileNotFoundInStorage


class LocalUploadSourceTest(unittest.IsolatedAsyncioTestCase):
    async def test_local_source_returns_path_as_is(self):
        source = LocalUploadSource("/tmp/source.docx", "Отчет.docx")

        source_file = await source.acquire("bucket-1")

        self.assertEqual(source_file.local_path, "/tmp/source.docx")
        self.assertEqual(source_file.original_filename, "Отчет.docx")
        self.assertIsNone(source_file.remote_key)
        await source.release()


class WatchtowerSourceTest(unittest.IsolatedAsyncioTestCase):
    async def test_watchtower_source_downloads_and_keeps_extension(self):
        watchtower = AsyncMock()
        watchtower.download_file.side_effect = (
            lambda bucket, file_path, dest_dir: f"{dest_dir}/report.pdf"
        )
        source = WatchtowerSource(watchtower, "documents/report.pdf")

        try:
            source_file = await source.acquire("bucket-1")

            call = watchtower.download_file.await_args
            self.assertEqual(call.args[0], "bucket-1")
            self.assertEqual(call.args[1], "documents/report.pdf")
            self.assertTrue(Path(call.args[2]).is_dir())
            self.assertEqual(source_file.original_filename, "report.pdf")
            self.assertEqual(source_file.remote_key, "documents/report.pdf")
            self.assertTrue(source_file.local_path.endswith(".pdf"))
        finally:
            await source.release()

    async def test_watchtower_source_propagates_not_found(self):
        watchtower = AsyncMock()
        watchtower.download_file.side_effect = FileNotFoundInStorage("нет файла")
        source = WatchtowerSource(watchtower, "documents/report.pdf")

        with self.assertRaises(FileNotFoundInStorage):
            await source.acquire("bucket-1")

        await source.release()

    async def test_watchtower_source_release_removes_temp_dir(self):
        watchtower = AsyncMock()
        created: list[str] = []

        async def download(bucket, file_path, dest_dir):
            created.append(dest_dir)
            path = Path(dest_dir) / "report.pdf"
            path.write_bytes(b"data")
            return str(path)

        watchtower.download_file.side_effect = download
        source = WatchtowerSource(watchtower, "documents/report.pdf")
        await source.acquire("bucket-1")

        await source.release()

        self.assertFalse(Path(created[0]).exists())

    async def test_watchtower_source_release_keeps_external_dir(self):
        watchtower = AsyncMock()
        with tempfile.TemporaryDirectory() as external:
            watchtower.download_file.return_value = f"{external}/report.pdf"
            source = WatchtowerSource(watchtower, "documents/report.pdf", external)
            await source.acquire("bucket-1")

            await source.release()

            self.assertTrue(Path(external).exists())


if __name__ == "__main__":
    unittest.main()
