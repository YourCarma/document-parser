import unittest

import aiohttp
from fastapi import HTTPException
from pydantic import BaseModel, ValidationError

from modules.broker.errors import MessageAction, classify_error
from modules.broker.exceptions import (
    InvalidEnvelope,
    InvalidTaskPayload,
    TaskPipelineFailed,
    UnknownTaskType,
    UnsupportedSourceFormat,
)
from modules.messages import (
    MSG_BROKEN_DOCUMENT,
    MSG_FILE_NOT_FOUND,
    MSG_FILE_TOO_LARGE,
    MSG_INVALID_PAYLOAD,
    MSG_LANGUAGE_UNDETECTED,
    MSG_NO_BUCKET,
    MSG_TIMEOUT,
    MSG_UNKNOWN_TASK_TYPE,
    MSG_UNSUPPORTED_FORMAT,
)
from modules.parser.v1.exceptions import (
    ContentNotSupportedError,
    ConversionTimeoutError,
    ProcessPoolUnavailable,
)
from modules.resource_manager.exceptions import BucketNotFound
from modules.translator.v1.exceptions import LanguageNotSupported
from modules.translator.v1.utils import RetryableUpstreamError
from modules.translator.v2.exceptions import TaskTimeout
from modules.watchtower.exceptions import (
    FileNotFoundInStorage,
    FileTooLargeError,
    WatchtowerError,
    WatchtowerUnavailable,
)
from modules.webhook_manager.cancellation import TaskCancelled
from modules.webhook_manager.service import _TransientWebhookError


class _Model(BaseModel):
    value: int


def _validation_error() -> ValidationError:
    try:
        _Model.model_validate({"value": "не число"})
    except ValidationError as exc:
        return exc
    raise AssertionError("ожидалась ValidationError")


class ClassifyErrorTest(unittest.TestCase):
    def test_cancelled_task_is_acked(self):
        decision = classify_error(TaskCancelled("user:svc:task", "перевод документа"))

        self.assertIs(decision.action, MessageAction.ACK)
        self.assertFalse(decision.report)
        self.assertIsNone(decision.public_message)

    def test_invalid_envelope_rejects_without_report(self):
        decision = classify_error(InvalidEnvelope("не JSON"))

        self.assertIs(decision.action, MessageAction.REJECT)
        self.assertFalse(decision.report)
        self.assertIsNone(decision.public_message)
        self.assertEqual(decision.log_level, "critical")

    def test_permanent_errors_map_to_reject_with_public_text(self):
        cases = [
            (UnknownTaskType("document-parser.parse"), MSG_UNKNOWN_TASK_TYPE),
            (InvalidTaskPayload("нет file_path"), MSG_INVALID_PAYLOAD),
            (_validation_error(), MSG_INVALID_PAYLOAD),
            (UnsupportedSourceFormat("report.zip"), MSG_UNSUPPORTED_FORMAT),
            (ContentNotSupportedError("zip"), MSG_UNSUPPORTED_FORMAT),
            (FileNotFoundInStorage("нет файла"), MSG_FILE_NOT_FOUND),
            (BucketNotFound("user-1"), MSG_NO_BUCKET),
            (FileTooLargeError("слишком большой"), MSG_FILE_TOO_LARGE),
            (LanguageNotSupported(detail="не определён"), MSG_LANGUAGE_UNDETECTED),
            (TaskTimeout("парсинг"), MSG_TIMEOUT),
            (ConversionTimeoutError("soffice"), MSG_TIMEOUT),
            (TimeoutError("время вышло"), MSG_TIMEOUT),
            (WatchtowerError("хранилище ответило 400"), MSG_BROKEN_DOCUMENT),
        ]

        for exc, expected_message in cases:
            with self.subTest(exception=type(exc).__name__):
                decision = classify_error(exc)
                self.assertIs(decision.action, MessageAction.REJECT)
                self.assertEqual(decision.public_message, expected_message)
                self.assertTrue(decision.report)

    def test_transient_errors_map_to_retry(self):
        cases = [
            WatchtowerUnavailable("хранилище недоступно"),
            ProcessPoolUnavailable(),
            RetryableUpstreamError(status_code=503, detail="переводчик недоступен"),
            aiohttp.ClientError("соединение разорвано"),
            _TransientWebhookError("webhook_manager 500"),
            ConnectionError("connection refused"),
            OSError("сеть недоступна"),
        ]

        for exc in cases:
            with self.subTest(exception=type(exc).__name__):
                self.assertIs(classify_error(exc).action, MessageAction.RETRY)

    def test_http_exception_split_by_status(self):
        self.assertIs(
            classify_error(HTTPException(status_code=502)).action, MessageAction.RETRY
        )
        self.assertIs(
            classify_error(HTTPException(status_code=422)).action, MessageAction.REJECT
        )

    def test_pipeline_failure_never_reports(self):
        decision = classify_error(
            TaskPipelineFailed(FileNotFoundInStorage("нет файла"), "получение файла")
        )

        self.assertIs(decision.action, MessageAction.REJECT)
        self.assertFalse(decision.report)

    def test_pipeline_failure_with_transient_cause_retries(self):
        decision = classify_error(
            TaskPipelineFailed(WatchtowerUnavailable("503"), "получение файла")
        )

        self.assertIs(decision.action, MessageAction.RETRY)
        self.assertFalse(decision.report)

    def test_pipeline_failure_without_cause_rejects(self):
        decision = classify_error(TaskPipelineFailed(None, "перевод документа"))

        self.assertIs(decision.action, MessageAction.REJECT)
        self.assertFalse(decision.report)

    def test_unknown_exception_defaults_to_reject(self):
        decision = classify_error(RuntimeError("что-то пошло не так"))

        self.assertIs(decision.action, MessageAction.REJECT)
        self.assertEqual(decision.public_message, MSG_BROKEN_DOCUMENT)


if __name__ == "__main__":
    unittest.main()
