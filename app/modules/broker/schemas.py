"""Схемы сообщений шины задач: общий конверт и payload по task_type."""

import json
from decimal import Decimal
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from modules.broker.exceptions import InvalidEnvelope
from modules.translator.v1.exceptions import InvalidLanguageCode
from modules.translator.v1.schemas import normalize_language_code


class TaskType(str, Enum):
    """Типы задач, которые сервис умеет обрабатывать."""

    TRANSLATE = "document-parser.translate"


class TaskEnvelope(BaseModel):
    """Общий конверт сообщения. `payload` — сервис-специфичный."""

    model_config = ConfigDict(extra="ignore")

    task_id: str
    user_id: str
    task_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    # Если гейтвей когда-нибудь пришлёт готовый ключ — он важнее собранного.
    task_key: str | None = None

    @field_validator("task_id", "user_id", "task_type", mode="before")
    @classmethod
    def _to_stripped_str(cls, value: Any) -> Any:
        # pydantic v2 не приводит int к str сам, а гейтвей в примере прислал
        # user_id числом.
        if value is None:
            return value
        if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
            return str(value)
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("task_id", "user_id", "task_type", mode="after")
    @classmethod
    def _reject_empty(cls, value: str) -> str:
        if not value:
            raise ValueError("обязательное поле конверта пустое")
        return value

    @field_validator("task_key", mode="after")
    @classmethod
    def _strip_task_key(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None


class TranslatePayload(BaseModel):
    """Payload задачи `document-parser.translate`."""

    model_config = ConfigDict(extra="ignore")

    file_path: str
    source_language: str = "auto"
    target_language: str = "ru"
    bucket: str | None = None
    output_prefix: str | None = None
    parse_images: bool = False
    include_image_in_output: bool = False
    full_vlm_pdf_parse: bool = False

    @field_validator("source_language", "target_language", mode="before")
    @classmethod
    def _default_language(cls, value: Any, info) -> Any:
        if value is None or (isinstance(value, str) and not value.strip()):
            return "auto" if info.field_name == "source_language" else "ru"
        return value

    @field_validator("file_path", mode="after")
    @classmethod
    def _normalize_object_key(cls, value: str) -> str:
        normalized = value.strip().replace("\\", "/").lstrip("/")
        if not normalized:
            raise ValueError("file_path обязателен")
        return normalized

    @field_validator("source_language", "target_language", mode="after")
    @classmethod
    def _normalize_language(cls, value: str) -> str:
        try:
            return normalize_language_code(value)
        except InvalidLanguageCode as exc:
            # pydantic v2 не оборачивает не-ValueError: без перевода
            # HTTPException пролез бы наружу мимо ValidationError.
            raise ValueError(str(exc.detail)) from exc

    @field_validator("bucket", "output_prefix", mode="after")
    @classmethod
    def _normalize_optional_path(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip().strip("/") or None


def parse_envelope(raw: bytes | str | dict[str, Any]) -> TaskEnvelope:
    """Разобрать тело сообщения. Любая неудача -> `InvalidEnvelope`."""
    if isinstance(raw, (bytes, bytearray)):
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise InvalidEnvelope(f"Тело сообщения не является JSON: {exc}") from exc
    elif isinstance(raw, str):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise InvalidEnvelope(f"Тело сообщения не является JSON: {exc}") from exc
    else:
        data = raw

    if not isinstance(data, dict):
        raise InvalidEnvelope(
            f"Тело сообщения должно быть объектом, получен {type(data).__name__}"
        )

    data = dict(data)
    payload = data.get("payload")
    if not data.get("user_id") and isinstance(payload, dict) and payload.get("user_id"):
        # Верхний уровень главнее: сюда попадаем только когда его нет вовсе.
        data["user_id"] = payload["user_id"]

    try:
        return TaskEnvelope.model_validate(data)
    except ValidationError as exc:
        raise InvalidEnvelope(f"Конверт не прошёл валидацию: {exc}") from exc


def payload_user_id_differs(envelope: TaskEnvelope) -> bool:
    """True, если `payload.user_id` есть и не совпадает с верхнеуровневым."""
    payload_user_id = envelope.payload.get("user_id")
    if payload_user_id is None or payload_user_id == "":
        return False
    return str(payload_user_id).strip() != envelope.user_id
