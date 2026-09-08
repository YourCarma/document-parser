"""Рендер контракта в Markdown — формат, который клиент отдаёт своему агенту.

Таблицы полей строятся из JSON Schema, то есть из тех же моделей, что
валидируют сообщение. Расхождение описания и поведения исключено по
построению: чтобы соврать в документации, придётся соврать в модели.
"""

import json
from typing import Any


def _type_name(prop: dict[str, Any]) -> str:
    """Человекочитаемый тип поля из JSON Schema."""
    if "anyOf" in prop:
        parts = [_type_name(item) for item in prop["anyOf"]]
        # `str | None` показываем как `string, необязательное`, а не как union
        # с null: продюсеру важно «можно не передавать», а не устройство схемы.
        parts = [part for part in parts if part != "null"]
        return " | ".join(parts) or "null"
    schema_type = prop.get("type", "any")
    if schema_type == "array":
        return f"array<{_type_name(prop.get('items', {}))}>"
    return str(schema_type)


def _default_cell(name: str, prop: dict[str, Any], required: set[str]) -> str:
    if name in required:
        return "**обязательное**"
    if "default" in prop:
        default = prop["default"]
        if default is None:
            return "`null`"
        return f"`{json.dumps(default, ensure_ascii=False)}`"
    return "—"


def _fields_table(schema: dict[str, Any]) -> str:
    """Таблица «поле — тип — обязательность — описание» из JSON Schema."""
    required = set(schema.get("required", []))
    rows = ["| Поле | Тип | По умолчанию | Описание |", "| --- | --- | --- | --- |"]
    for name, prop in schema.get("properties", {}).items():
        description = str(prop.get("description", "")).replace("\n", " ").strip()
        rows.append(
            f"| `{name}` | {_type_name(prop)} | "
            f"{_default_cell(name, prop, required)} | {description} |"
        )
    return "\n".join(rows)


def _numbered(items: list[str]) -> str:
    return "\n".join(f"{index}. {item}" for index, item in enumerate(items, 1))


def _bullets(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items)


def _json_block(value: Any) -> str:
    return "```json\n" + json.dumps(value, ensure_ascii=False, indent=2) + "\n```"


def render_markdown(contract: dict[str, Any]) -> str:
    """Собрать контракт в Markdown."""
    service = contract["service"]
    submission = contract["submission"]
    transport = contract["transport"]
    envelope = contract["envelope"]
    task_key = contract["task_key"]
    progress = contract["progress"]
    failure = contract["failure_semantics"]
    cancellation = contract["cancellation"]
    limits = contract["limits"]

    example_task = contract["task_types"][0] if contract["task_types"] else None
    parts: list[str] = []

    parts.append(
        f"""# Контракт задач `{service['name']}`

`{service['name']}` — исполнитель задач: он не принимает файлы по очереди и не
публикует ответы. Клиент ставит задачу через `task_gateway`, сервис забирает её
из брокера, а прогресс и результат пишет в `webhook_manager`.

```
клиент ──POST /api/v1/broker/publish──▶ task_gateway ──▶ брокер ──▶ {service['name']}
   ▲                                         │                              │
   └──── опрос по task_key ── webhook_manager ◀──── прогресс и результат ────┘
```

| | |
| --- | --- |
| Сервис | `{service['name']}` |
| Версия | `{service['version'] or 'не указана'}` |
| Окружение | `{service['environment']}` |
| Версия контракта | `{contract['contract_version']}` |

Документ сгенерирован самим сервисом из его моделей и настроек, поэтому
описывает ровно то окружение, у которого запрошен. Актуальную версию всегда
можно перечитать: `GET /api/v1/contract.md`; машиночитаемый вариант с
JSON Schema — `GET /api/v1/contract`."""
    )

    parts.append(
        f"""## 1. Что должно быть готово до постановки задачи

{_bullets(submission['prerequisites'])}"""
    )

    example_request = {
        "task_type": example_task["task_type"] if example_task else "",
        "payload": example_task["payload_example"] if example_task else {},
    }
    parts.append(
        f"""## 2. Как поставить задачу

`{submission['endpoint']}` у `{submission['via']}`.

Заголовок: `x-user-id` — {submission['headers']['x-user-id']}.

Тело запроса:

{_json_block(example_request)}

Ответ:

{_json_block(submission['response'])}

{submission['note']}

Правила:

{_bullets(submission['rules'])}"""
    )

    for index, task in enumerate(contract["task_types"], 1):
        parts.append(
            f"""## 3.{index} Задача `{task['task_type']}`

{task['description']}

Поля `payload`:

{_fields_table(task['payload_schema'])}

Правила заполнения:

{_bullets(contract['payload_rules'])}"""
        )

    parts.append(
        f"""## 4. Как следить за выполнением

Прогресс и результат живут в `webhook_manager`, ключ — `task_key` из ответа
гейтвея (формат `{task_key['format']}`, например `{task_key['example']}`):

```
GET /api/v1/storage/task?key=<task_key>
```

Статусы: {', '.join(f'`{status}`' for status in progress['statuses'])}.
Терминальные: {', '.join(f'`{status}`' for status in progress['terminal_statuses'])}.

Поле `response_data` хранится JSON-строкой и содержит:

{_fields_table(progress['response_data_schema'])}

{_bullets(progress['rules'])}"""
    )

    parts.append(
        f"""## 5. Отмена

`{cancellation['endpoint']}` у `{cancellation['via']}`.

{cancellation['mechanism']}

{cancellation['latency']}"""
    )

    parts.append(
        f"""## 6. Ошибки и повторы

| Параметр | Значение |
| --- | --- |
| Повторов на задачу | `{failure['max_retries']}` |
| Пауза перед повтором | `{failure['retry_delay_secs']}` c |

{_bullets(failure['rules'])}"""
    )

    extensions = ", ".join(f"`{ext}`" for ext in limits["supported_extensions"])
    parts.append(
        f"""## 7. Ограничения

| Параметр | Значение |
| --- | --- |
| Максимальный размер файла | {limits['max_file_size_mb']} МБ |
| Лимит времени на задачу | {limits['task_timeout_secs']} c |
| Лимит времени на парсинг | {limits['parse_timeout_secs']} c |

Поддерживаемые расширения: {extensions}.

Задача, не уложившаяся в лимит времени, завершается статусом ERROR с текстом
про превышение времени обработки."""
    )

    parts.append(
        f"""## 8. Чек-лист

- [ ] Файл лежит в бакете пользователя, `file_path` — object key без имени бакета и без ведущего слэша.
- [ ] Расширение файла есть в списке поддерживаемых.
- [ ] `task_type` совпадает с одним из перечисленных выше.
- [ ] Передан заголовок `x-user-id`, и это владелец бакета.
- [ ] Коды языков валидны по ISO 639 (или `auto` для исходного).
- [ ] `task_key` из ответа сохранён — по нему читается прогресс и делается отмена.
- [ ] Клиент не ретраит постановку сам и не ждёт ответа в очереди."""
    )

    parts.append(
        f"""## Приложение. Что приезжает консьюмеру

Этот раздел нужен только для отладки гейтвея и эксплуатации: клиент сообщения
не собирает и имён очередей не касается.

| Параметр | Значение |
| --- | --- |
| Exchange | `{transport['exchange']}` (`{transport['exchange_type']}`) |
| Routing keys | {', '.join(f'`{key}`' for key in transport['routing_keys']) or '—'} |
| Очередь | `{transport['queue']}` |
| Content-Type | `{transport['content_type']}`, кодировка `{transport['encoding']}` |
| Delivery mode | `{transport['delivery_mode']}` (persistent) |
| Очередь недоставленных | `{failure['dead_letter_queue']}` |

{transport['owner']}

Конверт сообщения:

{_fields_table(envelope['schema'])}

{_json_block(envelope['example'])}

{_bullets(envelope['rules'])}"""
    )

    return "\n\n".join(parts) + "\n"
