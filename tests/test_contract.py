"""Контракт задач: ручки, содержимое и защита от расхождения с кодом."""

import unittest

from fastapi.testclient import TestClient

from modules.broker.dispatcher import build_default_dispatcher
from modules.broker.schemas import TaskEnvelope, TranslatePayload, parse_envelope
from modules.contract.markdown import render_markdown
from modules.contract.service import build_contract
from modules.parser.v1.utils import is_supported_extension


class ContractContentTest(unittest.TestCase):
    def setUp(self):
        self.contract = build_contract(version="test")

    def test_task_types_come_from_the_dispatcher(self):
        """Новый обработчик обязан попадать в контракт сам.

        Список типов задач берётся из реестра, а не переписывается руками —
        иначе документация начнёт врать на первом же новом task_type.
        """
        registered = set(build_default_dispatcher().task_types)
        described = {task["task_type"] for task in self.contract["task_types"]}
        self.assertEqual(registered, described)

    def test_payload_schema_matches_the_model(self):
        translate = next(
            task
            for task in self.contract["task_types"]
            if task["task_type"] == "document-parser.translate"
        )
        self.assertEqual(
            translate["payload_schema"], TranslatePayload.model_json_schema()
        )

    def test_every_payload_field_is_documented(self):
        """Поле без описания — дырка в контракте, а не мелочь."""
        for task in self.contract["task_types"]:
            for name, prop in task["payload_schema"]["properties"].items():
                self.assertTrue(
                    prop.get("description"),
                    f"{task['task_type']}.{name} без описания",
                )
        for name, prop in self.contract["envelope"]["schema"]["properties"].items():
            self.assertTrue(prop.get("description"), f"envelope.{name} без описания")

    def test_examples_are_valid_messages(self):
        """Пример из документации обязан проходить ту же валидацию."""
        for task in self.contract["task_types"]:
            message = {
                **self.contract["envelope"]["example"],
                "task_type": task["task_type"],
                "payload": task["payload_example"],
            }
            envelope = parse_envelope(message)
            self.assertIsInstance(envelope, TaskEnvelope)
            payload = TranslatePayload.model_validate(envelope.payload)
            # И файл в примере должен быть таким, который сервис возьмёт в
            # работу: пример с неподдерживаемым расширением бесполезен.
            self.assertTrue(is_supported_extension(payload.file_path))

    def test_submission_describes_the_gateway(self):
        submission = self.contract["submission"]
        self.assertEqual(submission["via"], "task_gateway")
        self.assertEqual(submission["endpoint"], "POST /api/v1/broker/publish")
        self.assertIn("x-user-id", submission["headers"])
        # task_id генерирует гейтвей: просить его у клиента — ошибка контракта.
        self.assertNotIn("task_id", submission["request_fields"])

    def test_topology_and_limits_come_from_settings(self):
        from settings import settings

        transport = self.contract["transport"]
        self.assertEqual(transport["exchange"], settings.RMQ_EXCHANGE)
        self.assertEqual(transport["queue"], settings.RMQ_QUEUE)
        self.assertEqual(
            self.contract["limits"]["task_timeout_secs"], settings.TASK_TIMEOUT_SECS
        )
        self.assertEqual(
            self.contract["failure_semantics"]["max_retries"], settings.RMQ_MAX_RETRIES
        )


class ContractMarkdownTest(unittest.TestCase):
    def setUp(self):
        self.contract = build_contract(version="test")
        self.document = render_markdown(self.contract)

    def test_every_task_type_has_a_section(self):
        for task in self.contract["task_types"]:
            self.assertIn(f"Задача `{task['task_type']}`", self.document)

    def test_every_payload_field_is_in_the_table(self):
        for task in self.contract["task_types"]:
            for name in task["payload_schema"]["properties"]:
                self.assertIn(f"| `{name}` |", self.document)

    def test_required_and_optional_are_distinguishable(self):
        self.assertIn("**обязательное**", self.document)
        self.assertIn('`"auto"`', self.document)

    def test_operational_sections_are_present(self):
        for heading in (
            "Что должно быть готово до постановки задачи",
            "Как поставить задачу",
            "Как следить за выполнением",
            "Отмена",
            "Ошибки и повторы",
            "Ограничения",
            "Чек-лист",
            "Что приезжает консьюмеру",
        ):
            self.assertIn(heading, self.document)

    def test_client_path_goes_through_the_gateway(self):
        """Клиент в очередь не публикует — документ обязан вести к гейтвею."""
        self.assertIn("task_gateway", self.document)
        self.assertIn("/api/v1/broker/publish", self.document)
        self.assertIn("/api/v1/tasks/cancel", self.document)

    def test_removed_http_endpoint_is_not_advertised(self):
        self.assertNotIn("/api/v2/parser", self.document)


class ContractEndpointTest(unittest.TestCase):
    def setUp(self):
        import main

        self.client = TestClient(main.app)

    def test_json_endpoint(self):
        with self.client as client:
            response = client.get("/api/v1/contract")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["service"]["name"], build_contract()["service"]["name"])
        self.assertIn("envelope", body)

    def test_markdown_endpoint(self):
        with self.client as client:
            response = client.get("/api/v1/contract.md")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers["content-type"].startswith("text/markdown"))
        self.assertIn("# Контракт задач", response.text)


if __name__ == "__main__":
    unittest.main()
