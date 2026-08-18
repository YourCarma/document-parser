import json
import unittest
from unittest.mock import patch

from pydantic import ValidationError

from modules.broker.exceptions import InvalidEnvelope
from modules.broker.keys import build_task_key
from modules.broker.schemas import (
    TaskEnvelope,
    TranslatePayload,
    parse_envelope,
    payload_user_id_differs,
)


GATEWAY_EXAMPLE = {
    "task_id": "5fb0b68c-2259-47d8-8e72-3dc517ac6d4d",
    "user_id": "1234",
    "task_type": "document-parser.translate",
    "payload": {
        "file_path": "documents/report.pdf",
        "source_language": "auto",
        "target_language": "ru",
    },
}


class TaskEnvelopeTest(unittest.TestCase):
    def test_envelope_parses_gateway_example(self):
        envelope = parse_envelope(json.dumps(GATEWAY_EXAMPLE).encode("utf-8"))

        self.assertEqual(envelope.task_id, "5fb0b68c-2259-47d8-8e72-3dc517ac6d4d")
        self.assertEqual(envelope.user_id, "1234")
        self.assertEqual(envelope.task_type, "document-parser.translate")
        self.assertEqual(envelope.payload["file_path"], "documents/report.pdf")

    def test_envelope_accepts_numeric_user_id(self):
        envelope = parse_envelope({**GATEWAY_EXAMPLE, "user_id": 21233})

        self.assertEqual(envelope.user_id, "21233")

    def test_envelope_prefers_top_level_user_id_over_payload(self):
        raw = {
            **GATEWAY_EXAMPLE,
            "user_id": "1234",
            "payload": {**GATEWAY_EXAMPLE["payload"], "user_id": 21233},
        }

        envelope = parse_envelope(raw)

        self.assertEqual(envelope.user_id, "1234")
        self.assertTrue(payload_user_id_differs(envelope))

    def test_envelope_falls_back_to_payload_user_id(self):
        raw = dict(GATEWAY_EXAMPLE)
        raw.pop("user_id")
        raw["payload"] = {**GATEWAY_EXAMPLE["payload"], "user_id": 21233}

        envelope = parse_envelope(raw)

        self.assertEqual(envelope.user_id, "21233")
        self.assertFalse(payload_user_id_differs(envelope))

    def test_envelope_rejects_non_json_body(self):
        with self.assertRaises(InvalidEnvelope):
            parse_envelope(b"not json")

    def test_envelope_rejects_missing_task_id(self):
        raw = dict(GATEWAY_EXAMPLE)
        raw.pop("task_id")

        with self.assertRaises(InvalidEnvelope):
            parse_envelope(raw)

    def test_envelope_rejects_empty_user_id(self):
        with self.assertRaises(InvalidEnvelope):
            parse_envelope({**GATEWAY_EXAMPLE, "user_id": "   "})

    def test_envelope_ignores_unknown_fields(self):
        envelope = parse_envelope({**GATEWAY_EXAMPLE, "published_at": "2026-08-13"})

        self.assertEqual(envelope.task_id, GATEWAY_EXAMPLE["task_id"])


class TranslatePayloadTest(unittest.TestCase):
    def test_translate_payload_defaults(self):
        payload = TranslatePayload.model_validate({"file_path": "documents/report.pdf"})

        self.assertEqual(payload.source_language, "auto")
        self.assertEqual(payload.target_language, "ru")
        self.assertIsNone(payload.output_prefix)
        self.assertFalse(payload.parse_images)
        self.assertFalse(payload.include_image_in_output)
        self.assertFalse(payload.full_vlm_pdf_parse)

    def test_translate_payload_strips_leading_slash(self):
        payload = TranslatePayload.model_validate(
            {"file_path": "/documents/report.pdf"}
        )

        self.assertEqual(payload.file_path, "documents/report.pdf")

    def test_translate_payload_rejects_empty_file_path(self):
        with self.assertRaises(ValidationError):
            TranslatePayload.model_validate({"file_path": "   "})

    def test_translate_payload_rejects_bad_language(self):
        # Именно ValidationError: HTTPException из валидатора языка наружу
        # пролезать не должен.
        with self.assertRaises(ValidationError):
            TranslatePayload.model_validate(
                {"file_path": "report.pdf", "target_language": "klingon"}
            )

    def test_translate_payload_accepts_auto_and_iso639_3(self):
        payload = TranslatePayload.model_validate(
            {
                "file_path": "report.pdf",
                "source_language": "auto",
                "target_language": "rus",
            }
        )

        self.assertEqual(payload.source_language, "auto")
        self.assertEqual(payload.target_language, "ru")


class TaskKeyTest(unittest.TestCase):
    def _envelope(self, **overrides) -> TaskEnvelope:
        data = {
            "task_id": "task-1",
            "user_id": "user-1",
            "task_type": "document-parser.translate",
        }
        data.update(overrides)
        return TaskEnvelope.model_validate(data)

    def test_build_task_key_uses_task_type_prefix(self):
        envelope = self._envelope(task_type="document_parser.translate")

        with patch("modules.broker.keys.settings.SERVICE_NAME", "document-parser"):
            key = build_task_key(envelope)

        self.assertEqual(key, "user-1:document_parser:task-1")

    def test_build_task_key_falls_back_to_service_name(self):
        envelope = self._envelope(task_type="document_parser.translate")

        with (
            patch("modules.broker.keys.settings.TASK_KEY_SERVICE_FROM_TASK_TYPE", False),
            patch("modules.broker.keys.settings.SERVICE_NAME", "document-parser"),
        ):
            key = build_task_key(envelope)

        self.assertEqual(key, "user-1:document-parser:task-1")

    def test_build_task_key_prefers_explicit_task_key(self):
        envelope = self._envelope(task_key="ready:made:key")

        self.assertEqual(build_task_key(envelope), "ready:made:key")


if __name__ == "__main__":
    unittest.main()
