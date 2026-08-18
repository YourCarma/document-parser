"""Сборка ключа задачи webhook_manager: `{user_id}:{service}:{task_id}`."""

from loguru import logger

from modules.broker.schemas import TaskEnvelope
from settings import settings


# Расхождение написания имени сервиса логируем один раз на процесс, иначе
# каждое сообщение засорит лог одинаковым предупреждением.
_warned: set[str] = set()


def normalize_service_name(value: str) -> str:
    """Регистронезависимо, '_' -> '-'. Защита от расхождения написания."""
    return value.strip().lower().replace("_", "-")


def service_segment_for(task_type: str) -> str:
    """Средний сегмент ключа задачи.

    По умолчанию берётся префикс `task_type`: гейтвей строит ключ из того же
    имени, которым назвал тип задачи, поэтому это надёжнее, чем SERVICE_NAME.
    """
    task_type = str(task_type or "").strip()
    if settings.TASK_KEY_SERVICE_FROM_TASK_TYPE and "." in task_type:
        prefix = task_type.split(".", 1)[0].strip()
        if prefix:
            if normalize_service_name(prefix) != normalize_service_name(
                settings.SERVICE_NAME
            ):
                if prefix not in _warned:
                    _warned.add(prefix)
                    logger.warning(
                        "Broker: префикс task_type '{}' не совпадает с "
                        "SERVICE_NAME '{}'. Ключ задачи собирается по префиксу "
                        "из сообщения.",
                        prefix,
                        settings.SERVICE_NAME,
                    )
            # Ключ обязан совпадать байт в байт с гейтвеевским, поэтому
            # префикс возвращаем как есть, без нормализации.
            return prefix
    return settings.SERVICE_NAME


def build_task_key(envelope: TaskEnvelope) -> str:
    """Ключ задачи: готовый из конверта либо собранный из его полей."""
    if envelope.task_key:
        return envelope.task_key
    segment = service_segment_for(envelope.task_type)
    return f"{envelope.user_id}:{segment}:{envelope.task_id}"
