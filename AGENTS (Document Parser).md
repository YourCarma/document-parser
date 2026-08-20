# document-parser — карта сервиса для ИИ-агентов

Файл-ориентир по репозиторию целиком. Отвечает на вопросы: что это за сервис,
из чего состоит, куда идёт запрос, что нельзя ломать.

Детали асинхронного перевода — в отдельном файле
[`app/modules/translator/v2/AGENTS.md`](app/modules/translator/v2/AGENTS.md).

## 1. Что это

FastAPI-сервис, который превращает произвольные документы в Markdown / `.docx`
и переводит их. Ядро распознавания — [Docling](https://github.com/docling-project),
OCR по картинкам и «сложным» PDF — внешняя VLM с OpenAI-совместимым API.

Три прикладных сценария:

| Сценарий | Префикс | Режим | Результат |
| --- | --- | --- | --- |
| Parser V1 | `/api/v1/parser/parse/*` | синхронный | Markdown-строка, `.md`, `.docx` |
| Translator V1 | `/api/v1/parser/translator/*` | синхронный | переведённый Markdown, `.md`, `.docx` |
| Translator V2 | `/api/v2/parser/translator/file/word` | асинхронный | `task_id` + файлы в облаке пользователя |

Плюс служебные `GET /` и `GET /health`.

## 2. Дерево репозитория

```
app/
  main.py                     точка входа: lifespan, CORS, подключение роутеров
  settings.py                 pydantic-settings, читается из ENV_FILE (по умолчанию .env.dev)
  api/routers.py              список роутеров, которые монтирует main.py
  modules/
    parser/v1/                парсинг документов
      router.py               3 синхронных эндпоинта
      schemas.py              ParserRequest/ParserParams/ParserMods/FileFormats
      exceptions.py           HTTPException-наследники
      utils.py                save_file, delete_file, parse_document, run_in_process, convert_doc_to, is_supported_extension
      process_pool.py         ProcessPoolHolder: пересборка пула после гибели воркера
      abc/abc.py              ParserABC: чистка текста, to_utf8, общие поля
      abc/factory.py          ParserFactory: расширение -> класс парсера
      file_parsers/           по одному классу на семейство форматов
    translator/v1/            синхронный перевод (CustomModelTranslator)
      router.py, service.py, schemas.py, utils.py (post_request + retry), abc/abc.py
    translator/v2/            асинхронный перевод (TranslatorV2Service) + свой AGENTS.md
    watchtower/               клиент облачного хранилища (upload, download) + exceptions.py
    resource_manager/         клиент поиска персонального бакета пользователя
    webhook_manager/          клиент задач: create_task, update_progress, update_response_data, get_task
                              + cancellation.py (токены отмены задачи)
    metrics/                  наблюдаемость: OpenTelemetry, метрики, трейсы
                              otel.py (инициализация), instruments.py (все метрики),
                              stages.py (тайминги этапов), http.py (свой API и зависимости),
                              concurrency.py (семафор с учётом загрузки), registry.py
metrics/                      конфигурация наблюдаемости: коллектор, Prometheus, Tempo,
                              дашборд Grafana, правила алертов (см. metrics/README.md)
tests/                        unittest, без pytest
ml/                           локальные модели Docling (в git не хранится, монтируется в docker)
docs/                         drawio-схемы контекста
```

Пустые/служебные файлы, которые не несут логики: `app/modules/parser/v1/service.py`,
`app/modules/translator/v1/abc/factory.py`, `app/modules/pandoc.py` (разовый
загрузчик pandoc), `app/test_data/` (gitignored).

## 3. Поток запроса

### Синхронный парсинг

```
POST /api/v1/parser/parse/{text|file|file/word}
  │
  ├─ ParserRequest валидирует MIME по settings.ALLOWED_MIME_TYPES
  ├─ save_file(upload)                 -> tempfile.mkstemp, чтение чанками по 1 МБ
  ├─ run_in_process(parse_document, executor, params, mode, semaphore=parser_semaphore)
  │     └─ в отдельном процессе: ParserFactory(params).get_parser().parse(mode)
  └─ finally: delete_file(temp)        (для FileResponse — BackgroundTask на выходной файл)
```

### Синхронный перевод (V1)

То же самое, но `parse_document` вызывается с `ParserMods.TO_DOCLING`, и
полученный `DoclingDocument` уходит в `CustomModelTranslator.translate_docling()`:
детект языка (если `auto`) → перевод `TextItem` и ячеек таблиц батчами →
экспорт в нужный формат.

### Асинхронный перевод (V2)

Роутер создаёт задачу в `webhook_manager`, сразу отдаёт `task_id`/`key` и ставит
`run_translation_task` в `BackgroundTasks`. Подробности, шкала прогресса и
инварианты — в [`app/modules/translator/v2/AGENTS.md`](app/modules/translator/v2/AGENTS.md).

## 4. Как выбирается парсер

`ParserFactory.get_parser()` смотрит **только на расширение файла** (не на MIME):

| Расширения | Класс | Особенность |
| --- | --- | --- |
| `.jpg .jpeg .png .tiff .bmp .webp` | `ImageParser` | всегда идёт в VLM |
| `.pdf` | `PDFParser` | Docling + `ThreadedStandardPdfPipeline` |
| `.pdf` при `full_vlm_pdf_parse=true` | `PDFVLMParser` | весь PDF постранично в VLM |
| `.docx` | `DocParser` | Docling MsWord backend |
| `.doc .rtf` | `DocParser` | сначала `convert_doc_to()` через `soffice` в `.docx` |
| `.xlsx` / `.pptx` / `.html` | `XLSXParser` / `PPTXParser` / `HTMLParser` | |
| `.odt .ods .odp .epub .eml .xbrl .xml .txt .md` | наследники `DoclingFormatParser` | общий класс, отличаются `input_format` |
| прочее | — | `ContentNotSupportedError` (406) |

`ParserMods` — режим экспорта, общий для всех парсеров: `TO_TEXT` (Markdown-строка),
`TO_FILE` (`.md`), `TO_WORD` (`.docx` через pypandoc), `TO_DOCLING` (сам
`DoclingDocument` — используется переводчиками).

## 5. Конкурентность

Всё создаётся один раз в `lifespan` ([app/main.py](app/main.py)) и живёт в `app.state`:

- `executor` — `ProcessPoolHolder(PARSER_WORKERS)`, владелец `ProcessPoolExecutor`.
  Docling CPU-bound, поэтому процессы, а не потоки. При `BrokenProcessPool`
  `run_in_process` пересобирает пул и делает одну повторную попытку
  (`retries=1`); голый `Executor` тоже принимается, но не пересобирается.
- `parser_semaphore` — `TrackedSemaphore(PARSER_WORKERS)`, ограничивает очередь к пулу.
- `translation_semaphore` — `TrackedSemaphore(TRANSLATOR_MAX_CONCURRENCY)`, **общий на всё
  приложение** лимит одновременных запросов к сервису перевода.
- `TrackedSemaphore` — обычный `asyncio.Semaphore`, который дополнительно
  считает занятые слоты и ожидающих; отсюда берутся метрики загрузки.
- `http_session` — один `aiohttp.ClientSession` (`total=None`, но `connect`/`sock_read`
  из настроек, `TCPConnector(limit=EXTERNAL_HTTP_CONNECTION_LIMIT)`).

Правило: **не создавайте свои `ClientSession`, семафоры и пулы процессов внутри
обработчиков и сервисов.** Все клиенты умеют принимать `session` в конструкторе
и создают собственную только как fallback (это для тестов, не для прода).

Синхронный экспорт (pypandoc, `save_as_markdown`) оборачивается в
`asyncio.to_thread`, чтобы не блокировать event loop.

## 6. Внешние зависимости

| Что | Настройка | Без него не работает |
| --- | --- | --- |
| VLM (OpenAI-совместимый `/v1/chat/completions`) | `VLM_BASE_URL`, `VLM_MODEL_NAME`, `VLM_API_KEY` | `parse_images=true`, картинки, `full_vlm_pdf_parse` |
| Сервис перевода | `TRANSLATOR_ADDRESS` + `TRANSLATE_URI` | оба переводчика |
| Детектор языка | `DETECT_LANGUAGE_URL` | `source_language=auto` |
| `webhook_manager` | `WEBHOOK_MANAGER_URL` | Translator V2 |
| `watchtower` (хранилище) | `WATCHTOWER_URL` | Translator V2 |
| `resource_manager` | `RESOURCE_MANAGER_URL` | Translator V2 |
| LibreOffice (`soffice`) | системный пакет | `.doc`, `.rtf` |
| pandoc | системный пакет | любой экспорт `TO_WORD` |
| модели Docling | каталог `ml/` (`ML_DIR`) | офлайн-парсинг без похода в HuggingFace |

Ошибки внешних сервисов транслируются в `ServiceUnavailable` (503) или
`RetryableUpstreamError`; последний ретраится 3 раза с backoff `2**attempt`
(декоратор `retry` в `translator/v1/utils.py`).

## 7. Настройки

`app/settings.py`, pydantic-settings. Файл выбирается переменной `ENV_FILE`,
по умолчанию `.env.dev`; шаблон — `.env.example`.

Что стоит знать:

- `SERVICE_NAME` попадает в ключ задачи `user_id:SERVICE_NAME:task_id`.
- `PARSER_WORKERS` по умолчанию `multiprocessing.cpu_count()` — в контейнере
  почти всегда нужно задавать явно.
- Поле называется `TRANSALTOR_MAX_CONCURRENCY` (историческая опечатка).
  В коде используйте property `settings.TRANSLATOR_MAX_CONCURRENCY`;
  env читается по обоим написаниям через `AliasChoices`.
- Share-ссылок сервис не выдаёт: в `response_data` уходит object key внутри
  бакета. Ссылка протухает по сроку, ключ живёт столько же, сколько файл.
- `ALLOWED_MIME_TYPES` содержит `application/octet-stream`, поэтому MIME-проверка
  почти ничего не отсекает — реальный отбор идёт по расширению в фабрике.
- Таймауты и отмена: `TASK_TIMEOUT_SECS` (общий лимит задачи V2, держите его
  заведомо ниже `consumer_timeout` брокера), `PARSE_TIMEOUT_SECS` (этап парсинга),
  `SOFFICE_TIMEOUT_SECS` (одна конвертация LibreOffice),
  `TASK_CANCEL_CHECK_TTL_SECS` (кэш отрицательного ответа об отмене).
- `MAX_DOWNLOAD_FILE_SIZE_MB` — лимит для `WatchtowerService.download_file`;
  property `MAX_DOWNLOAD_FILE_SIZE_BYTES` отдаёт его в байтах.
- Наблюдаемость: `OTEL_ENABLED` (главный выключатель),
  `OTEL_EXPORTER_OTLP_ENDPOINT` (база OTLP/HTTP, без пути),
  `METRICS_HTTP_PORT` (Prometheus-эндпоинт на отдельном порту).
  Push и pull независимы, подробности — в `metrics/README.md`.

## 8. Запуск

```bash
poetry install --no-root
docling-tools models download --all -o ml     # один раз, модели в ml/
cd app && python main.py                      # берёт .env.dev

# или
docker build -t document-parser:latest .      # ml/ монтируется томом
```

Тесты (pytest в окружении нет, всё на `unittest`):

```bash
PYTHONPATH=app poetry run python -m unittest discover -s tests -t tests
```

Импорты в коде абсолютные от каталога `app` (`from modules...`, `from settings import settings`),
поэтому `PYTHONPATH=app` обязателен и при запуске, и при тестах.

## 9. Наблюдаемость

Код — в `app/modules/metrics/`, конфигурация стенда и дашборд — в `metrics/`
(там же таблица всех метрик). Главное для правок:

- Модуль **необязательный**. Пакеты `opentelemetry-*` могут отсутствовать, а
  `OTEL_ENABLED` по умолчанию `false` — тогда все инструменты становятся
  заглушками. Ни один вызов метрик не имеет права ронять обработку задачи.
- `setup_observability(app)` вызывается в `main.py` **на уровне модуля**, до
  старта приложения: авто-инструментация FastAPI добавляет мидлварь, а после
  старта стек мидлварей уже собран.
- Новые метрики заводятся только в `instruments.py`. Единица измерения — в
  имени (`_seconds`), поле `unit` пустое: иначе Prometheus-ридер и коллектор
  допишут суффикс каждый по-своему и запросы дашборда разъедутся.
- У каждой гистограммы обязаны быть свои границы в `HISTOGRAM_BUCKETS` —
  дефолтные заканчиваются на 10, а здесь всё измеряется тысячами секунд. Это
  проверяется тестом.
- В атрибуты нельзя класть `task_id`, `user_id`, имена файлов и сырые пути:
  каждое значение — отдельный временной ряд. Маршруты HTTP пишутся шаблоном
  роутера, незнакомые пути схлопываются в `unmatched`.
- Метрики исходящих вызовов снимает один `TraceConfig` на общей сессии из
  `AppRuntime`. Клиент, создавший свою сессию (fallback), в метрики не попадёт.
- Этапы перевода считает `StageTracker`: `enter()` на каждом переходе в
  `run_translation_task`, ровно один `finish()` в `finally`.
- `setup_observability(app)` инструментирует **каждый переданный `app`**, хотя
  провайдеры поднимает один раз на процесс. Так надо: `python main.py`
  импортирует модуль дважды — сначала как `__main__`, потом uvicorn по строке
  `"main:app"`, — и запросы обслуживает второй объект приложения. Общий флаг
  «уже настроено» оставил бы его без трейсов при работающих метриках.

## 10. Инварианты и подводные камни

- **Расширение важнее MIME.** Парсер выбирается по суффиксу временного файла,
  а суффикс берётся из имени, присланного клиентом. Меняя валидацию, не сломайте
  сохранение суффикса в `save_file`.
- **`parse_document` сам удаляет промежуточный файл конвертации.** Для `.doc`/`.rtf`
  фабрика подменяет `parser_params.file_path` на путь `.docx`; `parse_document`
  запоминает исходный путь до вызова фабрики и в `finally` удаляет только
  конвертированный. Исходный временный файл удаляет вызывающий роутер.
- **`ImageParser` и `PDFVLMParser` не вызывают `super().__init__()`** и принимают
  путь, а не `ParserParams`. У них нет `self.parser_params`, `self.image_mode`,
  `self.page_break_placeholder`. Флаги `parse_images` / `include_image_in_output`
  для них не применяются. Если добавляете использование этих полей в `ParserABC` —
  эти два класса упадут.
- **`TimeoutError` в `parser/v1/exceptions.py` перекрывает встроенный.** В
  `translator/v2/service.py` ловится именно встроенный (он же
  `asyncio.TimeoutError`) — и в деградации элемента, и в ветке общего таймаута.
  Импорт кастомного в этот модуль тихо сломает и то, и другое.
- **Исключения, пересекающие границу процесса, обязаны быть пиклюемыми.**
  У `HTTPException` пустой `args`, при распиковке он падает с `TypeError`.
  Поэтому таймаут конвертации внутри воркера — `ConversionTimeoutError`
  (обычный `Exception`), а `ProcessPoolUnavailable` (`HTTPException`) бросается
  только в родительском процессе.
- **Блок экспорта `match mode:` продублирован в 6 парсерах и в двух сервисах
  перевода.** Копии уже разошлись (`--wrap=none` только в переводчике, `to_utf8`
  для ячеек пропущен в `DocParser`). Правя экспорт, проверьте все копии или
  вынесите общий метод.
- **`_make_copy_with_refmode` — приватный метод `DoclingDocument`.** Используется
  в каждом `TO_WORD`. При апгрейде docling ломается в первую очередь он.
- **Обратные кавычки в переводе заменяются на `*`** перед записью — иначе pandoc
  ломает разметку.
- **Смерть воркера больше не ломает пул навсегда.** Docling может уронить процесс
  на битом файле; `ProcessPoolHolder.rebuild()` заменяет сломанный пул,
  `run_in_process` повторяет задачу один раз. Пересборка идемпотентна: параллельные
  вызовы с одним и тем же сломанным пулом пересоберут его ровно один раз.
- **`asyncio.timeout` не убивает воркер парсинга.** По `PARSE_TIMEOUT_SECS`
  отменяется только ожидание: слот `parser_semaphore` освобождается раньше, чем
  реально завершится процесс. Это принято осознанно, в логе остаётся
  `logger.error` с `task_id`.
- **Отмену нельзя затирать своими же апдейтами.** webhook_manager хранит отмену
  в том же поле `progress.status`, что и прогресс: `PATCH update_progress` со
  статусом `PROCESSING`, отправленный после нажатия «Отменить», стирает
  `CANCELLED` навсегда — задача доработает до конца, сколько её ни отменяй.
  Поэтому все промежуточные публикации (`_update`, прогресс перевода) сперва
  делают свежую проверку отмены (`is_cancelled(fresh=True)`, мимо TTL-кэша) и
  либо бросают `TaskCancelled`, либо молча пропускают запись. Терминальные
  публикации (`_publish_terminal`) этой проверки не делают. Окно гонки шириной
  в один запрос остаётся — закрыть его полностью можно только на стороне
  webhook_manager, запретив уход из `CANCELLED`.
- **Отмена проверяется на каждом элементе перевода**, а не только на границе
  батча: батч из `TRANSLATOR_MAX_CONCURRENCY` элементов с ретраями живёт долго.
  Батч при этом дожидается уже запущенных корутин (`gather(return_exceptions=True)`)
  — брошенные корутины дописали бы `PROCESSING` уже после публикации `CANCELLED`.
  Парсинг своих контрольных точек не имеет, поэтому `_await_or_cancel` опрашивает
  отмену параллельно ожиданию (шаг — `TASK_CANCEL_CHECK_TTL_SECS`, но не чаще
  `_MIN_CANCEL_POLL_SECS`).
- **Задачи V2 живут в `BackgroundTasks`** — не переживают рестарт процесса и не
  ограничены по количеству; общий лимит времени задаёт `TASK_TIMEOUT_SECS`.
- **Аутентификации нет.** `X-User-ID` принимается на веру, сервис рассчитан на
  работу за шлюзом.

## 11. Тесты

| Файл | Что покрывает |
| --- | --- |
| `test_parser_factory_formats.py` | соответствие расширение → класс парсера |
| `test_parser_file_utils.py` | `save_file` / `delete_file` / очистка конвертации |
| `test_pdf_parser_options.py` | опции пайплайна PDF |
| `test_txt_parser_docling.py` | `DoclingFormatParser` |
| `test_translator_v1_service.py` | батчи перевода, ретраи, экспорт |
| `test_translator_v2_service.py` | конвейер V2 целиком на моках |
| `test_async_storage_clients.py` | `Watchtower` / `Webhook` / `ResourceManager` на фейковой сессии, ретраи, `get_task`, `download_file` |
| `test_process_pool.py` | `ProcessPoolHolder` и повтор `run_in_process` после `BrokenProcessPool` |
| `test_soffice_conversion.py` | таймаут LibreOffice и убийство группы процессов |
| `test_translator_v2_cancellation.py` | токены отмены и остановка конвейера V2 |

Внешние сервисы, docling и VLM в тестах не поднимаются — всё на моках и
фейковых сессиях. Новые тесты пишите в том же стиле (`unittest.IsolatedAsyncioTestCase`).
