# Document Parser

> FastAPI + Docling + VLM (Optional) app

## 1. Overview

![Контекст сервиса](./docs/Context.drawio.png)

## Что умеет сервис

- Парсить документы в Markdown-текст.
- Возвращать результат как `.md` или `.docx`.
- Переводить документ синхронно через `translator v1`.
- Запускать асинхронный перевод с прогрессом через `translator v2`.
- Брать задачи перевода из очереди RabbitMQ (см. «Режим очереди»).

Поддерживаемые форматы:

| **Images** | **Documents** | **Presentation** | **XLSX** | **PDF** | **TXT** |
| :--------------: | :-----------------: | :--------------------: | :------------: | :-----------: | :-----------: |
|       .png       |        .docx        |         .pptx         |     .xlsx     |     .pdf     |     .txt     |
|      .jpeg      |                    |                        |                |              |              |
|       .jpg       |                    |                        |                |              |              |
|       .bmp       |                    |                        |                |              |              |
|      .tiff      |                    |                        |                |              |              |
|      .webp      |                    |                        |                |              |              |

## Архитектура и поток данных

Сервис состоит из нескольких прикладных модулей:

1. `Python 3.12`
2. `Docker`
3. ❗❗❗ Access to `VLM` for Image parsing (**external** or **self-hosted** `VLM`).
4. `Poetry`

❗❗❗ Download Docling models in root dir `ml` using:
`docling-tools models download --all -o ml` this will be yours volume of `docker`

Дополнительные схемы и runbooks:

* `SERVICE_NAME`- the name of service, e.g. `document-parser`
* `HOST` - service hosting IP, e.g. `0.0.0.0`
* `PORT` - service hosting PORT, e.g. `1338`
* `VLM_BASE_URL` - VLM API URL, e.g. `0.0.0.0:8000`
* `VLM_MODEL_NAME` - VLM model name, e.g. `Qwen2.5-VL`,
* `VLM_API_KEY` - API-KEY auth for model, e.g. `no-key-required`

## Внешние зависимости

Для полноценной работы сервис зависит от внешней инфраструктуры:

1. `poetry shell`
2. `poetry install --no-root`
3. `docling-tools models download --all -o ml`
4. `cd app`
5. `python main.py`

Без этих сервисов часть сценариев будет недоступна. Например, `translator v2` не сможет завершить задачу без `webhook_manager`, `watchtower` и `resource_manager`.

## Быстрый старт для разработчика

1. In the root directory command: `docker build -t document-parser:latest .`
2. Be sure, that you have ml models in dir `ml` as a volume in `docker`

- `Python 3.12`
- `Poetry`
- `Docker`
- `LibreOffice`
- `pandoc`
- Доступ к VLM, если нужен OCR по изображениям

Parser factory consists of **5** parser types, which having own processing algorithms, based on **Docling** and **VLM**:

* `ImageParser`
* `DocParser`
* `PPTXParser`
* `XLSXParser`
* `PDFParser`

Минимально важные переменные:

- `SERVICE_NAME` — имя сервиса, участвует в ключах задач.
- `HOST`, `PORT` — адрес и порт FastAPI.
- `ML_DIR` — каталог локальных моделей Docling.
- `PARSER_WORKERS` — количество процессов для CPU-bound парсинга.
- `VLM_BASE_URL`, `VLM_MODEL_NAME`, `VLM_API_KEY`, `VLM_MAX_TOKENS`, `VLM_TIMEOUT_SECS` — настройки VLM.
- `TRANSLATOR_ADDRESS`, `TRANSLATE_URI` — адрес сервиса перевода.
- `DETECT_LANGUAGE_URL` — адрес сервиса определения языка.
- `WEBHOOK_MANAGER_URL`, `WATCHTOWER_URL`, `RESOURCE_MANAGER_URL` — интеграционные сервисы.
- `TRANSALTOR_MAX_CONCURRENCY` — ограничение параллельных запросов к переводчику.

Пример запуска по умолчанию использует `.env.dev`. Для production-сценария можно задать `ENV_FILE=/path/to/.env.production`.

## Режим очереди (RabbitMQ)

Кроме HTTP сервис умеет брать задачи из очереди. Оба входа ведут в один и тот же
конвейер перевода — различается только источник файла и то, кто создаёт задачу.

Включается флагом, один и тот же образ работает в двух ролях:

```dotenv
BROKER_ENABLED=true      # в деплойменте воркера
BROKER_ENABLED=false     # в деплойменте API
```

Формат сообщения:

```json
{
  "task_id": "5fb0b68c-2259-47d8-8e72-3dc517ac6d4d",
  "user_id": "1234",
  "task_type": "document-parser.translate",
  "payload": {
    "file_path": "documents/report.pdf",
    "source_language": "auto",
    "target_language": "ru"
  }
}
```

`file_path` — object key внутри бакета пользователя. Бакет в сообщении не
передаётся: он всегда определяется по `user_id` через `resource_manager`, иначе
продюсер мог бы записать файл в чужое хранилище. Файл скачивается из
`watchtower`, результат кладётся туда же, в `output_prefix`.
Прогресс и итог публикуются в `webhook_manager` по ключу
`{user_id}:{SERVICE_NAME}:{task_id}`, в очередь ответ не пишется.

### Что важно знать про топологию

- **Exchange и рабочую очередь сервис не создаёт** — их владелец `task_gateway`.
  Если их нет, воркер осознанно падает на старте с явным сообщением.
- Свои `document-parser.dlx`, `document-parser.dlq` и `document-parser.retry`
  сервис объявляет сам.
- У рабочей очереди нет DLX, поэтому копии сообщений в DLQ **публикует сам
  сервис**. Отсюда следствие для эксплуатации: DLQ наполняется только работающим
  подом, и её непустота — сигнал о невыполненных задачах. Сервис проверяет её
  раз в `RMQ_DLQ_CHECK_INTERVAL_SECS` и пишет в лог, а `GET /health` отдаёт
  глубину в поле `broker.dlq_depth`.

### `consumer_timeout`

Брокер по умолчанию разрывает канал, если сообщение не подтверждено за 30 минут,
а перевод большого документа идёт дольше. На брокере должно стоять
`consumer_timeout = 3600000` (60 минут), в сервисе — `TASK_TIMEOUT_SECS=3000`
(50 минут) и `RMQ_ACK_DEADLINE_SECS=3600`. Готовый конфиг —
`deploy/rabbitmq/rabbitmq.conf`.

### Локальная разработка

```bash
docker compose -f docker-compose.dev.yaml up -d     # брокер с нужным consumer_timeout
PYTHONPATH=app python3 -m unittest discover -s tests -t tests
```

Management UI — http://localhost:15672 (guest/guest). Команды для создания
«гейтвейной» топологии вручную — в шапке `docker-compose.dev.yaml`.

### Проверка состояния

```bash
curl -s localhost:1338/health | jq
```

`GET /health` отдаёт **503**, если консюмер включён, но не потребляет: под,
который молча не разбирает очередь, для оркестратора должен выглядеть больным.

⚠️ Это **readiness**, а не liveness. На время переподключения к брокеру ручка
отдаёт 503 на секунду-две. Повесив её на liveness-пробу, вы получите
перезапуск пода с 50-минутным переводом из-за секундного сетевого блипа.

Подробности архитектуры — `docs/rabbitmq-integration.md`, инварианты модуля —
`app/modules/broker/AGENTS.md`.

#### 1. Parsing documents

```rust
POST /v1/parser/parse?parse_images=false&include_image_in_output=false
```

Query params:

1. `parse_images` - parse internal document images with VLM (need access to VLM, may take more time)
2. `include_image_in_output` - inject internal document images to output `Markdown` as `base64` (may increase output size)

 ![Include_Images](/docs/Include_images.png)
 ![Parse_Images](/docs/parse_images.png)
