import asyncio
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

from docling_core.types.doc import DocItemLabel, TextItem

from modules.parser.v1.schemas import ParserParams
from modules.translator.v2.service import TranslatorV2Service
from modules.translator.v2.schemas import TranslatorResponseData
from modules.webhook_manager.cancellation import (
    CancellationTokenABC,
    NullCancellationToken,
    TaskCancelled,
    WebhookCancellationToken,
    create_cancellation_token,
)
from modules.webhook_manager.schemas import Task, TaskProgress, TaskStatus


def make_task(status: TaskStatus = TaskStatus.PROCESSING) -> Task:
    now = datetime.now(timezone.utc)
    return Task(
        task_id="task-1",
        user_id="user-1",
        service="document-parser",
        progress=TaskProgress(progress=42, status=status),
        created_at=now,
        updated_at=now,
        response_data="{}",
    )


class ScriptedToken(CancellationTokenABC):
    """Токен, отменяющий задачу на заданной по счёту проверке."""

    def __init__(self, cancel_on_call: int, task_key: str = "task-key"):
        self.task_key = task_key
        self._cancel_on_call = cancel_on_call
        self.calls = 0
        self.stages: list[str] = []

    async def is_cancelled(self) -> bool:
        self.calls += 1
        return self.calls >= self._cancel_on_call

    async def raise_if_cancelled(self, stage: str = "") -> None:
        self.stages.append(stage)
        await super().raise_if_cancelled(stage)


class FakeDoclingDocument:
    def __init__(self, items):
        self.items = items

    def iterate_items(self):
        for item in self.items:
            yield item, 0


class CountingTranslator:
    source_language = "en"
    target_language = "ru"

    def __init__(self):
        self.calls = 0

    async def translate_element_limited(self, text: str) -> str:
        self.calls += 1
        return f"translated {text}"


class WebhookCancellationTokenTest(unittest.IsolatedAsyncioTestCase):
    async def test_token_returns_true_on_cancelled_status(self):
        webhook = AsyncMock()
        webhook.get_task.return_value = make_task(TaskStatus.CANCELLED)
        token = WebhookCancellationToken(webhook, "task-key", ttl_secs=60)

        self.assertTrue(await token.is_cancelled())
        with self.assertRaises(TaskCancelled):
            await token.raise_if_cancelled("translate document")

    async def test_token_caches_negative_answer_within_ttl(self):
        webhook = AsyncMock()
        webhook.get_task.return_value = make_task(TaskStatus.PROCESSING)
        token = WebhookCancellationToken(webhook, "task-key", ttl_secs=60)

        results = [await token.is_cancelled() for _ in range(5)]

        self.assertEqual(results, [False] * 5)
        self.assertEqual(webhook.get_task.await_count, 1)

    async def test_token_refreshes_after_ttl(self):
        webhook = AsyncMock()
        webhook.get_task.return_value = make_task(TaskStatus.PROCESSING)
        clock = {"now": 1000.0}

        with patch(
            "modules.webhook_manager.cancellation.time.monotonic",
            side_effect=lambda: clock["now"],
        ):
            token = WebhookCancellationToken(webhook, "task-key", ttl_secs=5)
            self.assertFalse(await token.is_cancelled())
            self.assertFalse(await token.is_cancelled())
            self.assertEqual(webhook.get_task.await_count, 1)

            clock["now"] += 10
            self.assertFalse(await token.is_cancelled())

        self.assertEqual(webhook.get_task.await_count, 2)

    async def test_token_is_sticky_after_cancellation(self):
        webhook = AsyncMock()
        webhook.get_task.return_value = make_task(TaskStatus.CANCELLED)
        token = WebhookCancellationToken(webhook, "task-key", ttl_secs=0)

        self.assertTrue(await token.is_cancelled())
        self.assertTrue(await token.is_cancelled())
        self.assertTrue(await token.is_cancelled())

        self.assertEqual(webhook.get_task.await_count, 1)

    async def test_token_treats_request_error_as_not_cancelled(self):
        webhook = AsyncMock()
        webhook.get_task.side_effect = Exception("webhook_manager недоступен")
        token = WebhookCancellationToken(webhook, "task-key", ttl_secs=60)

        self.assertFalse(await token.is_cancelled())
        await token.raise_if_cancelled("translate document")

    async def test_concurrent_checks_make_single_request(self):
        webhook = AsyncMock()

        async def slow_get_task(key):
            await asyncio.sleep(0)
            return make_task(TaskStatus.PROCESSING)

        webhook.get_task.side_effect = slow_get_task
        token = WebhookCancellationToken(webhook, "task-key", ttl_secs=60)

        results = await asyncio.gather(*(token.is_cancelled() for _ in range(10)))

        self.assertEqual(results, [False] * 10)
        self.assertEqual(webhook.get_task.await_count, 1)

    async def test_null_token_never_touches_webhook(self):
        webhook = AsyncMock()
        token = create_cancellation_token(webhook, "")

        self.assertIsInstance(token, NullCancellationToken)
        self.assertFalse(await token.is_cancelled())
        await token.raise_if_cancelled("translate document")
        webhook.get_task.assert_not_awaited()


class TranslationCancellationTest(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _service(webhook, watchtower, resource_manager):
        return TranslatorV2Service(
            webhook=webhook,
            watchtower=watchtower,
            resource_manager=resource_manager,
        )

    async def test_cancelled_before_start_skips_pipeline(self):
        webhook = AsyncMock()
        resource_manager = AsyncMock()
        watchtower = AsyncMock()
        service = self._service(webhook, watchtower, resource_manager)

        with (
            patch(
                "modules.translator.v2.service.run_in_process",
                AsyncMock(),
            ) as run_in_process,
            patch("modules.translator.v2.service.delete_file", AsyncMock()),
        ):
            status = await service.run_translation_task(
                user_id="user-1",
                task_id="task-1",
                task_key="task-key",
                file_path="/tmp/source.docx",
                original_filename="Отчет.docx",
                source_language="ru",
                target_language="en",
                parser_params=ParserParams(file_path=Path("/tmp/source.docx")),
                executor=object(),
                cancellation=ScriptedToken(cancel_on_call=1),
            )

        self.assertEqual(status, TaskStatus.CANCELLED)
        resource_manager.get_user_bucket.assert_not_awaited()
        run_in_process.assert_not_awaited()

    async def test_cancellation_publishes_cancelled_without_error_and_keeps_progress(self):
        webhook = AsyncMock()
        resource_manager = AsyncMock()
        resource_manager.get_user_bucket.return_value = "bucket-1"
        watchtower = AsyncMock()
        watchtower.upload_file.return_value = "Отчет.docx"
        watchtower.get_sharelink.return_value = "original-link"
        service = self._service(webhook, watchtower, resource_manager)

        with (
            patch("modules.translator.v2.service.run_in_process", AsyncMock()),
            patch("modules.translator.v2.service.delete_file", AsyncMock()),
        ):
            status = await service.run_translation_task(
                user_id="user-1",
                task_id="task-1",
                task_key="task-key",
                file_path="/tmp/source.docx",
                original_filename="Отчет.docx",
                source_language="ru",
                target_language="en",
                parser_params=ParserParams(file_path=Path("/tmp/source.docx")),
                executor=object(),
                # 3-я проверка — сразу после публикации прогресса 10.
                cancellation=ScriptedToken(cancel_on_call=3),
            )

        self.assertEqual(status, TaskStatus.CANCELLED)
        last_progress_call = webhook.update_progress.await_args_list[-1]
        self.assertEqual(last_progress_call.args[1], 10)
        self.assertEqual(last_progress_call.args[2], TaskStatus.CANCELLED)
        self.assertGreater(last_progress_call.args[1], 0)

        last_data_call = webhook.update_response_data.await_args_list[-1]
        published = last_data_call.args[1]
        self.assertIsNone(published["error"])
        self.assertEqual(published["text_status"], "Задача отменена")

    async def test_cancellation_removes_temp_files(self):
        webhook = AsyncMock()
        resource_manager = AsyncMock()
        resource_manager.get_user_bucket.return_value = "bucket-1"
        watchtower = AsyncMock()
        service = self._service(webhook, watchtower, resource_manager)

        with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as tmp:
            tmp.write(b"source")
            source_path = tmp.name

        with patch("modules.translator.v2.service.run_in_process", AsyncMock()):
            status = await service.run_translation_task(
                user_id="user-1",
                task_id="task-1",
                task_key="task-key",
                file_path=source_path,
                original_filename="Отчет.docx",
                source_language="ru",
                target_language="en",
                parser_params=ParserParams(file_path=Path(source_path)),
                executor=object(),
                cancellation=ScriptedToken(cancel_on_call=2),
            )

        self.assertEqual(status, TaskStatus.CANCELLED)
        self.assertFalse(Path(source_path).exists())

    async def test_cancellation_between_batches_stops_translation(self):
        elements = [
            TextItem(
                self_ref=f"#/texts/{index}",
                label=DocItemLabel.TEXT,
                orig=f"block {index}",
                text=f"block {index}",
            )
            for index in range(6)
        ]
        translator = CountingTranslator()
        service = self._service(AsyncMock(), AsyncMock(), AsyncMock())
        response_data = TranslatorResponseData(
            original_language="en",
            target_language="ru",
        )

        with (
            patch(
                "modules.translator.v2.service.settings.TRANSALTOR_MAX_CONCURRENCY",
                2,
            ),
            patch.object(
                service,
                "_export_to_word",
                AsyncMock(return_value="/tmp/result.docx"),
            ),
        ):
            with self.assertRaises(TaskCancelled):
                await service._translate_with_progress(
                    translator,
                    FakeDoclingDocument(elements),
                    "task-key",
                    response_data,
                    ScriptedToken(cancel_on_call=2),
                )

        self.assertEqual(translator.calls, 2)

    async def test_cancellation_mid_translation_keeps_reached_progress(self):
        """Регрессия: отмена посреди перевода не должна откатывать шкалу на 15%."""
        elements = [
            TextItem(
                self_ref=f"#/texts/{index}",
                label=DocItemLabel.TEXT,
                orig=f"block {index}",
                text=f"block {index}",
            )
            for index in range(40)
        ]
        webhook = AsyncMock()
        service = self._service(webhook, AsyncMock(), AsyncMock())
        response_data = TranslatorResponseData(
            original_language="en",
            target_language="ru",
        )

        with (
            patch(
                "modules.translator.v2.service.settings.TRANSALTOR_MAX_CONCURRENCY",
                4,
            ),
            patch.object(
                service,
                "_export_to_word",
                AsyncMock(return_value="/tmp/result.docx"),
            ),
        ):
            with self.assertRaises(TaskCancelled):
                await service._translate_with_progress(
                    CountingTranslator(),
                    FakeDoclingDocument(elements),
                    "task-key",
                    response_data,
                    ScriptedToken(cancel_on_call=3),
                )

        # Два батча по 4 элемента: 15 + 78 * (8 / 40) = 30.6
        self.assertAlmostEqual(service._last_progress, 30.6, places=3)
        published = [call.args[1] for call in webhook.update_progress.await_args_list]
        self.assertEqual(service._last_progress, max(published))


if __name__ == "__main__":
    unittest.main()
