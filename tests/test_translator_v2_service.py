import asyncio
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, call, patch

import aiohttp
from docling_core.types.doc import DocItemLabel, TextItem
from fastapi import HTTPException

from modules.translator.v2.schemas import TranslationOutcome, TranslatorResponseData
from modules.translator.v2.service import TranslatorV2Service
from modules.translator.v2.sources import SourceFile, SourceFileProviderABC
from modules.parser.v1.schemas import ParserParams
from modules.watchtower.exceptions import FileNotFoundInStorage
from modules.webhook_manager.schemas import TaskStatus


class FakeSource(SourceFileProviderABC):
    """Источник, имитирующий уже лежащий в бакете файл."""

    def __init__(self, remote_key: str | None = "documents/report.pdf"):
        self.remote_key = remote_key
        self.released = 0
        self.error: BaseException | None = None

    async def acquire(self, bucket: str) -> SourceFile:
        if self.error is not None:
            raise self.error
        return SourceFile(
            local_path="/tmp/source.pdf",
            original_filename="report.pdf",
            remote_key=self.remote_key,
        )

    async def release(self) -> None:
        self.released += 1


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


class FlakyTranslator:
    """Переводчик, падающий разными типами ошибок на разных элементах."""

    source_language = "en"
    target_language = "ru"

    async def translate_element_limited(self, text: str) -> str:
        if text == "http block":
            raise HTTPException(status_code=422, detail="Некорректный текст")
        if text == "client block":
            raise aiohttp.ClientError("соединение разорвано")
        return f"translated {text}"


def text_item(index: int, text: str) -> TextItem:
    return TextItem(
        self_ref=f"#/texts/{index}",
        label=DocItemLabel.TEXT,
        orig=text,
        text=text,
    )


class TranslatorV2ServiceTest(unittest.IsolatedAsyncioTestCase):
    def _service(self, webhook, watchtower=None, resource_manager=None):
        if resource_manager is None:
            resource_manager = AsyncMock()
            resource_manager.get_user_bucket.return_value = "personal-resource-id"
        if watchtower is None:
            watchtower = AsyncMock()
            watchtower.upload_file.return_value = "Отчет.docx"
            watchtower.get_sharelink.return_value = "share-link"
        return TranslatorV2Service(
            webhook=webhook,
            watchtower=watchtower,
            resource_manager=resource_manager,
        )

    async def _run(self, service, **overrides):
        params = dict(
            user_id="user-1",
            task_id="task-1",
            task_key="task-key",
            file_path="/tmp/source.docx",
            original_filename="Отчет.docx",
            source_language="ru",
            target_language="en",
            parser_params=ParserParams(file_path=Path("/tmp/source.docx")),
            executor=object(),
        )
        params.update(overrides)
        return await service.run_translation_task(**params)

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

        with (
            patch(
                "modules.translator.v2.service.run_in_process",
                AsyncMock(return_value=FakeDoclingDocument([])),
            ),
            patch.object(
                service,
                "_translate_with_progress",
                AsyncMock(
                    return_value=TranslationOutcome(
                        file_path="/tmp/translated.docx",
                    )
                ),
            ),
            patch(
                "modules.translator.v2.service.delete_file",
                AsyncMock(),
            ),
        ):
            status = await self._run(service)

        self.assertEqual(status, TaskStatus.READY)
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
        slow = text_item(0, "slow block")
        normal = text_item(1, "normal block")
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
            outcome = await service._translate_with_progress(
                FakeTranslator(),
                FakeDoclingDocument([slow, normal]),
                "task-key",
                response_data,
            )

        self.assertEqual(outcome.file_path, "/tmp/result.docx")
        self.assertEqual(outcome.untranslated_count, 1)
        self.assertEqual(
            slow.text,
            "slow block (ошибка запроса, переведите вручную)",
        )
        self.assertEqual(normal.text, "translated normal block")
        self.assertIsNone(response_data.error)

    async def test_translate_tracked_survives_http_exception_and_client_error(self):
        http_item = text_item(0, "http block")
        client_item = text_item(1, "client block")
        normal = text_item(2, "normal block")
        service = TranslatorV2Service(
            webhook=AsyncMock(),
            watchtower=AsyncMock(),
            resource_manager=AsyncMock(),
        )
        response_data = TranslatorResponseData(
            original_language="en",
            target_language="ru",
        )

        with patch.object(service, "_export_to_word", AsyncMock(return_value="/tmp/result.docx")):
            outcome = await service._translate_with_progress(
                FlakyTranslator(),
                FakeDoclingDocument([http_item, client_item, normal]),
                "task-key",
                response_data,
            )

        self.assertEqual(outcome.untranslated_count, 2)
        self.assertTrue(
            http_item.text.endswith("(ошибка запроса, переведите вручную)")
        )
        self.assertTrue(
            client_item.text.endswith("(ошибка запроса, переведите вручную)")
        )
        self.assertEqual(normal.text, "translated normal block")

    async def test_intermediate_status_failure_does_not_fail_task(self):
        webhook = AsyncMock()
        webhook.update_progress.side_effect = [
            Exception("webhook_manager недоступен"),
            None,
            None,
            None,
            None,
        ]
        service = self._service(webhook)

        with (
            patch(
                "modules.translator.v2.service.run_in_process",
                AsyncMock(return_value=FakeDoclingDocument([])),
            ),
            patch.object(
                service,
                "_translate_with_progress",
                AsyncMock(
                    return_value=TranslationOutcome(file_path="/tmp/translated.docx")
                ),
            ),
            patch("modules.translator.v2.service.delete_file", AsyncMock()),
        ):
            status = await self._run(service)

        self.assertEqual(status, TaskStatus.READY)

    async def test_terminal_status_failure_does_not_raise(self):
        webhook = AsyncMock()
        webhook.update_progress.side_effect = Exception("webhook_manager недоступен")
        webhook.update_response_data.side_effect = Exception("webhook_manager недоступен")
        service = self._service(webhook)

        with (
            patch(
                "modules.translator.v2.service.run_in_process",
                AsyncMock(return_value=FakeDoclingDocument([])),
            ),
            patch.object(
                service,
                "_translate_with_progress",
                AsyncMock(
                    return_value=TranslationOutcome(file_path="/tmp/translated.docx")
                ),
            ),
            patch("modules.translator.v2.service.delete_file", AsyncMock()),
        ):
            status = await self._run(service)

        self.assertEqual(status, TaskStatus.READY)

    async def test_untranslated_elements_are_counted_in_text_status(self):
        webhook = AsyncMock()
        service = self._service(webhook)

        with (
            patch(
                "modules.translator.v2.service.run_in_process",
                AsyncMock(return_value=FakeDoclingDocument([])),
            ),
            patch.object(
                service,
                "_translate_with_progress",
                AsyncMock(
                    return_value=TranslationOutcome(
                        file_path="/tmp/translated.docx",
                        untranslated_count=3,
                    )
                ),
            ),
            patch("modules.translator.v2.service.delete_file", AsyncMock()),
        ):
            status = await self._run(service)

        self.assertEqual(status, TaskStatus.READY)
        published = webhook.update_response_data.await_args_list[-1].args[1]
        self.assertEqual(published["text_status"], "Готово. Не переведено элементов: 3")

    async def test_task_timeout_publishes_error_status(self):
        webhook = AsyncMock()
        resource_manager = AsyncMock()

        async def slow_bucket(user_id):
            await asyncio.sleep(1)
            return "personal-resource-id"

        resource_manager.get_user_bucket.side_effect = slow_bucket
        service = self._service(webhook, resource_manager=resource_manager)

        with (
            patch("modules.translator.v2.service.settings.TASK_TIMEOUT_SECS", 0.01),
            patch("modules.translator.v2.service.delete_file", AsyncMock()),
        ):
            status = await self._run(service)

        self.assertEqual(status, TaskStatus.ERROR)
        last_progress_call = webhook.update_progress.await_args_list[-1]
        self.assertEqual(last_progress_call.args[1], 0)
        self.assertEqual(last_progress_call.args[2], TaskStatus.ERROR)
        published = webhook.update_response_data.await_args_list[-1].args[1]
        self.assertEqual(published["text_status"], "Превышено время обработки")
        self.assertEqual(published["error"], "Превышено время обработки")

    async def test_queue_source_is_not_uploaded_again(self):
        webhook = AsyncMock()
        watchtower = AsyncMock()
        watchtower.upload_file.return_value = "translated.docx"
        watchtower.get_sharelink.side_effect = ["original-link", "translated-link"]
        service = self._service(webhook, watchtower=watchtower)
        source = FakeSource()

        with (
            patch(
                "modules.translator.v2.service.run_in_process",
                AsyncMock(return_value=FakeDoclingDocument([])),
            ),
            patch.object(
                service,
                "_translate_with_progress",
                AsyncMock(
                    return_value=TranslationOutcome(file_path="/tmp/translated.docx")
                ),
            ),
            patch("modules.translator.v2.service.delete_file", AsyncMock()),
        ):
            status = await self._run(service, source=source)

        self.assertEqual(status, TaskStatus.READY)
        self.assertEqual(watchtower.upload_file.await_count, 1)
        self.assertEqual(watchtower.get_sharelink.await_count, 2)
        self.assertEqual(
            watchtower.get_sharelink.await_args_list[0],
            call("personal-resource-id", "documents/report.pdf"),
        )

    async def test_output_prefix_is_passed_to_result_upload(self):
        webhook = AsyncMock()
        watchtower = AsyncMock()
        watchtower.upload_file.return_value = "translated.docx"
        watchtower.get_sharelink.return_value = "link"
        service = self._service(webhook, watchtower=watchtower)

        with (
            patch(
                "modules.translator.v2.service.run_in_process",
                AsyncMock(return_value=FakeDoclingDocument([])),
            ),
            patch.object(
                service,
                "_translate_with_progress",
                AsyncMock(
                    return_value=TranslationOutcome(file_path="/tmp/translated.docx")
                ),
            ),
            patch("modules.translator.v2.service.delete_file", AsyncMock()),
        ):
            await self._run(
                service, source=FakeSource(), output_prefix="translated/task-1"
            )

        self.assertEqual(
            watchtower.upload_file.await_args_list[-1].kwargs,
            {"prefix": "translated/task-1"},
        )

    async def test_empty_output_prefix_keeps_positional_call(self):
        webhook = AsyncMock()
        watchtower = AsyncMock()
        watchtower.upload_file.side_effect = ["original.docx", "translated.docx"]
        watchtower.get_sharelink.side_effect = ["original-link", "translated-link"]
        service = self._service(webhook, watchtower=watchtower)

        with (
            patch(
                "modules.translator.v2.service.run_in_process",
                AsyncMock(return_value=FakeDoclingDocument([])),
            ),
            patch.object(
                service,
                "_translate_with_progress",
                AsyncMock(
                    return_value=TranslationOutcome(file_path="/tmp/translated.docx")
                ),
            ),
            patch("modules.translator.v2.service.delete_file", AsyncMock()),
        ):
            await self._run(service)

        self.assertEqual(watchtower.upload_file.await_args_list[-1].kwargs, {})

    async def test_explicit_bucket_skips_resource_manager(self):
        webhook = AsyncMock()
        resource_manager = AsyncMock()
        service = self._service(webhook, resource_manager=resource_manager)

        with (
            patch(
                "modules.translator.v2.service.run_in_process",
                AsyncMock(return_value=FakeDoclingDocument([])),
            ),
            patch.object(
                service,
                "_translate_with_progress",
                AsyncMock(
                    return_value=TranslationOutcome(file_path="/tmp/translated.docx")
                ),
            ),
            patch("modules.translator.v2.service.delete_file", AsyncMock()),
        ):
            status = await self._run(service, bucket="explicit-bucket")

        self.assertEqual(status, TaskStatus.READY)
        resource_manager.get_user_bucket.assert_not_awaited()

    async def test_last_error_is_recorded_on_failure(self):
        webhook = AsyncMock()
        watchtower = AsyncMock()
        failure = FileNotFoundInStorage("нет файла")
        watchtower.get_sharelink.side_effect = failure
        service = self._service(webhook, watchtower=watchtower)

        with patch("modules.translator.v2.service.delete_file", AsyncMock()):
            status = await self._run(service, source=FakeSource())

        self.assertEqual(status, TaskStatus.ERROR)
        self.assertIs(service.last_error, failure)
        self.assertEqual(service.last_stage, "upload original file")

    async def test_source_release_is_called_in_finally(self):
        webhook = AsyncMock()
        service = self._service(webhook)
        source = FakeSource()
        source.error = FileNotFoundInStorage("нет файла")

        with patch("modules.translator.v2.service.delete_file", AsyncMock()):
            status = await self._run(service, source=source)

        self.assertEqual(status, TaskStatus.ERROR)
        self.assertEqual(source.released, 1)

    async def test_error_message_prefers_exception_over_stage(self):
        webhook = AsyncMock()
        service = self._service(webhook)
        source = FakeSource()
        source.error = FileNotFoundInStorage("нет файла")

        with patch("modules.translator.v2.service.delete_file", AsyncMock()):
            await self._run(service, source=source)

        published = webhook.update_response_data.await_args_list[-1].args[1]
        self.assertEqual(published["error"], "Файл не найден в хранилище")

    async def test_parse_timeout_publishes_error_status(self):
        webhook = AsyncMock()
        service = self._service(webhook)

        async def slow_parse(*args, **kwargs):
            await asyncio.sleep(1)

        with (
            patch("modules.translator.v2.service.settings.PARSE_TIMEOUT_SECS", 0.01),
            patch("modules.translator.v2.service.run_in_process", slow_parse),
            patch("modules.translator.v2.service.delete_file", AsyncMock()),
        ):
            status = await self._run(service)

        self.assertEqual(status, TaskStatus.ERROR)
        published = webhook.update_response_data.await_args_list[-1].args[1]
        self.assertEqual(published["text_status"], "Превышено время обработки")


if __name__ == "__main__":
    unittest.main()
