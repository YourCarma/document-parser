"""Классификация исключений: что делать с сообщением и что сказать пользователю."""

from dataclasses import dataclass, replace
from enum import Enum

import aiohttp
from fastapi import HTTPException
from pydantic import ValidationError

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
    MSG_UPSTREAM_UNAVAILABLE,
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


class MessageAction(str, Enum):
    ACK = "ack"          # подтвердить, работы больше нет
    RETRY = "retry"      # повторить: retry-очередь либо nack(requeue)
    # Копию кладём в DLQ сами и только потом подтверждаем оригинал: у рабочей
    # очереди нет DLX, и reject(requeue=False) просто уничтожил бы сообщение.
    REJECT = "reject"


@dataclass(frozen=True, slots=True)
class ErrorDecision:
    action: MessageAction
    public_message: str | None      # текст в webhook_manager; None -> не пишем
    report: bool                    # публиковать ли ERROR самим консюмером
    log_level: str                  # "warning" | "error" | "critical"


def _reject(message: str | None, report: bool = True, log_level: str = "error") -> ErrorDecision:
    return ErrorDecision(MessageAction.REJECT, message, report, log_level)


_TRANSIENT = ErrorDecision(
    MessageAction.RETRY, MSG_UPSTREAM_UNAVAILABLE, True, "warning"
)
_PERMANENT_DEFAULT = _reject(MSG_BROKEN_DOCUMENT)

# Таблица данными, а не if-лесенкой. Порядок значим: первое совпадение по
# isinstance выигрывает, поэтому частные классы стоят выше базовых
# (WatchtowerUnavailable до WatchtowerError, TimeoutError до OSError).
_RULES: tuple[tuple[type[BaseException], ErrorDecision], ...] = (
    (InvalidEnvelope, _reject(None, report=False, log_level="critical")),
    (UnknownTaskType, _reject(MSG_UNKNOWN_TASK_TYPE)),
    (InvalidTaskPayload, _reject(MSG_INVALID_PAYLOAD)),
    (ValidationError, _reject(MSG_INVALID_PAYLOAD)),
    (UnsupportedSourceFormat, _reject(MSG_UNSUPPORTED_FORMAT)),
    (ContentNotSupportedError, _reject(MSG_UNSUPPORTED_FORMAT)),
    (FileNotFoundInStorage, _reject(MSG_FILE_NOT_FOUND)),
    (BucketNotFound, _reject(MSG_NO_BUCKET)),
    (FileTooLargeError, _reject(MSG_FILE_TOO_LARGE)),
    (LanguageNotSupported, _reject(MSG_LANGUAGE_UNDETECTED)),
    (TaskTimeout, _reject(MSG_TIMEOUT)),
    (ConversionTimeoutError, _reject(MSG_TIMEOUT)),
    # Встроенный TimeoutError (он же asyncio.TimeoutError). Кастомный
    # TimeoutError из parser/v1/exceptions.py сюда импортировать нельзя.
    (TimeoutError, _reject(MSG_TIMEOUT)),
    (WatchtowerUnavailable, _TRANSIENT),
    (ProcessPoolUnavailable, _TRANSIENT),
    (_TransientWebhookError, _TRANSIENT),
    (RetryableUpstreamError, _TRANSIENT),
    (WatchtowerError, _reject(MSG_BROKEN_DOCUMENT)),
    (aiohttp.ClientError, _TRANSIENT),
    (ConnectionError, _TRANSIENT),
    (OSError, _TRANSIENT),
)


def classify_error(exc: BaseException) -> ErrorDecision:
    """Отобразить исключение на решение по очереди.

    Неизвестное исключение считаем постоянным: молчаливый бесконечный
    requeue опаснее потерянного в DLQ сообщения.
    """
    if isinstance(exc, TaskCancelled):
        return ErrorDecision(MessageAction.ACK, None, False, "warning")

    if isinstance(exc, TaskPipelineFailed):
        if exc.cause is None:
            inner = _PERMANENT_DEFAULT
        else:
            inner = classify_error(exc.cause)
        # Конвейер уже опубликовал свой этапный текст — перетирать его
        # обобщённым не надо, поэтому report гасим всегда.
        return replace(inner, report=False)

    for exc_type, decision in _RULES:
        if isinstance(exc, exc_type):
            return decision

    if isinstance(exc, HTTPException):
        if exc.status_code >= 500:
            return _TRANSIENT
        return _PERMANENT_DEFAULT

    return _PERMANENT_DEFAULT
