import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

from modules.watchtower.exceptions import (
    FileNotFoundInStorage,
    FileTooLargeError,
    WatchtowerUnavailable,
)
from modules.watchtower.service import WatchtowerService
from modules.resource_manager.service import ResourceManagerService
from modules.webhook_manager.schemas import TaskStatus
from modules.webhook_manager.service import WebhookManagerService


class FakeResponse:
    def __init__(self, status=200, text="", json_data=None, headers=None):
        self.status = status
        self._text = text
        self._json_data = json_data or {"message": "Done", "status": status}
        self.headers = headers or {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def text(self):
        return self._text

    async def json(self):
        return self._json_data


class FakeContent:
    def __init__(self, chunks):
        self._chunks = list(chunks)
        self.iter_chunked_calls = []

    async def iter_chunked(self, size):
        self.iter_chunked_calls.append(size)
        for chunk in self._chunks:
            yield chunk


class FakeStreamResponse(FakeResponse):
    """Ответ с телом, доступным только потоково."""

    def __init__(self, status=200, chunks=(), headers=None):
        super().__init__(status=status, headers=headers)
        self.content = FakeContent(chunks)
        self.read_called = False

    async def read(self):
        self.read_called = True
        raise AssertionError("download_file обязан читать тело потоком, не read()")


class FakeSession:
    """Фейковая сессия. Принимает один ответ или список ответов по порядку."""

    def __init__(self, response):
        if isinstance(response, (list, tuple)):
            self._responses = list(response)
        else:
            self._responses = [response]
        self.requests = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    @property
    def response(self):
        return self._responses[0]

    def _next_response(self):
        if len(self._responses) > 1:
            return self._responses.pop(0)
        return self._responses[0]

    def _record(self, method, url, kwargs):
        self.requests.append((method, url, kwargs))
        return self._next_response()

    def post(self, url, **kwargs):
        return self._record("post", url, kwargs)

    def put(self, url, **kwargs):
        return self._record("put", url, kwargs)

    def patch(self, url, **kwargs):
        return self._record("patch", url, kwargs)

    def get(self, url, **kwargs):
        return self._record("get", url, kwargs)


def task_payload(status: str = "PROCESSING", progress: float = 42.0) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "task_id": "task-1",
        "user_id": "user-1",
        "service": "document-parser",
        "progress": {"progress": progress, "status": status},
        "created_at": now,
        "updated_at": now,
        "response_data": "{}",
    }


class ResourceManagerServiceTest(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def resource(
        resource_id: str,
        *,
        resource_type: str = "Document",
        resource_owner: str = "User",
        name: str = "Личные",
    ) -> dict:
        return {
            "id": resource_id,
            "name": name,
            "created_at": "2026-03-27T08:39:02.968662",
            "is_public": False,
            "external_id": None,
            "resource_type": resource_type,
            "resource_owner": resource_owner,
        }

    async def test_selects_personal_document_resource_id_as_bucket(self):
        personal_id = "c071ab1c-81c1-478e-acf9-4a4ad105b74d"
        session = FakeSession(FakeResponse(json_data=[
            self.resource(
                "4a0e00f7-d153-4eec-a7c9-5a5ee6c231a9",
                resource_type="News",
                resource_owner="Organization",
                name="Общие новости",
            ),
            self.resource(personal_id),
            self.resource(
                "dd085e1b-8443-4827-8925-93e22a871288",
                resource_owner="Organization",
                name="6 управление",
            ),
        ]))

        bucket = await ResourceManagerService(
            "http://resource-manager",
            session=session,
        ).get_user_bucket("user-1")

        self.assertEqual(bucket, personal_id)
        method, url, kwargs = session.requests[0]
        self.assertEqual(method, "get")
        self.assertEqual(url, "http://resource-manager/api/v1/resource/")
        self.assertEqual(kwargs["headers"], {"x-user-id": "user-1"})
        self.assertEqual(kwargs["params"], {"resource_kind": "Document"})

    async def test_returns_none_without_personal_document_resource(self):
        session = FakeSession(FakeResponse(json_data=[
            self.resource(
                "organization-resource",
                resource_owner="Organization",
            ),
        ]))

        bucket = await ResourceManagerService(
            "http://resource-manager",
            session=session,
        ).get_user_bucket("user-1")

        self.assertIsNone(bucket)

    async def test_rejects_multiple_personal_document_resources(self):
        session = FakeSession(FakeResponse(json_data=[
            self.resource("personal-1"),
            self.resource("personal-2"),
        ]))

        with self.assertRaisesRegex(ValueError, "personal-1.*personal-2"):
            await ResourceManagerService(
                "http://resource-manager",
                session=session,
            ).get_user_bucket("user-1")


class WebhookManagerServiceTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # Тесты не должны зависеть от содержимого .env.dev.
        service_name = patch(
            "modules.webhook_manager.service.settings.SERVICE_NAME",
            "document-parser",
        )
        service_name.start()
        self.addCleanup(service_name.stop)

    async def test_create_task_uses_v2_endpoint_without_key_in_body(self):
        session = FakeSession(FakeResponse(status=200))

        with patch("modules.webhook_manager.service.aiohttp.ClientSession", return_value=session):
            key = await WebhookManagerService("http://webhook").create_task(
                user_id="user-1",
                task_id="384f4d80-4ed6-4032-8569-f02fd5e1afb9",
                response_data={"text_status": "accepted"},
            )

        method, url, kwargs = session.requests[0]

        self.assertEqual(method, "post")
        self.assertEqual(url, "http://webhook/api/v2/storage/task")
        self.assertEqual(key, "user-1:document-parser:384f4d80-4ed6-4032-8569-f02fd5e1afb9")
        self.assertEqual(set(kwargs["json"].keys()), {"task"})
        self.assertNotIn("key", kwargs["json"])
        self.assertEqual(kwargs["json"]["task"]["user_id"], "user-1")

    async def test_update_progress_uses_documented_v1_endpoint(self):
        session = FakeSession(FakeResponse(status=200))

        with patch("modules.webhook_manager.service.aiohttp.ClientSession", return_value=session):
            await WebhookManagerService("http://webhook").update_progress(
                key="user-1:document-parser:task-1",
                progress=25,
                status=TaskStatus.PROCESSING,
            )

        method, url, kwargs = session.requests[0]

        self.assertEqual(method, "patch")
        self.assertEqual(url, "http://webhook/api/v1/storage/update_progress")
        self.assertEqual(kwargs["json"]["key"], "user-1:document-parser:task-1")

    async def test_update_response_data_uses_documented_v1_endpoint(self):
        session = FakeSession(FakeResponse(status=200))

        with patch("modules.webhook_manager.service.aiohttp.ClientSession", return_value=session):
            await WebhookManagerService("http://webhook").update_response_data(
                key="user-1:document-parser:task-1",
                response_data={"text_status": "done"},
            )

        method, url, kwargs = session.requests[0]

        self.assertEqual(method, "patch")
        self.assertEqual(url, "http://webhook/api/v1/storage/update_response_data")
        self.assertEqual(kwargs["json"]["key"], "user-1:document-parser:task-1")

    async def test_get_task_returns_task_for_existing_key(self):
        session = FakeSession(FakeResponse(status=200, json_data=task_payload()))

        task = await WebhookManagerService("http://webhook", session=session).get_task(
            "user-1:document-parser:task-1"
        )

        method, url, kwargs = session.requests[0]
        self.assertEqual(method, "get")
        self.assertEqual(url, "http://webhook/api/v1/storage/task")
        self.assertEqual(kwargs["params"], {"key": "user-1:document-parser:task-1"})
        self.assertIsNotNone(task)
        self.assertEqual(task.progress.status, TaskStatus.PROCESSING)

    async def test_get_task_returns_none_for_unknown_key(self):
        session = FakeSession(FakeResponse(status=404))

        task = await WebhookManagerService("http://webhook", session=session).get_task(
            "user-1:document-parser:unknown"
        )

        self.assertIsNone(task)

    async def test_get_task_does_not_retry_on_server_error(self):
        session = FakeSession(FakeResponse(status=500, text="boom"))

        with patch("modules.webhook_manager.service.asyncio.sleep", AsyncMock()):
            with self.assertRaises(Exception):
                await WebhookManagerService(
                    "http://webhook", session=session
                ).get_task("user-1:document-parser:task-1")

        self.assertEqual(len(session.requests), 1)

    async def test_get_task_accepts_task_wrapped_in_envelope(self):
        session = FakeSession(
            FakeResponse(status=200, json_data={"task": task_payload("CANCELLED")})
        )

        task = await WebhookManagerService("http://webhook", session=session).get_task(
            "user-1:document-parser:task-1"
        )

        self.assertIsNotNone(task)
        self.assertEqual(task.progress.status, TaskStatus.CANCELLED)

    async def test_update_progress_retries_transient_failure(self):
        session = FakeSession([
            FakeResponse(status=500, text="upstream down"),
            FakeResponse(status=200),
        ])

        with patch("modules.webhook_manager.service.asyncio.sleep", AsyncMock()) as sleep:
            await WebhookManagerService("http://webhook", session=session).update_progress(
                key="user-1:document-parser:task-1",
                progress=25,
                status=TaskStatus.PROCESSING,
            )

        self.assertEqual(len(session.requests), 2)
        sleep.assert_awaited_once()

    async def test_update_progress_raises_after_retries_exhausted(self):
        from modules.webhook_manager.service import _RETRY_ATTEMPTS

        session = FakeSession(FakeResponse(status=503, text="down"))

        with patch("modules.webhook_manager.service.asyncio.sleep", AsyncMock()):
            with self.assertRaises(Exception):
                await WebhookManagerService(
                    "http://webhook", session=session
                ).update_progress(
                    key="user-1:document-parser:task-1",
                    progress=25,
                    status=TaskStatus.PROCESSING,
                )

        self.assertEqual(len(session.requests), _RETRY_ATTEMPTS)

    async def test_update_progress_does_not_retry_client_error(self):
        session = FakeSession(FakeResponse(status=400, text="bad key"))

        with patch("modules.webhook_manager.service.asyncio.sleep", AsyncMock()):
            with self.assertRaises(Exception):
                await WebhookManagerService(
                    "http://webhook", session=session
                ).update_progress(
                    key="user-1:document-parser:task-1",
                    progress=25,
                    status=TaskStatus.PROCESSING,
                )

        self.assertEqual(len(session.requests), 1)


class WatchtowerServiceTest(unittest.IsolatedAsyncioTestCase):
    async def test_upload_file_to_bucket_root_omits_prefix(self):
        session = FakeSession(FakeResponse(status=200))

        with tempfile.NamedTemporaryFile() as tmp:
            tmp.write(b"content")
            tmp.flush()

            object_key = await WatchtowerService(
                "http://watchtower",
                session=session,
            ).upload_file(
                bucket="personal-resource-id",
                local_path=tmp.name,
                filename="Отчет по работе.docx",
            )

        method, url, kwargs = session.requests[0]
        form_fields = kwargs["data"]._fields

        self.assertEqual(method, "put")
        self.assertEqual(
            url,
            "http://watchtower/api/v1/cloud/personal-resource-id/file/upload",
        )
        self.assertFalse(
            any(field[0].get("name") == "prefix" for field in form_fields)
        )
        self.assertEqual(
            object_key,
            "Отчет по работе.docx",
        )
        multipart = kwargs["data"]()
        disposition = multipart._parts[0][0].headers["Content-Disposition"]
        self.assertIn('filename="Отчет по работе.docx"', disposition)
        self.assertNotIn("%D0", disposition)

    async def test_upload_file_keeps_unicode_name_in_multipart_and_object_key(self):
        session = FakeSession(FakeResponse(status=200))

        with tempfile.NamedTemporaryFile() as tmp:
            tmp.write(b"content")
            tmp.flush()

            with patch("modules.watchtower.service.aiohttp.ClientSession", return_value=session):
                object_key = await WatchtowerService("http://watchtower").upload_file(
                    bucket="bucket-1",
                    local_path=tmp.name,
                    filename="Отчет 1.docx",
                    prefix="user-1/translator",
                )

        method, url, kwargs = session.requests[0]
        form_fields = kwargs["data"]._fields

        self.assertEqual(method, "put")
        self.assertEqual(url, "http://watchtower/api/v1/cloud/bucket-1/file/upload")
        self.assertEqual(object_key, "user-1/translator/Отчет 1.docx")
        self.assertTrue(
            any(field[0].get("name") == "prefix" and field[2] == "user-1/translator" for field in form_fields)
        )
        self.assertTrue(
            any(
                field[0].get("name") == "files"
                and field[0].get("filename") == "Отчет 1.docx"
                for field in form_fields
            )
        )

    async def test_download_file_streams_and_preserves_extension(self):
        response = FakeStreamResponse(chunks=[b"abc", b"defg"])
        session = FakeSession(response)

        with tempfile.TemporaryDirectory() as dest_dir:
            path = await WatchtowerService(
                "http://watchtower",
                session=session,
            ).download_file(
                bucket="bucket-1",
                file_path="folder/Отчет.docx",
                dest_dir=dest_dir,
            )

            method, url, kwargs = session.requests[0]
            self.assertEqual(method, "post")
            self.assertEqual(
                url,
                "http://watchtower/api/v1/cloud/bucket-1/file/download",
            )
            self.assertEqual(kwargs["json"], {"file_name": "folder/Отчет.docx"})
            self.assertEqual(Path(path).name, "Отчет.docx")
            self.assertEqual(Path(path).suffix, ".docx")
            self.assertEqual(Path(path).read_bytes(), b"abcdefg")

        self.assertEqual(response.content.iter_chunked_calls, [1024 * 1024])
        self.assertFalse(response.read_called)

    async def test_download_file_rejects_by_content_length(self):
        response = FakeStreamResponse(
            chunks=[b"x" * 10],
            headers={"Content-Length": str(5 * 1024 * 1024)},
        )
        session = FakeSession(response)

        with tempfile.TemporaryDirectory() as dest_dir:
            with self.assertRaises(FileTooLargeError):
                await WatchtowerService(
                    "http://watchtower",
                    session=session,
                ).download_file(
                    bucket="bucket-1",
                    file_path="big.docx",
                    dest_dir=dest_dir,
                    max_size_mb=1,
                )

            self.assertEqual(list(Path(dest_dir).iterdir()), [])

        self.assertEqual(response.content.iter_chunked_calls, [])
        self.assertFalse(response.read_called)

    async def test_download_file_aborts_when_stream_exceeds_limit(self):
        one_mb = b"y" * (1024 * 1024)
        response = FakeStreamResponse(chunks=[one_mb, one_mb, one_mb])
        session = FakeSession(response)

        with tempfile.TemporaryDirectory() as dest_dir:
            with self.assertRaises(FileTooLargeError):
                await WatchtowerService(
                    "http://watchtower",
                    session=session,
                ).download_file(
                    bucket="bucket-1",
                    file_path="big.docx",
                    dest_dir=dest_dir,
                    max_size_mb=1,
                )

            self.assertEqual(list(Path(dest_dir).iterdir()), [])

    async def test_download_file_raises_file_not_found_on_404(self):
        session = FakeSession(FakeStreamResponse(status=404))

        with tempfile.TemporaryDirectory() as dest_dir:
            with self.assertRaises(FileNotFoundInStorage):
                await WatchtowerService(
                    "http://watchtower",
                    session=session,
                ).download_file(
                    bucket="bucket-1",
                    file_path="missing.docx",
                    dest_dir=dest_dir,
                )

    async def test_download_file_raises_unavailable_on_5xx(self):
        session = FakeSession(FakeStreamResponse(status=502))

        with tempfile.TemporaryDirectory() as dest_dir:
            with self.assertRaises(WatchtowerUnavailable):
                await WatchtowerService(
                    "http://watchtower",
                    session=session,
                ).download_file(
                    bucket="bucket-1",
                    file_path="any.docx",
                    dest_dir=dest_dir,
                )


if __name__ == "__main__":
    unittest.main()
