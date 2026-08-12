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
      utils.py                save_file, delete_file, parse_document, run_in_process, convert_doc_to
      abc/abc.py              ParserABC: чистка текста, to_utf8, общие поля
      abc/factory.py          ParserFactory: расширение -> класс парсера
      file_parsers/           по одному классу на семейство форматов
    translator/v1/            синхронный перевод (CustomModelTranslator)
      router.py, service.py, schemas.py, utils.py (post_request + retry), abc/abc.py
    translator/v2/            асинхронный перевод (TranslatorV2Service) + свой AGENTS.md
    watchtower/               клиент облачного хранилища (upload, sharelink)
    resource_manager/         клиент поиска персонального бакета пользователя
    webhook_manager/          клиент задач: create_task, update_progress, update_response_data
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

- `executor` — `ProcessPoolExecutor(PARSER_WORKERS)`. Docling CPU-bound, поэтому
  процессы, а не потоки.
- `parser_semaphore` — `Semaphore(PARSER_WORKERS)`, ограничивает очередь к пулу.
- `translation_semaphore` — `Semaphore(TRANSLATOR_MAX_CONCURRENCY)`, **общий на всё
  приложение** лимит одновременных запросов к сервису перевода.
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
| `watchtower` (хранилище) | `WATCHTOWER_URL`, `WATCHTOWER_SHARED_*` | Translator V2 |
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
- `WATCHTOWER_SHARED_PREFIX` задаёт относительный frontend-префикс для share-ссылок
  (например `/api/gateway`); если пусто — работает legacy-режим с
  `WATCHTOWER_SHARED_HOST`.
- `ALLOWED_MIME_TYPES` содержит `application/octet-stream`, поэтому MIME-проверка
  почти ничего не отсекает — реальный отбор идёт по расширению в фабрике.

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

## 9. Инварианты и подводные камни

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
  `translator/v2/service.py` в `except (TimeoutError, RetryableUpstreamError)`
  ловится именно встроенный (он же `asyncio.TimeoutError`). Импорт кастомного
  в этот модуль тихо сломает fallback «оставить оригинал при таймауте».
- **Блок экспорта `match mode:` продублирован в 6 парсерах и в двух сервисах
  перевода.** Копии уже разошлись (`--wrap=none` только в переводчике, `to_utf8`
  для ячеек пропущен в `DocParser`). Правя экспорт, проверьте все копии или
  вынесите общий метод.
- **`_make_copy_with_refmode` — приватный метод `DoclingDocument`.** Используется
  в каждом `TO_WORD`. При апгрейде docling ломается в первую очередь он.
- **Обратные кавычки в переводе заменяются на `*`** перед записью — иначе pandoc
  ломает разметку.
- **Смерть воркера ломает пул до рестарта.** Docling может уронить процесс на
  битом файле; `ProcessPoolExecutor` после этого отдаёт `BrokenProcessPool` на
  все последующие задачи. Восстановления сейчас нет.
- **Задачи V2 живут в `BackgroundTasks`** — не переживают рестарт процесса, не
  имеют общего таймаута и не ограничены по количеству.
- **Аутентификации нет.** `X-User-ID` принимается на веру, сервис рассчитан на
  работу за шлюзом.

## 10. Тесты

| Файл | Что покрывает |
| --- | --- |
| `test_parser_factory_formats.py` | соответствие расширение → класс парсера |
| `test_parser_file_utils.py` | `save_file` / `delete_file` / очистка конвертации |
| `test_pdf_parser_options.py` | опции пайплайна PDF |
| `test_txt_parser_docling.py` | `DoclingFormatParser` |
| `test_translator_v1_service.py` | батчи перевода, ретраи, экспорт |
| `test_translator_v2_service.py` | конвейер V2 целиком на моках |
| `test_async_storage_clients.py` | `Watchtower` / `Webhook` / `ResourceManager` на фейковой сессии |

Внешние сервисы, docling и VLM в тестах не поднимаются — всё на моках и
фейковых сессиях. Новые тесты пишите в том же стиле (`unittest.IsolatedAsyncioTestCase`).
