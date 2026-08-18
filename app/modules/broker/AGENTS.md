# Broker — потребление задач из очереди

Файл-ориентир для ИИ-агентов, работающих с `app/modules/broker/`.
Решения и их обоснование — в `docs/rabbitmq-integration.md`, здесь только то,
что нужно, чтобы не сломать работающее.

## 1. Назначение

Второй транспорт к тому же конвейеру перевода, что и HTTP-роутер
`translator/v2`. Сервис читает задачи из `document-parser.queue`, выполняет их
общим пайплайном и публикует статус в `webhook_manager`. Ответ в очередь не
пишется.

## 2. Файлы

| Файл | Роль |
| --- | --- |
| `abc/abc.py` | `BrokerConsumerABC` (connect/start/stop/health/health_report), `TaskHandlerABC`, `HandlerOutcome`. Ничего AMQP-специфичного. |
| `abc/factory.py` | `BrokerFactory`: `BROKER_TYPE` → реализация. |
| `rabbitmq/config.py` | `RabbitMQConfig` — разбор настроек, `validate()` возвращает предупреждения, не бросает. |
| `rabbitmq/consumer.py` | `RabbitMQConsumer` — топология, потребление, ack/nack, retry, DLQ, сторож DLQ. |
| `dispatcher.py` | Реестр `task_type → handler`, терпит расхождение написания имени сервиса. |
| `schemas.py` | `TaskEnvelope`, `TranslatePayload`, `parse_envelope`. |
| `keys.py` | Сборка ключа задачи `{user_id}:{SERVICE_NAME}:{task_id}`. |
| `errors.py` | `classify_error` — исключение → решение по очереди + публичный текст. |
| `reporting.py` | Best-effort публикация статуса из транспорта. |
| `handlers/translate.py` | Хендлер `document-parser.translate`. |

## 3. Инварианты — ломать нельзя

1. **Чужое не создаём.** `document-parser.tasks` и `document-parser.queue`
   создаёт `task_gateway`. Мы делаем только passive-проверку и потребляем;
   `queue.bind()` не вызываем никогда. Активно объявляем **только** свои
   `dlx`/`dlq`/`retry`.
2. **У рабочей очереди нет DLX и не будет.** Поэтому `reject(requeue=False)`
   запрещён — он уничтожает сообщение. Permanent-ошибка = публикация копии в
   наш DLQ и **только после успеха** ack оригинала. Обратный порядок теряет
   задачу.
3. **Успех публикации — только `Basic.Ack`.** Публикуем с `mandatory=True`.
   Публикация в несуществующую очередь исключения не даёт: брокер возвращает
   `Basic.Return`, и без явной проверки копия считалась бы сохранённой.
4. **Retry возвращает сообщение через пустой DLX**: `x-dead-letter-exchange=""`
   и `x-dead-letter-routing-key=<рабочая очередь>`. Возврат через основной
   exchange не работает — он `direct`, и ключ retry-очереди ни с чем не связан.
5. **`create_task` из очереди не зовём** — задачу уже создал гейтвей. Только
   обновляем.
6. **Один экземпляр `TranslatorV2Service` на задачу** (в нём копится
   `_last_progress`).
7. **Общие ресурсы берём из `AppRuntime`**: одна `ClientSession`, один пул
   процессов, общие семафоры. Своих не заводим.
8. **`dlq_depth()` открывает новый канал на каждый опрос.** aio-pika кэширует
   `declaration_result`, и passive-declare на том же канале вернёт счётчик на
   момент первого объявления. Это же правило действует для любых проверок
   очередей в тестах на живом брокере.

## 4. Классификация ошибок

`errors.classify_error` возвращает `ErrorDecision(action, message, report, log_level)`:

- `ACK` — работы больше нет (отмена). Подтвердить.
- `RETRY` — временный сбой. Копия в retry-очередь со счётчиком `x-attempt`;
  попытки исчерпаны → DLQ.
- `REJECT` — чинить повтором нечего. Копия в DLQ, затем ack.

`x-attempt` везде означает **число провалившихся попыток, включая текущую**.

## 5. Проверка изменений

Юнит-тесты обязательны, но их **недостаточно**: все три дефекта с тихой потерей
сообщения (см. `docs/rabbitmq-handoff.md` §4) юнит-тесты на фейках пропускали.
Любую правку в `consumer.py` проверяйте прогоном против живого RabbitMQ на
временной топологии с уникальными именами, убирая её за собой.

```bash
docker compose -f docker-compose.dev.yaml up -d
PYTHONPATH=app python3 -m unittest discover -s tests -t tests
```

Считать сообщения в очередях — через management API (`:15672`) или новое
соединение, иначе получите ложный результат (см. инвариант 8).
