import tempfile
import unittest
from unittest.mock import patch

from modules.watchtower.service import WatchtowerService
from modules.resource_manager.service import ResourceManagerService
from modules.webhook_manager.schemas import TaskStatus
from modules.webhook_manager.service import WebhookManagerService


class FakeResponse:
    def __init__(self, status=200, text="", json_data=None):
        self.status = status
        self._text = text
        self._json_data = json_data or {"message": "Done", "status": status}

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def text(self):
        return self._text

    async def json(self):
        return self._json_data


class FakeSession:
    def __init__(self, response):
        self.response = response
        self.requests = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def post(self, url, **kwargs):
        self.requests.append(("post", url, kwargs))
        return self.response

    def put(self, url, **kwargs):
        self.requests.append(("put", url, kwargs))
        return self.response

    def patch(self, url, **kwargs):
        self.requests.append(("patch", url, kwargs))
        return self.response

    def get(self, url, **kwargs):
        self.requests.append(("get", url, kwargs))
        return self.response


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
        self.assertEqual(key, "user-1:document_parser:384f4d80-4ed6-4032-8569-f02fd5e1afb9")
        self.assertEqual(set(kwargs["json"].keys()), {"task"})
        self.assertNotIn("key", kwargs["json"])
        self.assertEqual(kwargs["json"]["task"]["user_id"], "user-1")

    async def test_update_progress_uses_documented_v1_endpoint(self):
        session = FakeSession(FakeResponse(status=200))

        with patch("modules.webhook_manager.service.aiohttp.ClientSession", return_value=session):
            await WebhookManagerService("http://webhook").update_progress(
                key="user-1:document_parser:task-1",
                progress=25,
                status=TaskStatus.PROCESSING,
            )

        method, url, kwargs = session.requests[0]

        self.assertEqual(method, "patch")
        self.assertEqual(url, "http://webhook/api/v1/storage/update_progress")
        self.assertEqual(kwargs["json"]["key"], "user-1:document_parser:task-1")

    async def test_update_response_data_uses_documented_v1_endpoint(self):
        session = FakeSession(FakeResponse(status=200))

        with patch("modules.webhook_manager.service.aiohttp.ClientSession", return_value=session):
            await WebhookManagerService("http://webhook").update_response_data(
                key="user-1:document_parser:task-1",
                response_data={"text_status": "done"},
            )

        method, url, kwargs = session.requests[0]

        self.assertEqual(method, "patch")
        self.assertEqual(url, "http://webhook/api/v1/storage/update_response_data")
        self.assertEqual(kwargs["json"]["key"], "user-1:document_parser:task-1")


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

    async def test_get_sharelink_returns_relative_gateway_path(self):
        session = FakeSession(
            FakeResponse(
                status=200,
                json_data={
                    "message": "http://internal/user-1/translator/%D0%9E%D1%82%D1%87%D0%B5%D1%82.docx?token=abc",
                    "status": 200,
                },
            )
        )

        with patch("modules.watchtower.service.settings.WATCHTOWER_SHARED_PREFIX", "/api/gateway"):
            with patch("modules.watchtower.service.aiohttp.ClientSession", return_value=session):
                url = await WatchtowerService("http://watchtower").get_sharelink(
                    "bucket-1",
                    "user-1/translator/Отчет.docx",
                )

        self.assertEqual(
            url,
            "/api/gateway/user-1/translator/%D0%9E%D1%82%D1%87%D0%B5%D1%82.docx?token=abc",
        )
        method, request_url, kwargs = session.requests[0]
        self.assertEqual(method, "post")
        self.assertEqual(
            request_url,
            "http://watchtower/api/v1/cloud/bucket-1/file/share",
        )
        self.assertEqual(
            kwargs["json"]["file_path"],
            "user-1/translator/Отчет.docx",
        )


if __name__ == "__main__":
    unittest.main()
