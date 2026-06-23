import unittest
from unittest.mock import AsyncMock, patch

from docling_core.types.doc import DocItemLabel, TextItem

from modules.translator.v2.schemas import TranslatorResponseData
from modules.translator.v2.service import TranslatorV2Service


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
