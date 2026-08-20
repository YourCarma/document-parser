"""Контракт задач: что сервис ожидает получить и что отдаёт в ответ.

Написан с точки зрения консьюмера. Клиент в очередь не публикует: он ставит
задачу через `task_gateway`, а тот сам заводит запись задачи и кладёт
сообщение в брокер. Поэтому главное в контракте — `task_type` и поля payload,
а не механика AMQP; она вынесена в приложение, для отладки и эксплуатации.

Контракт собирается из тех же объектов, которыми пользуется консюмер:
pydantic-модели конверта и payload, реестр обработчиков, настройки топологии
и лимиты. Отдельного описания, которое надо не забыть обновить, здесь нет —
именно поэтому документация не разъезжается с кодом.
"""

import json
from typing import Any

from modules.broker.dispatcher import build_default_dispatcher
from modules.broker.keys import service_segment_for
from modules.broker.schemas import TaskEnvelope
from modules.parser.v1.utils import SUPPORTED_EXTENSIONS
from modules.translator.v2.schemas import TranslatorResponseData
from modules.webhook_manager.schemas import TaskStatus
from settings import settings


CONTRACT_VERSION = "2"

_SUBMIT_RULES: tuple[str, ...] = (
    "`task_id` придумывать не нужно: его генерирует гейтвей и возвращает "
    "третьим сегментом `task_key`.",
    "Пользователь задаётся заголовком `x-user-id`; он приоритетнее "
    "`payload.user_id`, а бакет ищется именно по нему.",
    "Бакет в задаче не передаётся и передан быть не может: сервис находит его "
    "сам по `user_id`. Иначе клиент мог бы записать файл в чужое хранилище.",
    "Ответ гейтвея `{\"task_key\": \"...\"}` нужно сохранить: по этому ключу "
    "читается прогресс и отменяется задача.",
    "Задачу в webhook_manager создаёт гейтвей. Сервис её только двигает — "
    "если записи нет, работа выполнится, но статус писать будет некуда.",
    "Неизвестные поля игнорируются: расширять запрос безопасно.",
)

_ENVELOPE_RULES: tuple[str, ...] = (
    "Тело сообщения — JSON-объект в UTF-8, `content_type: application/json`, "
    "публикация с `delivery_mode=2` (persistent).",
    "`task_id`, `user_id` и `task_type` обязательны и не могут быть пустыми. "
    "Пустое или отсутствующее поле — сообщение уедет в DLQ без повторов.",
    "`user_id` внутри payload не нужен. Если он там есть и отличается от "
    "верхнеуровневого, в лог уйдёт предупреждение, а работать сервис будет по "
    "верхнеуровневому.",
    "Повторная доставка того же `task_id` безопасна: перед работой сервис "
    "читает статус задачи и не переделывает уже готовую.",
)

_PROGRESS_RULES: tuple[str, ...] = (
    "В очередь ответ не публикуется и колбэка клиенту не приходит: результат "
    "читается из webhook_manager опросом по `task_key`.",
    "Прогресс идёт от 0 к 100 и сопровождается человекочитаемым "
    "`text_status` — его можно показывать пользователю как есть.",
    "Задача завершена, когда статус стал терминальным: READY, ERROR или "
    "CANCELLED. Промежуточные статусы означают, что работа идёт.",
    "`response_data` хранится строкой JSON. Сервис дописывает свои поля, не "
    "затирая чужие.",
    "Файлы отдаются object key внутри бакета, а не ссылкой: ссылка протухает "
    "по сроку, ключ живёт столько же, сколько файл.",
    "READY с непустым счётчиком в `text_status` («Не переведено элементов: "
    "N») означает частичный успех: файл готов, но часть элементов вернулась "
    "без перевода из-за сбоев переводчика.",
)

_FAILURE_RULES: tuple[str, ...] = (
    "Повторы делает сам сервис: временный сбой (недоступное хранилище, "
    "переводчик, сеть) уходит в retry-очередь с паузой и возвращается в "
    "работу. Клиенту ретраить не нужно.",
    "После исчерпания попыток задача получает статус ERROR, а причина "
    "человекочитаемым текстом ложится в `text_status` и `error`.",
    "Постоянные ошибки (неизвестный `task_type`, неподдерживаемый формат, "
    "файл не найден в хранилище, нет бакета у пользователя) дают ERROR сразу, "
    "без повторов — их надо чинить на стороне клиента.",
    "Клиенту повторять постановку не нужно и вредно: получится вторая задача "
    "с тем же файлом. Дождитесь терминального статуса.",
    "Остановка или переезд пода задачу не теряет: незавершённое сообщение "
    "возвращается в очередь и доигрывается другой репликой.",
)


def _payload_rules() -> tuple[str, ...]:
    """Правила payload, часть значений — из настроек этого окружения."""
    return (
        "`file_path` — object key внутри бакета пользователя: без имени "
        "бакета и без ведущего слэша. Обратные слэши приводятся к прямым.",
        "Расширение файла обязано быть из списка поддерживаемых — оно "
        "проверяется до скачивания, а MIME-тип не проверяется вовсе.",
        f"Файл больше {settings.MAX_DOWNLOAD_FILE_SIZE_MB} МБ будет отклонён "
        "на скачивании.",
        "Коды языков — ISO 639-1 или 639-3, приводятся к нижнему регистру. "
        "`source_language: \"auto\"` включает автоопределение по первым "
        "абзацам документа; неизвестный код — постоянная ошибка.",
        "Пустая строка в языках равнозначна значению по умолчанию: `auto` для "
        "исходного языка, `ru` для целевого.",
        f"`output_prefix` по умолчанию — `{settings.TRANSLATE_OUTPUT_PREFIX}` "
        "с подстановкой `task_id`.",
        "`parse_images` и `full_vlm_pdf_parse` требуют доступной VLM и "
        "заметно удлиняют задачу; включайте их осознанно.",
    )


def _task_types() -> list[dict[str, Any]]:
    """Типы задач и схемы их payload — из реестра обработчиков."""
    dispatcher = build_default_dispatcher()
    result: list[dict[str, Any]] = []
    for task_type in dispatcher.task_types:
        handler = dispatcher.resolve(task_type)
        payload_model = getattr(handler, "payload_model", None)
        result.append(
            {
                "task_type": task_type,
                "description": (handler.__doc__ or "").strip().splitlines()[0]
                if handler.__doc__
                else "",
                "routing_key": task_type,
                "payload_schema": payload_model.model_json_schema()
                if payload_model is not None
                else {},
                "payload_example": _payload_example(payload_model),
            }
        )
    return result


def _payload_example(payload_model) -> dict[str, Any]:
    """Пример payload.

    У обязательных полей берём `examples`, у необязательных — значения по
    умолчанию. Пример копируют как есть, а копировать чужой `output_prefix` с
    зашитым внутрь task_id не нужно никому.
    """
    if payload_model is None:
        return {}
    example: dict[str, Any] = {}
    for name, field in payload_model.model_fields.items():
        if field.is_required():
            example[name] = field.examples[0] if field.examples else ""
        else:
            example[name] = field.get_default(call_default_factory=True)
    return example


_PREREQUISITES: tuple[str, ...] = (
    "Файл уже лежит в персональном бакете пользователя (watchtower): сервис "
    "принимает не файл, а его object key и скачивает содержимое сам.",
    "У пользователя есть персональный Document-ресурс в resource_manager — "
    "именно он и определяет бакет. Без него задача завершится ошибкой.",
)


def _submission() -> dict[str, Any]:
    """Как клиент ставит задачу: через task_gateway, не в очередь напрямую."""
    return {
        "via": "task_gateway",
        "endpoint": "POST /api/v1/broker/publish",
        "headers": {"x-user-id": "идентификатор пользователя, обязателен"},
        "request_fields": {
            "task_type": "тип задачи из списка ниже",
            "payload": "параметры задачи, поля описаны ниже",
        },
        "response": {
            "task_key": (
                f"1234:{settings.SERVICE_NAME}:"
                "5fb0b68c-2259-47d8-8e72-3dc517ac6d4d"
            )
        },
        "rules": list(_SUBMIT_RULES),
        "prerequisites": list(_PREREQUISITES),
        "note": (
            "Адрес гейтвея зависит от контура — спросите у платформенной "
            "команды. Публиковать в брокер напрямую клиенту не нужно: этим "
            "занимается гейтвей."
        ),
    }


def _cancellation() -> dict[str, Any]:
    return {
        "endpoint": "POST /api/v1/tasks/cancel?task_id=...",
        "via": "task_gateway",
        "mechanism": (
            "Гейтвей ставит задаче статус CANCELLED. Сервис читает статус на "
            "контрольных точках между этапами и аккуратно выходит из "
            "конвейера, публикуя CANCELLED с последним прогрессом."
        ),
        "latency": (
            "Отмена срабатывает не мгновенно: внутри длинного этапа "
            "(парсинг, перевод) сервис дойдёт до ближайшей контрольной точки. "
            "Загрузка большого файла и парсинг могут занять минуты."
        ),
    }


def build_contract(version: str = "") -> dict[str, Any]:
    """Полный контракт публикации задач для этого окружения."""
    task_types = _task_types()
    example_task_type = task_types[0]["task_type"] if task_types else ""
    envelope_example = {
        "task_id": "5fb0b68c-2259-47d8-8e72-3dc517ac6d4d",
        "user_id": "1234",
        "task_type": example_task_type,
        "payload": task_types[0]["payload_example"] if task_types else {},
    }

    return {
        "contract_version": CONTRACT_VERSION,
        "service": {
            "name": settings.SERVICE_NAME,
            "version": version,
            "environment": settings.DEPLOY_ENVIRONMENT,
            # Топология и лимиты берутся из настроек этого пода: контракт
            # описывает то окружение, у которого его запросили.
            "broker_enabled": settings.BROKER_ENABLED,
        },
        "transport": {
            "protocol": "amqp",
            "exchange": settings.RMQ_EXCHANGE,
            "exchange_type": settings.RMQ_EXCHANGE_TYPE,
            "routing_keys": list(settings.RMQ_ROUTING_KEYS),
            "queue": settings.RMQ_QUEUE,
            "content_type": "application/json",
            "encoding": "utf-8",
            "delivery_mode": 2,
            "owner": (
                "Exchange, очередь и биндинг создаёт task_gateway: сервис их "
                "не объявляет, только проверяет наличие."
            ),
            "audience": (
                "Справочно. Клиент публикует через гейтвей и этих имён не "
                "касается."
            ),
        },
        "submission": _submission(),
        "task_types": task_types,
        "payload_rules": list(_payload_rules()),
        "task_key": {
            "format": "{user_id}:{service}:{task_id}",
            "service_segment": service_segment_for(example_task_type),
            "source": (
                "префикс task_type до точки"
                if settings.TASK_KEY_SERVICE_FROM_TASK_TYPE
                else f"SERVICE_NAME сервиса ({settings.SERVICE_NAME})"
            ),
            "example": (
                f"1234:{service_segment_for(example_task_type)}:"
                "5fb0b68c-2259-47d8-8e72-3dc517ac6d4d"
            ),
        },
        "progress": {
            "storage": "webhook_manager",
            "statuses": [status.value for status in TaskStatus],
            "terminal_statuses": [
                TaskStatus.READY.value,
                TaskStatus.ERROR.value,
                TaskStatus.CANCELLED.value,
            ],
            "response_data_schema": TranslatorResponseData.model_json_schema(),
            "rules": list(_PROGRESS_RULES),
        },
        "failure_semantics": {
            "max_retries": settings.RMQ_MAX_RETRIES,
            "retry_delay_secs": settings.RMQ_RETRY_DELAY_SECS,
            "dead_letter_queue": settings.RMQ_DLQ,
            "rules": list(_FAILURE_RULES),
        },
        "limits": {
            "max_file_size_mb": settings.MAX_DOWNLOAD_FILE_SIZE_MB,
            "task_timeout_secs": settings.TASK_TIMEOUT_SECS,
            "parse_timeout_secs": settings.PARSE_TIMEOUT_SECS,
            "supported_extensions": sorted(SUPPORTED_EXTENSIONS),
        },
        "cancellation": _cancellation(),
        # Приложение: механика очереди. Клиенту она не нужна — это для отладки
        # гейтвея и эксплуатации.
        "envelope": {
            "schema": TaskEnvelope.model_json_schema(),
            "example": envelope_example,
            "rules": list(_ENVELOPE_RULES),
        },
    }
