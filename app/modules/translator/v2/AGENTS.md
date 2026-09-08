# Translator V2 — асинхронный перевод документов

Файл-ориентир для ИИ-агентов, работающих с `app/modules/translator/v2/`.
Описывает, что делает модуль, с кем общается, какие инварианты нельзя ломать.

## 1. Назначение

`TranslatorV2Service` — фоновый конвейер, который принимает загруженный
документ, парсит его в `DoclingDocument`, поэлементно переводит текст через
внешний сервис перевода, собирает результат в `.docx`, кладёт оригинал и перевод
в облачное хранилище пользователя и публикует прогресс задачи.

Отличие от `translator/v1`: v1 синхронный (результат возвращается в том же HTTP-
ответе), v2 асинхронный и **доступен только из очереди** — задачу ставит клиент
через `task_gateway`, а прогресс опрашивает в `webhook_manager`. Своего
HTTP-эндпоинта у v2 больше нет: `router.py` удалён вместе с
`POST /api/v2/parser/translator/file/word`.

## 2. Файлы модуля

| Файл | Роль |
| --- | --- |
| `service.py` | `TranslatorV2Service` — весь фоновый конвейер. |
| `schemas.py` | `TranslatorResponseData` (снимок состояния задачи), `TranslationOutcome` (результат этапа перевода). |
| `sources.py` | Откуда берётся исходный файл: `WatchtowerSource` для очереди, `LocalUploadSource` для локального пути. |
| `exceptions.py` | `TaskTimeout` — превышен лимит времени этапа. |

## 3. Внешние зависимости

Все клиенты — тонкие обёртки над `aiohttp`, живут в `app/modules/`:

- `resource_manager.service.ResourceManagerService` — `get_user_bucket(user_id)`.
  Ходит в `GET /api/v1/resource/` с заголовком `x-user-id`, ищет **ровно один**
  ресурс с `resource_type == "Document"` и `resource_owner == "User"`, возвращает
  его `id` как bucket. Несколько персональных ресурсов → `ValueError`.
- `watchtower.service.WatchtowerService` — `upload_file(bucket, local_path, filename)`
  `download_file(bucket, file_path, dest_dir)`. Загрузка идёт multipart-ом с
  `quote_fields=False`: Watchtower сохраняет имя из multipart буквально, поэтому
  предварительное URL-кодирование кириллицы ломает имя объекта (см. комментарии
  в коде — это не случайность, не «чинить»).
- `webhook_manager.service.WebhookManagerService` — `create_task`,
  `update_progress`, `update_response_data`, `get_task`. Ключ задачи —
  `user_id:SERVICE_NAME:task_id`. Первые три метода ретраятся внутри клиента
  (3 попытки, backoff `0.5 * 2**attempt`) на 5xx и сетевых сбоях; `get_task`
  не ретраится намеренно.
- `webhook_manager.cancellation` — `CancellationTokenABC`, `NullCancellationToken`,
  `WebhookCancellationToken`, `TaskCancelled`. Токен опрашивает `get_task`
  не чаще чем раз в `TASK_CANCEL_CHECK_TTL_SECS`; отмена «липкая», а ошибка
  опроса **никогда** не считается отменой. `is_cancelled(fresh=True)` обходит
  TTL-кэш — так спрашивают там, где сразу после ответа пишут статус задачи.
- `translator.v1.service.CustomModelTranslator` — `detect_language(text)` и
  `translate_element_limited(text)` (перевод одного элемента под семафором).
- `parser.v1.utils.parse_document` / `run_in_process` — парсинг в отдельном
  процессе через `app.state.executor` (`ProcessPoolHolder`).

Внешние HTTP-сервисы: сервис перевода (`TRANSLATOR_ADDRESS + TRANSLATE_URI`),
детектор языка (`DETECT_LANGUAGE_URL`), `webhook_manager`, `watchtower`,
`resource_manager`. Без них v2-сценарий не завершится.

## 4. Поток выполнения

```
task_gateway POST /api/v1/broker/publish  (x-user-id, task_type, payload)
  │
  ├─ гейтвей создаёт задачу в webhook_manager -> task_key = "user:service:task_id"
  └─ гейтвей публикует сообщение в очередь
       │
       └─ консюмер -> TranslateHandler -> run_translation_task

run_translation_task (фон):
  1. resolve user bucket      resource_manager.get_user_bucket
  2. upload original file     watchtower.upload_file                     -> 5..10 %
  3. parse document           run_in_process(parse_document, TO_DOCLING) -> 15 %
                              под asyncio.timeout(PARSE_TIMEOUT_SECS)
  4. translate document       _translate_with_progress                   -> 15..93 %
  5. upload translated file   watchtower.upload_file                     -> 95 %
  6. READY                                                                    -> 100 %
  finally: удалить временный исходник и временный .docx
```

Всё тело обёрнуто в `asyncio.timeout(TASK_TIMEOUT_SECS)`. Метод возвращает
терминальный статус (`READY | ERROR | CANCELLED`) и не бросает исключений,
кроме `asyncio.CancelledError`.

Перед каждым тяжёлым этапом вызывается `cancellation.raise_if_cancelled(stage)`:
до старта, после получения бакета, после загрузки оригинала, после парсинга,
перед каждым батчем перевода, **на каждом элементе перевода** и после перевода.
Внутри `except`/`finally` и после терминальной публикации отмену не проверяем.

Скачивание исходника и парсинг своих контрольных точек не имеют (первое едет
минутами, второй идёт в чужом процессе), поэтому их ожидание завёрнуто в
`_await_or_cancel`: он опрашивает отмену параллельно
ожиданию (шаг — `TASK_CANCEL_CHECK_TTL_SECS`, но не чаще `_MIN_CANCEL_POLL_SECS`)
и бросает `TaskCancelled`, сняв ожидание. Воркер, как и при `PARSE_TIMEOUT_SECS`,
дорабатывает сам.

**Промежуточные публикации не имеют права затирать отмену.** webhook_manager
держит отмену в том же поле `progress.status`, что и прогресс: `PROCESSING`,
отправленный после нажатия «Отменить», стирает `CANCELLED` навсегда — задача
доработает до конца, а следующий опрос уже ничего не увидит. Поэтому `_update`
и публикатор прогресса перевода сперва делают `is_cancelled(fresh=True)`:
`_update` бросает `TaskCancelled`, публикатор прогресса молча пропускает запись,
но двигает `self._last_progress` (его отдаст терминальный `CANCELLED`).
`_publish_terminal` эту проверку не делает — иначе не опубликовать сам `CANCELLED`.
Окно гонки шириной в один запрос остаётся: закрыть его полностью можно только
на стороне webhook_manager, запретив уход из `CANCELLED`.

### Порядок веток обработки (ломать нельзя)

| # | Ветка | progress | status | text_status | error |
| --- | --- | --- | --- | --- | --- |
| 1 | `TaskCancelled` | `self._last_progress` | `CANCELLED` | «Задача отменена» | не трогаем |
| 2 | `asyncio.CancelledError` | ничего не публикуем, `raise` | | | |
| 3 | `TimeoutError` / `TaskTimeout` | 0 | `ERROR` | «Превышено время обработки» | то же |
| 4 | `Exception` | 0 | `ERROR` | `_stage_to_user_message(stage)` | то же |
| — | успех | 100 | `READY` | «Готово» либо счётчик непереведённых | None |

`asyncio.CancelledError` наследует `BaseException` и в `except Exception` не
попадёт, но ветка написана явно, чтобы правка не сделала её недостижимой молча.

### Внутри `_translate_with_progress`

1. Если `source_language == "auto"` — взять первые 3 непустых `TextItem`,
   вызвать `detect_language`. Не определился → `LanguageNotSupported`.
2. Обойти `docling_doc.iterate_items()`: собрать `TextItem` (сохранив
   `element.orig = element.text`) и ячейки `TableItem.data.table_cells`.
3. Перевести батчами размером `TRANSLATOR_MAX_CONCURRENCY`
   (`_translate_in_batches`) — сначала тексты, потом ячейки. Батчи нужны, чтобы
   не создавать корутину на каждый элемент документа сразу. `gather` вызывается
   с `return_exceptions=True`, и первое исключение бросается уже после того, как
   батч догрёб: брошенные на полпути корутины дописали бы `PROCESSING` поверх
   опубликованного `CANCELLED`.
4. Каждый элемент переводится через `translate_tracked`: `TimeoutError`,
   `RetryableUpstreamError`, `HTTPException`, `ValueError` и `aiohttp.ClientError`
   **не роняют задачу** — в документ подставляется оригинал с суффиксом
   `" (ошибка запроса, переведите вручную)"`, счётчик непереведённых растёт.
   `TaskCancelled` пробрасывается: отмена — не деградация элемента. Проверка
   отмены стоит **до** `try`: отменённый элемент не считается обработанным.
5. Прогресс публикуется примерно 20 раз за задачу (`update_every = total // 20`),
   под `asyncio.Lock`, ошибки публикации прогресса только логируются.
6. Экспорт: `DoclingDocument -> markdown -> pypandoc -> .docx` в
   `asyncio.to_thread` (`_export_to_word_sync`).
7. Возвращается `TranslationOutcome(file_path, untranslated_count)`, а не строка.

## 5. Модель прогресса и статусов

`webhook_manager` хранит два поля: числовой `progress` + `status`
(`PENDING/AWAITING/PROCESSING/READY/ERROR/CANCELLED`) и JSON `response_data` = `TranslatorResponseData`:

```json
{
  "original_language": "en",
  "target_language": "ru",
  "original_file": "<object key>",
  "translated_file": "<object key>",
  "text_status": "Перевожу... 45/120 элементов",
  "error": null
}
```

Шкала: 5 → 10 → 15 → (15..93 перевод) → 95 → 100. При ошибке `progress = 0`,
`status = ERROR`. При отмене публикуется `self._last_progress` — последний
успешно опубликованный прогресс, чтобы не обнулять шкалу в UI. Отсюда правило:
**один экземпляр `TranslatorV2Service` — на одну задачу**.

## 6. Обработка ошибок

- `current_stage` — строковый маркер текущего этапа (константы `STAGE_*`,
  английские: значение попадает в логи); в `except` он переводится в
  пользовательское сообщение через `_STAGE_MESSAGES` / `_stage_to_user_message`.
  Технический текст исключения уходит только в лог, наружу идёт этапное сообщение.
- `_update` (промежуточные публикации 5/10/15/95) — **best-effort**: любое
  исключение только логируется, уронить почти сделанную задачу оно не может.
  При успехе обновляет `self._last_progress`.
- `_publish_terminal` — терминальная публикация: ретраи внутри клиента,
  наружу исключений не пробрасывает, возвращает `bool`.
- `_cleanup_files` всегда удаляет исходный временный файл и, если он создан,
  переведённый `.docx` — в том числе под `CancelledError`, переходя на
  синхронный `Path.unlink(missing_ok=True)`.

## 7. Конкурентность и ресурсы

Создаются в `app/main.py` (lifespan) и прокидываются в сервис через `router.py`:

- `app.state.executor` — `ProcessPoolHolder(PARSER_WORKERS)` для парсинга;
  `run_in_process` пересобирает пул после `BrokenProcessPool` и повторяет
  задачу один раз.
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
`WEBHOOK_MANAGER_URL`, `WATCHTOWER_URL`, `RESOURCE_MANAGER_URL`, `PARSER_WORKERS`,
`EXTERNAL_*_TIMEOUT_SECS`, `TASK_TIMEOUT_SECS`, `PARSE_TIMEOUT_SECS`,
`TASK_CANCEL_CHECK_TTL_SECS`.

Отдельно: поле называется `TRANSALTOR_MAX_CONCURRENCY` (историческая опечатка),
в коде используйте property `settings.TRANSLATOR_MAX_CONCURRENCY`; env-переменная
читается по обоим именам.

## 9. Инварианты и подводные камни

- Имена объектов в хранилище **не префиксуются** (`prefix=""`), файл кладётся в
  корень персонального бакета под своим именем — одинаковые имена перезаписывают
  друг друга.
- `quote_fields=False` в `FormData` и отсутствие ручного URL-кодирования имени —
  осознанное решение против двойного кодирования кириллицы.
- `_export_to_word_sync` использует приватный `DoclingDocument._make_copy_with_refmode`
  — при апгрейде docling проверять в первую очередь это место.
- Задача живёт внутри обработки сообщения: рестарт пода её не теряет —
  неподтверждённое сообщение вернётся в очередь и доиграется другой репликой.
  Общий лимит времени задаёт `TASK_TIMEOUT_SECS`, и он обязан быть заметно
  ниже `consumer_timeout` брокера.
- `asyncio.timeout` **не убивает воркер парсинга**: по `PARSE_TIMEOUT_SECS`
  отменяется только ожидание, слот `parser_semaphore` освобождается раньше, чем
  реально завершится процесс. Принято осознанно, в лог пишется `logger.error`
  с `task_id`.
- В этом модуле запрещено импортировать кастомный `TimeoutError` из
  `modules.parser.v1.exceptions` — он перекроет встроенный и сломает и
  деградацию элемента, и ветку таймаута.
- Обратные кавычки в переводе заменяются на `*` перед записью в документ —
  иначе pandoc ломает разметку.

## 10. Тесты

- `tests/test_translator_v2_service.py` — конвейер целиком на моках
  (успешный путь, таймаут элемента, публикация прогресса).
- `tests/test_async_storage_clients.py` — `ResourceManagerService`,
  `WatchtowerService` (включая `download_file`), `WebhookManagerService`
  (ретраи, `get_task`) на фейковой сессии.
- `tests/test_translator_v2_cancellation.py` — токены отмены и остановка
  конвейера на разных этапах.

Запуск (pytest в окружении не установлен, тесты на `unittest`):

```bash
PYTHONPATH=app poetry run python -m unittest discover -s tests -t tests
```
