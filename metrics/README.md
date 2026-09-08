# Наблюдаемость document-parser

Здесь лежит всё, что не является кодом сервиса: конфигурация OTLP-коллектора,
Prometheus, Tempo, дашборд Grafana и правила алертов. Код инструментации —
в [`app/modules/metrics/`](../app/modules/metrics/).

```
metrics/
  docker-compose.observability.yaml   локальный стенд: коллектор, Prometheus, Tempo, Grafana
  otel-collector.yaml                 приём OTLP, раздача метрик Prometheus, трейсы в Tempo
  prometheus.yml                      два скрейпа: коллектор и сам сервис
  tempo.yaml                          однобинарный Tempo для трейсов
  alerts/document-parser.rules.yml    правила алертов Prometheus
  grafana/provisioning/               датасорсы и провижининг дашбордов
  grafana/dashboards/                 дашборд «Document Parser»
```

## Быстрый старт

```bash
docker compose -f metrics/docker-compose.observability.yaml up -d
OTEL_ENABLED=true PYTHONPATH=app python3 app/main.py
```

* Grafana — <http://localhost:3000>, дашборд `document-parser / Document Parser`
  (вход не спрашивается);
* Prometheus — <http://localhost:9090>, там же вкладка Alerts;
* сырые метрики сервиса — <http://localhost:9464/metrics>.

В переменной дашборда **Job** выберите одно значение: сервис виден и через
коллектор (`sova/document-parser`), и прямым скрейпом
(`document-parser-direct`). Это одни и те же метрики, и сумма по обоим job
посчитает всё дважды.

Остановить стенд: `docker compose -f metrics/docker-compose.observability.yaml down`
(с `-v`, если нужно стереть накопленные данные).

## Как подключён OTLP

Сервис инициализирует OpenTelemetry в `app/main.py` **до старта приложения** —
авто-инструментация FastAPI добавляет мидлварь, а после старта стек мидлварей
уже собран. Инициализация целиком в `app/modules/metrics/otel.py`.

Транспорт — OTLP over HTTP/protobuf. Адрес задаётся базой без пути, пути
сигналов дописываются сами:

| Переменная | Смысл |
| --- | --- |
| `OTEL_ENABLED` | Главный выключатель. `false` — весь модуль превращается в заглушки |
| `OTEL_METRICS_ENABLED` | Push метрик в коллектор |
| `OTEL_TRACES_ENABLED` | Push трейсов в коллектор |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | База OTLP/HTTP, например `http://otel-collector:4318` |
| `OTEL_EXPORTER_OTLP_HEADERS` | Заголовки в формате `k=v,k2=v2` — для Grafana Cloud и прочих, где нужен токен |
| `OTEL_METRIC_EXPORT_INTERVAL_MS` | Период отправки метрик, по умолчанию 15 000 |
| `OTEL_TRACES_SAMPLER_RATIO` | Доля трейсов: 1.0 локально, 0.1–0.3 в проде |
| `METRICS_HTTP_ENABLED` / `METRICS_HTTP_PORT` | Prometheus-эндпоинт на отдельном порту (9464) |
| `DEPLOY_ENVIRONMENT` | Попадает в `deployment.environment` — разделяет dev и прод в одном бэкенде |

Два пути экспорта независимы: можно оставить только push (`METRICS_HTTP_ENABLED=false`),
только pull (`OTEL_METRICS_ENABLED=false`) или оба сразу.

### Подключение к готовому коллектору контура

Менять в сервисе нечего — только адрес:

```env
OTEL_ENABLED=true
OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector.observability:4318
DEPLOY_ENVIRONMENT=production
OTEL_TRACES_SAMPLER_RATIO=0.2
```

Для Grafana Cloud или другого приёмника с авторизацией:

```env
OTEL_EXPORTER_OTLP_ENDPOINT=https://otlp-gateway-prod-eu-west-2.grafana.net/otlp
OTEL_EXPORTER_OTLP_HEADERS=Authorization=Basic <base64(instanceID:token)>
```

Если коллектора по адресу нет, экспортёр не падает, но пишет в лог повторные
попытки — включать `OTEL_ENABLED` имеет смысл только вместе с рабочим адресом.

### Скрейп вместо push

Prometheus-эндпоинт живёт на отдельном порту и не зависит от занятости
приложения. В Kubernetes:

```yaml
metadata:
  annotations:
    prometheus.io/scrape: 'true'
    prometheus.io/port: '9464'
    prometheus.io/path: '/metrics'
```

При нескольких uvicorn-воркерах в одном поде порт займёт первый — остальные
напишут в лог, что pull-эндпоинт выключен, и продолжат работать. Сервис
рассчитан на один процесс на под, для нескольких реплик используйте push.

## Метрики

Имена в OTLP записаны через точки, в Prometheus точки заменяются на
подчёркивания, а счётчики получают суффикс `_total`. Единица измерения зашита
в имя (`_seconds`), поле `unit` пустое — иначе экспортёры дописывали бы суффикс
единицы по-своему, и запросы дашборда разъезжались бы между push и pull.

| Prometheus | Тип | Атрибуты | О чём |
| --- | --- | --- | --- |
| `document_parser_broker_messages_total` | counter | `outcome`, `task_type`, `error_type` | Исход каждого сообщения: `processed`, `retried`, `dlq`, `acked`, `requeued`, `interrupted` |
| `document_parser_broker_processing_duration_seconds` | histogram | `outcome`, `task_type` | От получения сообщения до ack/nack |
| `document_parser_broker_inflight` | gauge | — | Сообщений в обработке прямо сейчас |
| `document_parser_broker_dlq_depth` | gauge | `queue` | Глубина DLQ по данным сторожа, `-1` — опрос не удался |
| `document_parser_broker_consuming` | gauge | `queue` | 1 — подписка активна |
| `document_parser_broker_connected` | gauge | `queue` | 1 — соединение с брокером живо |
| `document_parser_broker_topology_ready` | gauge | `object` | Готовность `dlq` и `retry` |
| `document_parser_tasks_total` | counter | `status`, `stage`, `error_type`, `source` | Завершённые задачи перевода |
| `document_parser_task_duration_seconds` | histogram | `status`, `source` | Полное время задачи |
| `document_parser_task_stage_duration_seconds` | histogram | `stage`, `outcome`, `source` | Время этапа конвейера |
| `document_parser_translate_items_total` | counter | `result` | Элементы документа: `translated` / `untranslated` |
| `document_parser_dependency_requests_total` | counter | `dependency`, `method`, `status` | Вызовы внешних сервисов |
| `document_parser_dependency_duration_seconds` | histogram | `dependency`, `method`, `outcome` | Время ответа внешнего сервиса |
| `document_parser_http_requests_total` | counter | `method`, `route`, `status` | Запросы к своему API |
| `document_parser_http_duration_seconds` | histogram | `method`, `route`, `outcome` | Время обработки запроса |
| `document_parser_semaphore_in_use` / `_waiting` / `_capacity` | gauge | `semaphore` | Загрузка `parser` и `translation` |
| `document_parser_process_pool_generation` | gauge | `pool` | Растёт на каждой пересборке пула после гибели воркера |

`status` задачи: `ready`, `error`, `timeout`, `cancelled` (отмена пользователем),
`interrupted` (SIGTERM). `stage` — этапы из `TranslatorV2Service`:
`init`, `resolve user bucket`, `fetch source file`, `upload original file`,
`parse document`, `translate document`, `upload translated file`.

Атрибуты намеренно ограничены: ни `task_id`, ни `user_id`, ни имена файлов в
метрики не попадают — каждое их значение стало бы отдельным временным рядом.
Эти данные ищутся в трейсах и логах.

## Трейсы

Авто-инструментация покрывает FastAPI (входящие запросы), aiohttp (все
исходящие вызовы) и aio-pika (потребление сообщений). Поверх этого
`StageTracker` открывает по спану на каждый этап перевода, так что в Tempo
видно, где именно задача провела время.

`/health` и `/metrics` из трейсов исключены: их опрашивает оркестратор, и
полезного в этих спанах ничего нет.

## Алерты

`alerts/document-parser.rules.yml` подключается к Prometheus стенда
автоматически. Пороги привязаны к настройкам сервиса — при изменении
`TASK_TIMEOUT_SECS` или `RMQ_ACK_DEADLINE_SECS` правьте и правила.

Что считается серьёзным:

* `DocumentParserConsumerSilent` — под жив, но очередь не разбирает;
* `DocumentParserDlqNotEmpty` — сообщения в DLQ, задачи потеряны для пользователя;
* `DocumentParserAckDeadlineRisk` — обработка подошла к `consumer_timeout`
  брокера, дальше начнутся дубли.

## Импорт в уже работающую Grafana

Локальный стенд нужен не всегда: если Grafana и Prometheus в контуре уже есть,
достаточно двух шагов.

1. Прометею — цель. Либо scrape нашего порта:

   ```yaml
   - job_name: document-parser
     static_configs:
       - targets: ['document-parser:9464']
   ```

   либо, если метрики идут через коллектор, — скрейп его `prometheus`-экспортёра
   (не забудьте `honor_labels: true`, иначе `job` и `instance`, проставленные
   коллектором из ресурсных атрибутов, затрутся именем скрейп-джоба).

2. Grafana — дашборд: Dashboards → New → Import → загрузить
   `grafana/dashboards/document-parser.json`, выбрать свой Prometheus.
   Правила из `alerts/document-parser.rules.yml` кладутся в `rule_files`
   Прометея как есть.

Переменная **Job** в дашборде подхватит то имя, под которым метрики окажутся
в вашем Prometheus, — правки запросов не нужны.

## Правки дашборда

Дашборд провижинится из файла, `allowUiUpdates: false` — правки в UI
перетираются при перезагрузке провижининга. Порядок такой: поправить в UI,
экспортировать JSON (Dashboard settings → JSON Model), положить в
`grafana/dashboards/document-parser.json`, сохранив `"uid": "document-parser"`.
