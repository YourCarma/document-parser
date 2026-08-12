# Translator V2 — асинхронный перевод документов

Файл-ориентир для ИИ-агентов, работающих с `app/modules/translator/v2/`.
Описывает, что делает модуль, с кем общается, какие инварианты нельзя ломать.

## 1. Назначение

`TranslatorV2Service` — фоновый конвейер, который принимает загруженный
документ, парсит его в `DoclingDocument`, поэлементно переводит текст через
внешний сервис перевода, собирает результат в `.docx`, кладёт оригинал и перевод
в облачное хранилище пользователя и публикует прогресс задачи.

Отличие от `translator/v1`: v1 синхронный (результат возвращается в том же HTTP-
ответе), v2 асинхронный — клиент сразу получает `task_id` и опрашивает прогресс
в `webhook_manager`.

## 2. Файлы модуля

| Файл | Роль |
| --- | --- |
| `router.py` | `POST /api/v2/parser/translator/file/word` — приём файла, создание задачи, постановка в `BackgroundTasks`. |
| `service.py` | `TranslatorV2Service` — весь фоновый конвейер. |
| `schemas.py` | `TranslatorResponseData` (снимок состояния задачи), `TranslatorV2Response` (ответ роутера). |

## 3. Внешние зависимости

Все клиенты — тонкие обёртки над `aiohttp`, живут в `app/modules/`:

- `resource_manager.service.ResourceManagerService` — `get_user_bucket(user_id)`.
  Ходит в `GET /api/v1/resource/` с заголовком `x-user-id`, ищет **ровно один**
  ресурс с `resource_type == "Document"` и `resource_owner == "User"`, возвращает
  его `id` как bucket. Несколько персональных ресурсов → `ValueError`.
- `watchtower.service.WatchtowerService` — `upload_file(bucket, local_path, filename)`
  и `get_sharelink(bucket, object_key)`. Загрузка идёт multipart-ом с
  `quote_fields=False`: Watchtower сохраняет имя из multipart буквально, поэтому
  предварительное URL-кодирование кириллицы ломает имя объекта (см. комментарии
  в коде — это не случайность, не «чинить»).
- `webhook_manager.service.WebhookManagerService` — `create_task`,
  `update_progress`, `update_response_data`. Ключ задачи — `user_id:SERVICE_NAME:task_id`.
- `translator.v1.service.CustomModelTranslator` — `detect_language(text)` и
  `translate_element_limited(text)` (перевод одного элемента под семафором).
- `parser.v1.utils.parse_document` / `run_in_process` — парсинг в отдельном
  процессе через `app.state.executor` (`ProcessPoolExecutor`).

Внешние HTTP-сервисы: сервис перевода (`TRANSLATOR_ADDRESS + TRANSLATE_URI`),
детектор языка (`DETECT_LANGUAGE_URL`), `webhook_manager`, `watchtower`,
`resource_manager`. Без них v2-сценарий не завершится.

## 4. Поток выполнения

```
POST /api/v2/parser/translator/file/word  (X-User-ID, файл, языки, параметры парсера)
  │
  ├─ webhook.create_task(...)            -> task_key = "user:service:task_id"
  ├─ save_file(upload)                   -> временный файл на диске
  └─ BackgroundTasks.add_task(run_translation_task)   -> 200 {task_id, key}

run_translation_task (фон):
  1. получение бакета пользователя   resource_manager.get_user_bucket
  2. загрузка оригинального файла    watchtower.upload_file + get_sharelink   -> 5..10 %
  3. парсинг документа               run_in_process(parse_document, TO_DOCLING) -> 15 %
  4. перевод документа               _translate_with_progress                 -> 15..93 %
  5. загрузка переведённого файла    watchtower.upload_file + get_sharelink   -> 95 %
  6. READY                                                                    -> 100 %
  finally: удалить временный исходник и временный .docx
```

### Внутри `_translate_with_progress`

1. Если `source_language == "auto"` — взять первые 3 непустых `TextItem`,
   вызвать `detect_language`. Не определился → `LanguageNotSupported`.
2. Обойти `docling_doc.iterate_items()`: собрать `TextItem` (сохранив
   `element.orig = element.text`) и ячейки `TableItem.data.table_cells`.
3. Перевести батчами размером `TRANSLATOR_MAX_CONCURRENCY`
   (`_translate_in_batches`) — сначала тексты, потом ячейки. Батчи нужны, чтобы
   не создавать корутину на каждый элемент документа сразу.
4. Каждый элемент переводится через `translate_tracked`: `TimeoutError` и
   `RetryableUpstreamError` **не роняют задачу** — в документ подставляется
   оригинал с суффиксом `" (ошибка запроса, переведите вручную)"`.
5. Прогресс публикуется примерно 20 раз за задачу (`update_every = total // 20`),
   под `asyncio.Lock`, ошибки публикации прогресса только логируются.
6. Экспорт: `DoclingDocument -> markdown -> pypandoc -> .docx` в
   `asyncio.to_thread` (`_export_to_word_sync`).

## 5. Модель прогресса и статусов

`webhook_manager` хранит два поля: числовой `progress` + `status`
(`PENDING/PROCESSING/READY/ERROR`) и JSON `response_data` = `TranslatorResponseData`:

```json
{
  "original_language": "en",
  "target_language": "ru",
  "original_file": "<sharelink>",
  "translated_file": "<sharelink>",
  "text_status": "Перевожу... 45/120 элементов",
  "error": null
}
```

Шкала: 5 → 10 → 15 → (15..93 перевод) → 95 → 100. При ошибке `progress = 0`,
`status = ERROR`.

## 6. Обработка ошибок

- `current_stage` — строковый маркер текущего этапа; в `except` он переводится в
  пользовательское сообщение через `_STAGE_MESSAGES` / `_stage_to_user_message`.
  Технический текст исключения уходит только в лог, наружу идёт этапное сообщение.
- Падение публикации ошибки тоже перехватывается — задача не должна валить воркер.
- `finally` всегда удаляет исходный временный файл и, если он создан,
  переведённый `.docx`.

## 7. Конкурентность и ресурсы

Создаются в `app/main.py` (lifespan) и прокидываются в сервис через `router.py`:

- `app.state.executor` — `ProcessPoolExecutor(PARSER_WORKERS)` для парсинга.
- `app.state.parser_semaphore` — `Semaphore(PARSER_WORKERS)`, ограничивает
  очередь к пулу процессов.
- `app.state.translation_semaphore` — `Semaphore(TRANSLATOR_MAX_CONCURRENCY)`,
  **общий на всё приложение** лимит одновременных запросов к переводчику;
  передаётся в `CustomModelTranslator(shared_semaphore=...)`.
- `app.state.http_session` — один `aiohttp.ClientSession` на процесс; все клиенты
  принимают `session` в конструкторе и не создают свою, если она передана.

Не создавайте здесь новые `ClientSession`, семафоры или пулы процессов на задачу —
это ломает лимиты и утекает сокеты.

## 8. Настройки (`app/settings.py`)

`SERVICE_NAME`, `TRANSLATOR_ADDRESS`, `TRANSLATE_URI`, `DETECT_LANGUAGE_URL`,
`WEBHOOK_MANAGER_URL`, `WATCHTOWER_URL`, `WATCHTOWER_SHARED_PREFIX`,
`WATCHTOWER_SHARED_HOST`, `RESOURCE_MANAGER_URL`, `PARSER_WORKERS`,
`EXTERNAL_*_TIMEOUT_SECS`.

Отдельно: поле называется `TRANSALTOR_MAX_CONCURRENCY` (историческая опечатка),
в коде используйте property `settings.TRANSLATOR_MAX_CONCURRENCY`; env-переменная
читается по обоим именам.

## 9. Инварианты и подводные камни

- Имена объектов в хранилище **не префиксуются** (`prefix=""`), файл кладётся в
  корень персонального бакета под своим именем — одинаковые имена перезаписывают
  друг друга.
- `quote_fields=False` в `FormData` и отсутствие ручного URL-кодирования имени —
  осознанное решение против двойного кодирования кириллицы.
- `_apply_shared_prefix` превращает ссылку Watchtower в относительный
  frontend-путь, если задан `WATCHTOWER_SHARED_PREFIX`; иначе используется
  legacy-режим с `WATCHTOWER_SHARED_HOST`.
- `_export_to_word_sync` использует приватный `DoclingDocument._make_copy_with_refmode`
  — при апгрейде docling проверять в первую очередь это место.
- Задача живёт в `BackgroundTasks`: она не переживает рестарт процесса и не имеет
  общего таймаута. Долгие изменения в этом модуле держите в уме этот факт.
- Обратные кавычки в переводе заменяются на `*` перед записью в документ —
  иначе pandoc ломает разметку.

## 10. Тесты

- `tests/test_translator_v2_service.py` — конвейер целиком на моках
  (успешный путь, таймаут элемента, публикация прогресса).
- `tests/test_async_storage_clients.py` — `ResourceManagerService`,
  `WatchtowerService`, `WebhookManagerService` на фейковой сессии.

Запуск (pytest в окружении не установлен, тесты на `unittest`):

```bash
PYTHONPATH=app poetry run python -m unittest discover -s tests -t tests
```
