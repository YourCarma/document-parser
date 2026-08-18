import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from modules.broker.exceptions import InvalidTaskPayload, TaskPipelineFailed, UnsupportedSourceFormat
from modules.broker.handlers.translate import TranslateHandler
from modules.broker.keys import build_task_key
from modules.broker.schemas import parse_envelope
from modules.resource_manager.exceptions import BucketNotFound
from modules.translator.v2.service import TranslatorV2Service
from modules.translator.v2.sources import WatchtowerSource
from modules.watchtower.exceptions import WatchtowerUnavailable
from modules.webhook_manager.cancellation import WebhookCancellationToken
from modules.webhook_manager.schemas import Task, TaskProgress, TaskStatus

from tests.test_rabbitmq_consumer import FakeRuntime


def envelope(**payload_overrides):
    payload = {"file_path": "documents/report.pdf"}
    payload.update(payload_overrides)
    return parse_envelope(
        {
            "task_id": "task-1",
            "user_id": "user-1",
            "task_type": "document-parser.translate",
            "payload": payload,
        }
    )


def task_with_status(status: TaskStatus) -> Task:
    now = datetime.now(timezone.utc)
    return Task(
        task_id="task-1",
        user_id="user-1",
        service="document-parser",
        progress=TaskProgress(progress=0, status=status),
        created_at=now,
        updated_at=now,
        response_data="{}",
    )


class TranslateHandlerTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.runtime = FakeRuntime()
        self.runtime.webhook.get_task.return_value = None
        self.runtime.resource_manager_client.get_user_bucket.return_value = "bucket-1"
        self.handler = TranslateHandler(self.runtime)

    def _patch_pipeline(self, status=TaskStatus.READY, last_error=None, last_stage=""):
        async def run(self_service, **kwargs):
            self_service.last_error = last_error
            self_service.last_stage = last_stage
            return status

        return patch.object(TranslatorV2Service, "run_translation_task", run)

    async def _handle(self, env=None, **kwargs):
        env = env or envelope()
        return await self.handler.handle(env, build_task_key(env))

    async def test_handler_runs_pipeline_with_downloaded_file(self):
        captured = {}

        async def run(self_service, **kwargs):
            captured.update(kwargs)
            return TaskStatus.READY

        with patch.object(TranslatorV2Service, "run_translation_task", run):
            outcome = await self._handle()

        self.assertEqual(outcome.status, TaskStatus.READY)
        self.assertIsInstance(captured["source"], WatchtowerSource)
        self.assertIsInstance(captured["cancellation"], WebhookCancellationToken)
        self.assertEqual(captured["bucket"], "bucket-1")
        self.assertEqual(captured["output_prefix"], "translated/task-1")
        self.assertEqual(captured["user_id"], "user-1")
        self.assertEqual(captured["task_id"], "task-1")

    async def test_handler_never_creates_task(self):
        with self._patch_pipeline():
            await self._handle()

        self.runtime.webhook.create_task.assert_not_awaited()

    async def test_handler_builds_key_from_task_type_prefix(self):
        captured = {}

        async def run(self_service, **kwargs):
            captured.update(kwargs)
            return TaskStatus.READY

        with patch.object(TranslatorV2Service, "run_translation_task", run):
            await self._handle()

        self.assertEqual(captured["task_key"], "user-1:document-parser:task-1")

    async def test_handler_skips_work_when_task_already_ready(self):
        self.runtime.webhook.get_task.return_value = task_with_status(TaskStatus.READY)

        with patch.object(
            TranslatorV2Service, "run_translation_task", AsyncMock()
        ) as pipeline:
            outcome = await self._handle()

        self.assertEqual(outcome.status, TaskStatus.READY)
        pipeline.assert_not_awaited()

    async def test_handler_skips_work_when_task_already_cancelled(self):
        self.runtime.webhook.get_task.return_value = task_with_status(
            TaskStatus.CANCELLED
        )

        with patch.object(
            TranslatorV2Service, "run_translation_task", AsyncMock()
        ) as pipeline:
            outcome = await self._handle()

        self.assertEqual(outcome.status, TaskStatus.CANCELLED)
        pipeline.assert_not_awaited()

    async def test_handler_continues_when_task_not_found(self):
        self.runtime.webhook.get_task.return_value = None

        with self._patch_pipeline():
            outcome = await self._handle()

        self.assertEqual(outcome.status, TaskStatus.READY)

    async def test_handler_rejects_unsupported_extension_before_download(self):
        with self.assertRaises(UnsupportedSourceFormat):
            await self._handle(envelope(file_path="report.zip"))

        self.runtime.watchtower_client.download_file.assert_not_awaited()

    async def test_handler_raises_bucket_not_found(self):
        self.runtime.resource_manager_client.get_user_bucket.return_value = None

        with self.assertRaises(BucketNotFound):
            await self._handle()

        self.runtime.watchtower_client.download_file.assert_not_awaited()

    async def test_handler_uses_explicit_bucket_from_payload(self):
        captured = {}

        async def run(self_service, **kwargs):
            captured.update(kwargs)
            return TaskStatus.READY

        with patch.object(TranslatorV2Service, "run_translation_task", run):
            await self._handle(envelope(bucket="explicit-bucket"))

        self.assertEqual(captured["bucket"], "explicit-bucket")
        self.runtime.resource_manager_client.get_user_bucket.assert_not_awaited()

    async def test_handler_uses_output_prefix_from_payload(self):
        captured = {}

        async def run(self_service, **kwargs):
            captured.update(kwargs)
            return TaskStatus.READY

        with patch.object(TranslatorV2Service, "run_translation_task", run):
            await self._handle(envelope(output_prefix="custom/place"))

        self.assertEqual(captured["output_prefix"], "custom/place")

    async def test_handler_raises_pipeline_failed_on_error_status(self):
        cause = WatchtowerUnavailable("хранилище недоступно")

        with self._patch_pipeline(
            status=TaskStatus.ERROR, last_error=cause, last_stage="парсинг документа"
        ):
            with self.assertRaises(TaskPipelineFailed) as ctx:
                await self._handle()

        self.assertIs(ctx.exception.cause, cause)
        self.assertEqual(ctx.exception.stage, "парсинг документа")

    async def test_handler_invalid_payload_raises_typed_error(self):
        env = parse_envelope(
            {
                "task_id": "task-1",
                "user_id": "user-1",
                "task_type": "document-parser.translate",
                "payload": {},
            }
        )

        with self.assertRaises(InvalidTaskPayload):
            await self.handler.handle(env, build_task_key(env))


if __name__ == "__main__":
    unittest.main()
