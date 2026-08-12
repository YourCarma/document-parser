import unittest
from pathlib import Path
from unittest.mock import AsyncMock, call, patch

from docling_core.types.doc import DocItemLabel, TextItem

from modules.translator.v2.schemas import TranslatorResponseData
from modules.translator.v2.service import TranslatorV2Service
from modules.parser.v1.schemas import ParserParams


class FakeDoclingDocument:
    def __init__(self, items):
        self.items = items

    def iterate_items(self):
        for item in self.items:
            yield item, 0


class FakeTranslator:
    source_language = "en"
    target_language = "ru"

    async def translate_element_limited(self, text: str) -> str:
        if text == "slow block":
            raise TimeoutError("request timed out")
        return f"translated {text}"


class TranslatorV2ServiceTest(unittest.IsolatedAsyncioTestCase):
    async def test_translation_uploads_files_to_personal_bucket_root(self):
        webhook = AsyncMock()
        resource_manager = AsyncMock()
        resource_manager.get_user_bucket.return_value = "personal-resource-id"
        watchtower = AsyncMock()
        watchtower.upload_file.side_effect = ["original.docx", "translated.docx"]
        watchtower.get_sharelink.side_effect = ["original-link", "translated-link"]
        service = TranslatorV2Service(
            webhook=webhook,
            watchtower=watchtower,
            resource_manager=resource_manager,
        )
        parser_params = ParserParams(file_path=Path("/tmp/source.docx"))

        with (
            patch(
                "modules.translator.v2.service.run_in_process",
                AsyncMock(return_value=FakeDoclingDocument([])),
            ),
            patch.object(
                service,
                "_translate_with_progress",
                AsyncMock(return_value="/tmp/translated.docx"),
            ),
            patch(
                "modules.translator.v2.service.delete_file",
                AsyncMock(),
            ),
        ):
            await service.run_translation_task(
                user_id="user-1",
                task_id="task-1",
                task_key="task-key",
                file_path="/tmp/source.docx",
                original_filename="Отчет.docx",
                source_language="ru",
                target_language="en",
                parser_params=parser_params,
                executor=object(),
            )

        watchtower.create_folder.assert_not_awaited()
        self.assertEqual(
            watchtower.upload_file.await_args_list,
            [
                call(
                    "personal-resource-id",
                    "/tmp/source.docx",
                    "Отчет.docx",
                ),
                call(
                    "personal-resource-id",
                    "/tmp/translated.docx",
                    "Отчет_(переведённый).docx",
                ),
            ],
        )

    async def test_translate_with_progress_keeps_original_text_on_timeout(self):
        slow = TextItem(
            self_ref="#/texts/0",
            label=DocItemLabel.TEXT,
            orig="slow block",
            text="slow block",
        )
        normal = TextItem(
            self_ref="#/texts/1",
            label=DocItemLabel.TEXT,
            orig="normal block",
            text="normal block",
        )
        webhook = AsyncMock()
        service = TranslatorV2Service(
            webhook=webhook,
            watchtower=AsyncMock(),
            resource_manager=AsyncMock(),
        )
        response_data = TranslatorResponseData(
            original_language="en",
            target_language="ru",
        )

        with patch.object(service, "_export_to_word", AsyncMock(return_value="/tmp/result.docx")):
            result = await service._translate_with_progress(
                FakeTranslator(),
                FakeDoclingDocument([slow, normal]),
                "task-key",
                response_data,
            )

        self.assertEqual(result, "/tmp/result.docx")
        self.assertEqual(
            slow.text,
            "slow block (ошибка запроса, переведите вручную)",
        )
        self.assertEqual(normal.text, "translated normal block")
        self.assertIsNone(response_data.error)


if __name__ == "__main__":
    unittest.main()
