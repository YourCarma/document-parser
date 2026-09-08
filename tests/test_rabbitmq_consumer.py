import asyncio
import json
import time
import unittest
from unittest.mock import AsyncMock, patch

from pamqp.commands import Basic

from modules.broker.abc.abc import HandlerOutcome, TaskHandlerABC
from modules.broker.dispatcher import TaskDispatcher
from modules.broker.exceptions import InvalidTaskPayload
from modules.broker.rabbitmq.config import RabbitMQConfig
from modules.broker.rabbitmq.consumer import RabbitMQConsumer, read_attempt
from modules.broker.schemas import TaskEnvelope, TranslatePayload
from modules.watchtower.exceptions import WatchtowerUnavailable
from modules.webhook_manager.schemas import TaskStatus


ENVELOPE_BODY = json.dumps(
    {
        "task_id": "task-1",
        "user_id": "user-1",
        "task_type": "document-parser.translate",
        "payload": {"file_path": "documents/report.pdf"},
    }
).encode("utf-8")


class FakeMessage:
    """Утиный аналог `AbstractIncomingMessage`."""

    def __init__(
        self,
        body: bytes = ENVELOPE_BODY,
        headers=None,
        routing_key: str = "document-parser.translate",
        message_id: str = "msg-1",
        redelivered: bool = False,
        content_type: str = "application/json",
    ):
        self.body = body
        self.headers = headers or {}
        self.routing_key = routing_key
        self.message_id = message_id
        self.correlation_id = None
        self.redelivered = redelivered
        self.content_type = content_type
        self.acked = False
        self.ack_error: Exception | None = None
        self.nacked: list[bool] = []
        self.rejected: list[bool] = []

    async def ack(self):
        if self.ack_error is not None:
            raise self.ack_error
        self.acked = True

    async def nack(self, requeue: bool = True):
        self.nacked.append(requeue)

    async def reject(self, requeue: bool = False):
        self.rejected.append(requeue)


class FakeDeliveredMessage:
    """Ответ брокера на неотмаршрутизированную публикацию (mandatory=True)."""

    def __init__(self):
        self.delivery = Basic.Return()


class FakeExchange:
    def __init__(self, error: Exception | None = None, unroutable: set | None = None):
        self.published: list[tuple] = []
        self.mandatory_flags: list[bool] = []
        self._error = error
        # Имена очередей, которых «физически нет»: брокер вернёт Basic.Return.
        self._unroutable = unroutable or set()

    async def publish(self, message, routing_key, mandatory=False):
        if self._error is not None:
            raise self._error
        self.mandatory_flags.append(mandatory)
        if routing_key in self._unroutable:
            return FakeDeliveredMessage()
        self.published.append((message, routing_key))
        return Basic.Ack()


class FakeChannel:
    def __init__(self, error: Exception | None = None, unroutable: set | None = None):
        self.default_exchange = FakeExchange(error, unroutable)
        self.is_closed = False


class FakeQueue:
    def __init__(self):
        self.cancelled: list[str] = []

    async def cancel(self, consumer_tag):
        self.cancelled.append(consumer_tag)


class FakeConnection:
    def __init__(self, log: list | None = None):
        self.is_closed = False
        self._log = log

    async def close(self):
        self.is_closed = True
        if self._log is not None:
            self._log.append("connection-closed")


class FakeRuntime:
    """Общие ресурсы процесса без единого реального клиента."""

    def __init__(self):
        self.http_session = None
        self.executor = object()
        self.parser_semaphore = asyncio.Semaphore(1)
        self.translation_semaphore = asyncio.Semaphore(1)
        self.webhook = AsyncMock()
        self.webhook.get_task.return_value = None
        self.watchtower_client = AsyncMock()
        self.resource_manager_client = AsyncMock()

    def webhook_manager(self):
        return self.webhook

    def watchtower(self):
        return self.watchtower_client

    def resource_manager(self):
        return self.resource_manager_client


class RecordingHandler(TaskHandlerABC):
    """Возвращает заданный `HandlerOutcome` либо бросает заданное исключение."""

    task_type = "document-parser.translate"
    payload_model = TranslatePayload

    outcome: HandlerOutcome = HandlerOutcome(TaskStatus.READY)
    error: BaseException | None = None
    calls: list[str] = []

    async def handle(self, envelope: TaskEnvelope, task_key: str) -> HandlerOutcome:
        type(self).calls.append(task_key)
        if type(self).error is not None:
            raise type(self).error
        return type(self).outcome


def build_config(**overrides) -> RabbitMQConfig:
    # Фикстура повторяет боевую топологию: exchange direct и единственный
    # точный ключ. На topic-биндинге ошибки маршрутизации не видны.
    params = dict(
        url="amqp://guest:guest@localhost:5672/",
        safe_url="amqp://guest:***@localhost:5672/",
        exchange="document-parser.tasks",
        exchange_type="direct",
        queue="document-parser.queue",
        routing_keys=("document-parser.translate",),
        prefetch_count=3,
        dlx="document-parser.dlx",
        dlq="document-parser.dlq",
        retry_queue="document-parser.retry",
        retry_delay_secs=30,
        max_retries=3,
        reconnect_interval_secs=5,
        connect_timeout_secs=15,
        consumer_tag="document-parser",
        declare_topology=True,
        ack_deadline_secs=1800,
        shutdown_grace_secs=60,
        # 0 — сторож DLQ в юнит-тестах не поднимается: он ходит в сеть.
        dlq_check_interval_secs=0,
    )
    params.update(overrides)
    return RabbitMQConfig(**params)


def build_consumer(handler_cls=RecordingHandler, **config_overrides):
    dispatcher = TaskDispatcher()
    dispatcher.register(handler_cls)
    consumer = RabbitMQConsumer(
        FakeRuntime(), dispatcher, build_config(**config_overrides)
    )
    consumer._publish_channel = FakeChannel()
    # Состояние после успешного объявления своих служебных объектов.
    consumer._dlq_ready = True
    consumer._retry_queue_ready = True
    return consumer


def published_to(consumer, routing_key: str) -> list:
    return [
        message
        for message, key in consumer._publish_channel.default_exchange.published
        if key == routing_key
    ]


class ProcessMessageTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        RecordingHandler.outcome = HandlerOutcome(TaskStatus.READY)
        RecordingHandler.error = None
        RecordingHandler.calls = []

    async def test_successful_message_is_acked_once(self):
        consumer = build_consumer()
        message = FakeMessage()

        await consumer._process_message(message)

        self.assertTrue(message.acked)
        self.assertEqual(message.rejected, [])
        self.assertEqual(message.nacked, [])
        self.assertEqual(RecordingHandler.calls, ["user-1:document-parser:task-1"])

    async def test_cancelled_outcome_is_acked(self):
        RecordingHandler.outcome = HandlerOutcome(TaskStatus.CANCELLED)
        consumer = build_consumer()
        message = FakeMessage()

        await consumer._process_message(message)

        self.assertTrue(message.acked)
        consumer._runtime.webhook.update_progress.assert_not_awaited()

    async def test_unparsable_body_goes_to_dlq_without_report(self):
        consumer = build_consumer()
        message = FakeMessage(body=b"not json")

        with patch("modules.broker.rabbitmq.consumer.logger") as fake_logger:
            await consumer._process_message(message)

        saved = published_to(consumer, "document-parser.dlq")
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0].body, b"not json")
        self.assertTrue(message.acked)
        consumer._runtime.webhook.update_progress.assert_not_awaited()
        self.assertTrue(fake_logger.critical.called)

    async def test_unknown_task_type_goes_to_dlq_and_is_reported(self):
        consumer = build_consumer()
        body = json.dumps(
            {
                "task_id": "task-1",
                "user_id": "user-1",
                "task_type": "document-parser.parse",
                "payload": {},
            }
        ).encode("utf-8")
        message = FakeMessage(body=body)

        await consumer._process_message(message)

        webhook = consumer._runtime.webhook
        published = webhook.update_response_data.await_args_list[-1].args[1]
        self.assertEqual(published["error"], "Неизвестный тип задачи")
        self.assertEqual(
            webhook.update_progress.await_args_list[-1].args[2], TaskStatus.ERROR
        )
        self.assertEqual(len(published_to(consumer, "document-parser.dlq")), 1)
        self.assertTrue(message.acked)

    async def test_invalid_payload_goes_to_dlq_and_is_reported(self):
        RecordingHandler.error = InvalidTaskPayload("нет file_path")
        consumer = build_consumer()
        message = FakeMessage()

        await consumer._process_message(message)

        self.assertEqual(len(published_to(consumer, "document-parser.dlq")), 1)
        self.assertTrue(message.acked)
        published = consumer._runtime.webhook.update_response_data.await_args_list[
            -1
        ].args[1]
        self.assertEqual(published["error"], "Неверный формат данных задачи")

    async def test_permanent_failure_is_published_to_dlq_before_ack(self):
        RecordingHandler.error = InvalidTaskPayload("нет file_path")
        consumer = build_consumer()
        message = FakeMessage(headers={"x-attempt": 2})
        order: list[str] = []

        original_publish = consumer._publish_channel.default_exchange.publish

        async def tracking_publish(msg, routing_key, mandatory=False):
            order.append(f"publish:{routing_key}")
            return await original_publish(msg, routing_key, mandatory=mandatory)

        consumer._publish_channel.default_exchange.publish = tracking_publish

        async def tracking_ack():
            order.append("ack")
            message.acked = True

        message.ack = tracking_ack

        await consumer._process_message(message)

        self.assertEqual(order, ["publish:document-parser.dlq", "ack"])
        copy = published_to(consumer, "document-parser.dlq")[0]
        self.assertEqual(copy.body, ENVELOPE_BODY)
        self.assertEqual(
            copy.headers["x-original-routing-key"], "document-parser.translate"
        )
        self.assertEqual(copy.headers["x-attempt"], 3)
        self.assertIn("InvalidTaskPayload", copy.headers["x-last-error"])

    async def test_dlq_publish_failure_leaves_message_unacked(self):
        RecordingHandler.error = InvalidTaskPayload("нет file_path")
        consumer = build_consumer(retry_delay_secs=0)
        consumer._publish_channel = FakeChannel(error=RuntimeError("канал закрыт"))
        message = FakeMessage()

        await consumer._process_message(message)

        self.assertFalse(message.acked)
        self.assertEqual(message.nacked, [True])

    async def test_permanent_failure_is_not_acked_when_dlq_is_unavailable(self):
        RecordingHandler.error = InvalidTaskPayload("нет file_path")
        consumer = build_consumer(retry_delay_secs=0)
        # DLX/DLQ объявить не удалось при старте.
        consumer._dlq_ready = False
        message = FakeMessage()

        await consumer._process_message(message)

        self.assertFalse(message.acked)
        self.assertEqual(message.nacked, [True])
        self.assertEqual(consumer._publish_channel.default_exchange.published, [])

    async def test_transient_failure_goes_to_retry_queue_with_incremented_attempt(self):
        RecordingHandler.error = WatchtowerUnavailable("хранилище недоступно")
        consumer = build_consumer()
        message = FakeMessage()

        await consumer._process_message(message)

        published = consumer._publish_channel.default_exchange.published
        self.assertEqual(len(published), 1)
        copy, routing_key = published[0]
        self.assertEqual(routing_key, "document-parser.retry")
        self.assertEqual(copy.headers["x-attempt"], 1)
        self.assertTrue(message.acked)
        self.assertEqual(message.rejected, [])

    async def test_retry_preserves_body_and_original_routing_key(self):
        RecordingHandler.error = WatchtowerUnavailable("хранилище недоступно")
        consumer = build_consumer()
        message = FakeMessage(routing_key="document_parser.translate")

        await consumer._process_message(message)

        copy, _ = consumer._publish_channel.default_exchange.published[0]
        self.assertEqual(copy.body, ENVELOPE_BODY)
        self.assertEqual(
            copy.headers["x-original-routing-key"], "document_parser.translate"
        )

    async def test_original_routing_key_survives_the_retry_hop(self):
        # На втором круге сообщение приходит из retry-очереди, и его
        # routing_key — имя рабочей очереди. Ключ продюсера должен уцелеть.
        RecordingHandler.error = InvalidTaskPayload("нет file_path")
        consumer = build_consumer()
        message = FakeMessage(
            routing_key="document-parser.queue",
            headers={
                "x-attempt": 1,
                "x-original-routing-key": "document-parser.translate",
            },
        )

        await consumer._process_message(message)

        copy = published_to(consumer, "document-parser.dlq")[0]
        self.assertEqual(
            copy.headers["x-original-routing-key"], "document-parser.translate"
        )

    async def test_attempts_exhausted_goes_to_dlq_and_reports_upstream_message(self):
        RecordingHandler.error = WatchtowerUnavailable("хранилище недоступно")
        consumer = build_consumer(max_retries=3)
        message = FakeMessage(headers={"x-attempt": 3})

        await consumer._process_message(message)

        self.assertEqual(published_to(consumer, "document-parser.retry"), [])
        self.assertEqual(len(published_to(consumer, "document-parser.dlq")), 1)
        self.assertTrue(message.acked)
        published = consumer._runtime.webhook.update_response_data.await_args_list[
            -1
        ].args[1]
        self.assertEqual(
            published["error"], "Сервис временно недоступен, попробуйте позже"
        )

    async def test_last_allowed_attempt_still_goes_to_retry(self):
        # Граница ретраев: при max_retries=3 попытка №3 обязана уйти в retry,
        # и только следующая — в DLQ.
        RecordingHandler.error = WatchtowerUnavailable("хранилище недоступно")
        consumer = build_consumer(max_retries=3)
        message = FakeMessage(headers={"x-attempt": 2})

        await consumer._process_message(message)

        retried = published_to(consumer, "document-parser.retry")
        self.assertEqual(len(retried), 1)
        self.assertEqual(retried[0].headers["x-attempt"], 3)
        self.assertEqual(published_to(consumer, "document-parser.dlq"), [])
        self.assertTrue(message.acked)

    async def test_first_attempt_beyond_limit_goes_to_dlq(self):
        RecordingHandler.error = WatchtowerUnavailable("хранилище недоступно")
        consumer = build_consumer(max_retries=1)
        message = FakeMessage(headers={"x-attempt": 1})

        await consumer._process_message(message)

        self.assertEqual(published_to(consumer, "document-parser.retry"), [])
        self.assertEqual(len(published_to(consumer, "document-parser.dlq")), 1)

    async def test_attempt_is_read_from_x_death_when_header_missing(self):
        self.assertEqual(read_attempt({"x-death": [{"count": 2}]}), 2)
        self.assertEqual(read_attempt({"x-attempt": 1, "x-death": [{"count": 2}]}), 2)
        self.assertEqual(read_attempt(None), 0)

    async def test_retry_falls_back_to_nack_when_retry_queue_disabled(self):
        RecordingHandler.error = WatchtowerUnavailable("хранилище недоступно")
        consumer = build_consumer(retry_queue="", retry_delay_secs=0)
        message = FakeMessage()

        await consumer._process_message(message)

        self.assertEqual(message.nacked, [True])
        self.assertFalse(message.acked)

    async def test_retry_falls_back_to_nack_when_retry_queue_not_declared(self):
        RecordingHandler.error = WatchtowerUnavailable("хранилище недоступно")
        consumer = build_consumer(retry_delay_secs=0)
        # Очередь настроена, но объявить её при старте не удалось.
        consumer._retry_queue_ready = False
        message = FakeMessage()

        await consumer._process_message(message)

        self.assertEqual(message.nacked, [True])
        self.assertEqual(consumer._publish_channel.default_exchange.published, [])

    async def test_degraded_requeue_waits_before_nack(self):
        # Без паузы лежащий upstream превращается в горячий цикл, который
        # завалит и брокер, и webhook_manager записями PROCESSING.
        RecordingHandler.error = WatchtowerUnavailable("хранилище недоступно")
        consumer = build_consumer(retry_queue="", retry_delay_secs=30)
        message = FakeMessage()
        order: list[str] = []

        async def tracking_delay():
            order.append("delay")

        consumer._delay_before_requeue = tracking_delay

        async def tracking_nack(requeue=True):
            order.append("nack")
            message.nacked.append(requeue)

        message.nack = tracking_nack

        await consumer._process_message(message)

        self.assertEqual(order, ["delay", "nack"])

    async def test_requeue_delay_actually_waits(self):
        consumer = build_consumer(retry_delay_secs=0.2)
        started = time.monotonic()

        await consumer._delay_before_requeue()

        self.assertGreaterEqual(time.monotonic() - started, 0.15)

    async def test_requeue_delay_is_interrupted_by_shutdown(self):
        # Иначе сообщение впустую держит слот prefetch половину grace-периода.
        consumer = build_consumer(retry_delay_secs=30)
        consumer._stop_event.set()
        started = time.monotonic()

        await consumer._delay_before_requeue()

        self.assertLess(time.monotonic() - started, 1)

    async def test_unroutable_retry_publish_is_not_treated_as_success(self):
        # Публикация в удалённую очередь исключения не даёт: брокер отвечает
        # Basic.Return. Считать это успехом = потерять задачу.
        RecordingHandler.error = WatchtowerUnavailable("хранилище недоступно")
        consumer = build_consumer(retry_delay_secs=0)
        consumer._publish_channel = FakeChannel(unroutable={"document-parser.retry"})
        message = FakeMessage()

        await consumer._process_message(message)

        self.assertFalse(message.acked)
        self.assertEqual(message.nacked, [True])
        self.assertFalse(consumer._retry_queue_ready)

    async def test_unroutable_dlq_publish_is_not_treated_as_success(self):
        RecordingHandler.error = InvalidTaskPayload("нет file_path")
        consumer = build_consumer(retry_delay_secs=0)
        consumer._publish_channel = FakeChannel(unroutable={"document-parser.dlq"})
        message = FakeMessage()

        await consumer._process_message(message)

        self.assertFalse(message.acked)
        self.assertEqual(message.nacked, [True])
        self.assertFalse(consumer._dlq_ready)

    async def test_copies_are_published_as_mandatory(self):
        # Без mandatory=True брокер молча проглотит неотмаршрутизированную
        # копию и ответит Basic.Ack.
        RecordingHandler.error = WatchtowerUnavailable("хранилище недоступно")
        consumer = build_consumer()

        await consumer._process_message(FakeMessage())

        self.assertEqual(
            consumer._publish_channel.default_exchange.mandatory_flags, [True]
        )

    async def test_publish_failure_falls_back_to_nack(self):
        RecordingHandler.error = WatchtowerUnavailable("хранилище недоступно")
        consumer = build_consumer(retry_delay_secs=0)
        consumer._publish_channel = FakeChannel(error=RuntimeError("канал закрыт"))
        message = FakeMessage()

        await consumer._process_message(message)

        self.assertEqual(message.nacked, [True])
        self.assertFalse(message.acked)

    async def test_ack_failure_is_swallowed(self):
        consumer = build_consumer()
        message = FakeMessage()
        message.ack_error = RuntimeError("канал закрыт брокером")

        await consumer._process_message(message)

        self.assertFalse(message.acked)

    async def test_cancelled_error_propagates_without_ack(self):
        RecordingHandler.error = asyncio.CancelledError()
        consumer = build_consumer()
        message = FakeMessage()

        with self.assertRaises(asyncio.CancelledError):
            await consumer._process_message(message)

        self.assertFalse(message.acked)
        self.assertEqual(message.nacked, [])
        self.assertEqual(message.rejected, [])


class OnMessageTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        RecordingHandler.outcome = HandlerOutcome(TaskStatus.READY)
        RecordingHandler.error = None
        RecordingHandler.calls = []

    async def test_message_is_nacked_when_consumer_is_stopping(self):
        consumer = build_consumer()
        consumer._stopping = True
        message = FakeMessage()

        await consumer._on_message(message)

        self.assertEqual(message.nacked, [True])
        self.assertEqual(consumer._tasks, set())

    async def test_on_message_spawns_task_and_tracks_it(self):
        consumer = build_consumer()
        message = FakeMessage()

        await consumer._on_message(message)

        self.assertEqual(len(consumer._tasks), 1)
        await asyncio.gather(*consumer._tasks)
        self.assertEqual(consumer._tasks, set())
        self.assertTrue(message.acked)


class ConsumerLifecycleTest(unittest.IsolatedAsyncioTestCase):
    async def test_stop_waits_for_active_tasks(self):
        order: list[str] = []
        consumer = build_consumer()
        consumer._queue = FakeQueue()
        consumer._consumer_tag = "document-parser"
        consumer._connection = FakeConnection(order)
        consumer._channel = FakeChannel()
        consumer._started = True

        async def slow_task():
            await asyncio.sleep(0.05)
            order.append("task-done")

        task = asyncio.create_task(slow_task())
        consumer._tasks.add(task)
        task.add_done_callback(consumer._tasks.discard)

        await consumer.stop()

        self.assertEqual(order, ["task-done", "connection-closed"])
        self.assertEqual(consumer._queue, None)

    async def test_stop_cancels_tasks_after_grace(self):
        consumer = build_consumer(shutdown_grace_secs=0)
        consumer._queue = FakeQueue()
        consumer._consumer_tag = "document-parser"
        consumer._connection = FakeConnection()
        consumer._started = True
        message = FakeMessage()

        async def endless():
            await asyncio.sleep(3600)
            await message.ack()

        task = asyncio.create_task(endless())
        consumer._tasks.add(task)
        task.add_done_callback(consumer._tasks.discard)

        await consumer.stop()

        self.assertTrue(task.cancelled())
        self.assertFalse(message.acked)

    async def test_stop_is_idempotent(self):
        consumer = build_consumer()
        consumer._queue = FakeQueue()
        consumer._consumer_tag = "document-parser"
        consumer._connection = FakeConnection()
        consumer._started = True

        await consumer.stop()
        await consumer.stop()

        self.assertFalse(consumer._started)

    async def test_health_reports_false_when_not_started(self):
        consumer = build_consumer()

        self.assertFalse(await consumer.health())

        consumer._started = True
        consumer._connection = FakeConnection()
        consumer._channel = FakeChannel()
        self.assertTrue(await consumer.health())


class FakeDeclaredQueue:
    def __init__(self, name):
        self.name = name
        self.declaration_result = None
        self.bindings: list[tuple] = []

    async def bind(self, exchange, routing_key=None):
        self.bindings.append((exchange, routing_key))


class FakeTopologyChannel:
    """Канал, запоминающий все объявления, с возможностью подсунуть ошибку."""

    def __init__(self, errors: dict | None = None):
        self.errors = errors or {}
        self.declared_queues: list[dict] = []
        self.declared_exchanges: list[dict] = []
        self.qos: list[int] = []
        self.is_closed = False

    async def declare_exchange(self, name, type=None, durable=False, passive=False):
        self.declared_exchanges.append(
            {"name": name, "type": type, "durable": durable, "passive": passive}
        )
        if name in self.errors and not passive:
            raise self.errors[name]
        if name in self.errors and passive:
            raise self.errors[name]
        return name

    async def declare_queue(self, name, durable=False, arguments=None, passive=False):
        self.declared_queues.append(
            {
                "name": name,
                "durable": durable,
                "arguments": arguments,
                "passive": passive,
            }
        )
        if name in self.errors:
            raise self.errors[name]
        return FakeDeclaredQueue(name)

    async def set_qos(self, prefetch_count):
        self.qos.append(prefetch_count)

    async def close(self):
        self.is_closed = True


class FakeChannelFactory:
    """Соединение, выдающее сколько угодно новых каналов.

    Каналов ровно столько, сколько попросят: сценарий «упали и DLX/DLQ, и
    retry» делает два `_reopen_channel` подряд.
    """

    def __init__(self, errors: dict | None = None):
        self.errors = errors or {}
        self.created: list[FakeTopologyChannel] = []
        self.is_closed = False

    async def channel(self, publisher_confirms=False):
        channel = FakeTopologyChannel(self.errors)
        self.created.append(channel)
        return channel


class TopologyDeclarationTest(unittest.IsolatedAsyncioTestCase):
    def _consumer(self, channel, errors=None, **config_overrides):
        consumer = build_consumer(**config_overrides)
        consumer._dlq_ready = False
        consumer._retry_queue_ready = False
        consumer._channel = channel
        consumer._connection = FakeChannelFactory(errors)
        return consumer

    async def test_retry_queue_dead_letters_straight_into_work_queue(self):
        # Обратный путь через основной exchange невозможен: на direct-биндинге
        # ключ retry-очереди ни с чем не совпадёт и сообщение исчезнет.
        channel = FakeTopologyChannel()
        consumer = self._consumer(channel)

        await consumer._declare_topology()

        retry = next(
            q for q in channel.declared_queues if q["name"] == "document-parser.retry"
        )
        self.assertEqual(
            retry["arguments"],
            {
                "x-message-ttl": 30_000,
                "x-dead-letter-exchange": "",
                "x-dead-letter-routing-key": "document-parser.queue",
            },
        )
        self.assertFalse(retry["passive"])
        self.assertTrue(consumer._retry_queue_ready)

    async def test_own_objects_are_declared_actively(self):
        channel = FakeTopologyChannel()
        consumer = self._consumer(channel)

        await consumer._declare_topology()

        dlx = next(
            e for e in channel.declared_exchanges if e["name"] == "document-parser.dlx"
        )
        dlq = next(
            q for q in channel.declared_queues if q["name"] == "document-parser.dlq"
        )
        self.assertFalse(dlx["passive"])
        self.assertTrue(dlx["durable"])
        self.assertFalse(dlq["passive"])
        self.assertTrue(consumer._dlq_ready)

    async def test_foreign_exchange_and_queue_are_never_declared_actively(self):
        # Их создаёт task_gateway: любое активное объявление с нашей стороны
        # либо перехватит владение, либо упадёт с PRECONDITION_FAILED.
        channel = FakeTopologyChannel()
        consumer = self._consumer(channel)

        await consumer._declare_topology()

        exchange = next(
            e
            for e in channel.declared_exchanges
            if e["name"] == "document-parser.tasks"
        )
        queue = next(
            q for q in channel.declared_queues if q["name"] == "document-parser.queue"
        )
        self.assertTrue(exchange["passive"])
        self.assertTrue(queue["passive"])
        self.assertIsNone(queue["arguments"])

    async def test_no_bindings_are_created(self):
        channel = FakeTopologyChannel()
        consumer = self._consumer(channel)

        await consumer._declare_topology()

        # Единственный допустимый биндинг — наш собственный DLQ к нашему DLX.
        self.assertEqual(consumer._queue.bindings, [])

    async def test_missing_work_queue_fails_with_explicit_message(self):
        channel = FakeTopologyChannel(
            errors={"document-parser.queue": RuntimeError("NOT_FOUND")}
        )
        consumer = self._consumer(channel)

        with patch("modules.broker.rabbitmq.consumer.logger") as fake_logger:
            with self.assertRaises(RuntimeError):
                await consumer._declare_topology()

        text = " ".join(str(call) for call in fake_logger.critical.call_args_list)
        self.assertIn("task_gateway", text)

    async def test_retry_queue_conflict_degrades_without_crashing(self):
        channel = FakeTopologyChannel(
            errors={"document-parser.retry": RuntimeError("PRECONDITION_FAILED")}
        )
        consumer = self._consumer(channel)

        await consumer._declare_topology()

        self.assertFalse(consumer._retry_queue_ready)
        self.assertTrue(consumer._dlq_ready)

    async def test_declare_topology_disabled_skips_own_objects(self):
        channel = FakeTopologyChannel()
        consumer = self._consumer(channel, declare_topology=False)

        await consumer._declare_topology()

        self.assertEqual(
            [q["name"] for q in channel.declared_queues], ["document-parser.queue"]
        )
        self.assertFalse(consumer._retry_queue_ready)
        self.assertFalse(consumer._dlq_ready)


class ConfigValidationTest(unittest.TestCase):
    def test_config_validate_warns_on_task_timeout_close_to_deadline(self):
        config = build_config(ack_deadline_secs=600)

        with patch(
            "modules.broker.rabbitmq.config.settings.TASK_TIMEOUT_SECS", 1500
        ):
            warnings = config.validate()

        self.assertTrue(any("consumer_timeout" in text for text in warnings))

    def test_config_validate_warns_when_parser_workers_below_prefetch(self):
        config = build_config(prefetch_count=5)

        with patch("modules.broker.rabbitmq.config.settings.PARSER_WORKERS", 2):
            warnings = config.validate()

        self.assertTrue(any("PARSER_WORKERS" in text for text in warnings))


if __name__ == "__main__":
    unittest.main()
