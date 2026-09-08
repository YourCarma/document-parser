"""Этап 4: /health со состоянием консюмера и сторож DLQ."""
import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI, Request, Response
from fastapi.testclient import TestClient

from modules.broker.abc.abc import BrokerConsumerABC
from test_rabbitmq_consumer import build_consumer


class StubBroker(BrokerConsumerABC):
    """Минимальный консюмер: у ABC есть health_report по умолчанию."""

    def __init__(self, healthy: bool):
        self._healthy = healthy

    async def connect(self) -> None: ...
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def health(self) -> bool:
        return self._healthy


def build_app(broker) -> FastAPI:
    """Собрать приложение с тем же обработчиком /health, что и в main.py."""
    import main

    app = FastAPI()
    app.state.broker = broker
    app.add_api_route("/health", main.health_check, methods=["GET"])
    return app


class HealthEndpointTest(unittest.TestCase):
    def test_health_reports_disabled_broker_without_error(self):
        with TestClient(build_app(None)) as client:
            resp = client.get("/health")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"status": "Ok", "broker": "disabled"})

    def test_health_is_ok_when_consumer_is_healthy(self):
        with TestClient(build_app(StubBroker(healthy=True))) as client:
            resp = client.get("/health")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["broker"]["healthy"])

    def test_health_is_503_when_consumer_is_not_consuming(self):
        """Под, который молча не разбирает очередь, обязан выглядеть больным."""
        with TestClient(build_app(StubBroker(healthy=False))) as client:
            resp = client.get("/health")
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp.json()["status"], "Error")

    def test_health_is_503_when_health_check_itself_fails(self):
        broker = StubBroker(healthy=True)
        broker.health_report = AsyncMock(side_effect=RuntimeError("канал закрыт"))
        with TestClient(build_app(broker)) as client:
            resp = client.get("/health")
        self.assertEqual(resp.status_code, 503)
        self.assertIn("канал закрыт", resp.json()["broker"]["error"])


class HealthReportTest(unittest.IsolatedAsyncioTestCase):
    async def test_report_exposes_topology_state(self):
        consumer = build_consumer()
        consumer._dlq_ready = True
        consumer._retry_queue_ready = False
        consumer._dlq_depth = 7

        report = await consumer.health_report()

        self.assertEqual(report["queue"], "document-parser.queue")
        self.assertEqual(report["dlq"], "document-parser.dlq")
        self.assertTrue(report["dlq_ready"])
        self.assertFalse(report["retry_queue_ready"])
        self.assertEqual(report["dlq_depth"], 7)
        self.assertFalse(report["healthy"])  # потребление не начиналось


class DlqDepthTest(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _connection(message_count=4, error=None):
        """Соединение с одним каналом и подложкой, отдающей счётчик."""
        underlay = MagicMock()
        if error is not None:
            underlay.queue_declare = AsyncMock(side_effect=error)
        else:
            underlay.queue_declare = AsyncMock(
                return_value=MagicMock(message_count=message_count)
            )
        channel = MagicMock()
        channel.is_closed = False
        channel.get_underlay_channel = AsyncMock(return_value=underlay)
        connection = MagicMock()
        connection.is_closed = False
        connection.channel = AsyncMock(return_value=channel)
        return connection, channel, underlay

    async def test_depth_is_unknown_without_connection(self):
        consumer = build_consumer()
        consumer._dlq_ready = True
        consumer._connection = None
        self.assertEqual(await consumer.dlq_depth(), -1)

    async def test_depth_is_unknown_when_dlq_was_not_declared(self):
        """Не смогли объявить DLQ — не долбим брокер каждую минуту."""
        consumer = build_consumer()
        consumer._dlq_ready = False
        connection, _, underlay = self._connection()
        consumer._connection = connection

        self.assertEqual(await consumer.dlq_depth(), -1)
        underlay.queue_declare.assert_not_awaited()

    async def test_depth_reuses_single_channel_and_bypasses_cache(self):
        """Канал один на все опросы, счётчик берётся мимо кэша aio-pika.

        Новый канал на опрос течёт: при NOT_FOUND брокер закрывает канал,
        а RobustChannel молча восстанавливает его уже без ссылки у нас.
        """
        consumer = build_consumer()
        consumer._dlq_ready = True
        connection, channel, underlay = self._connection(message_count=4)
        consumer._connection = connection

        first = await consumer.dlq_depth()
        second = await consumer.dlq_depth()

        self.assertEqual((first, second), (4, 4))
        connection.channel.assert_awaited_once()
        self.assertIs(consumer._stats_channel, channel)
        self.assertEqual(underlay.queue_declare.await_count, 2)
        underlay.queue_declare.assert_awaited_with(
            "document-parser.dlq", passive=True
        )
        # declare_queue кэширует declaration_result — им пользоваться нельзя.
        channel.declare_queue.assert_not_called()

    async def test_depth_recreates_channel_after_it_was_closed(self):
        consumer = build_consumer()
        consumer._dlq_ready = True
        connection, channel, _ = self._connection()
        consumer._connection = connection
        dead = MagicMock()
        dead.is_closed = True
        consumer._stats_channel = dead

        self.assertEqual(await consumer.dlq_depth(), 4)
        self.assertIs(consumer._stats_channel, channel)

    async def test_depth_is_unknown_when_queue_is_missing(self):
        consumer = build_consumer()
        consumer._dlq_ready = True
        connection, _, _ = self._connection(error=RuntimeError("NOT_FOUND"))
        consumer._connection = connection

        self.assertEqual(await consumer.dlq_depth(), -1)


class DlqWatchTest(unittest.IsolatedAsyncioTestCase):
    async def test_watch_does_not_start_when_disabled(self):
        consumer = build_consumer(dlq_check_interval_secs=0)
        consumer._start_dlq_watch()
        self.assertIsNone(consumer._dlq_watch_task)

    async def test_watch_logs_and_remembers_depth(self):
        consumer = build_consumer(dlq_check_interval_secs=60)
        consumer.dlq_depth = AsyncMock(return_value=3)
        records: list[tuple] = []

        with patch("modules.broker.rabbitmq.consumer.logger") as fake_logger:
            fake_logger.log.side_effect = lambda level, msg, *a: records.append(
                (level, msg)
            )
            consumer._start_dlq_watch()
            # Первый опрос идёт сразу, ждать интервал не нужно.
            await asyncio.sleep(0.05)
            await consumer._stop_dlq_watch()

        self.assertEqual(consumer._dlq_depth, 3)
        self.assertTrue(records, "сторож не сказал о непустой DLQ")
        self.assertEqual(records[0][0], "CRITICAL")

    async def test_watch_escalates_only_when_depth_changes(self):
        """Непустая DLQ не должна кричать critical каждый цикл до разбора."""
        consumer = build_consumer(dlq_check_interval_secs=60)
        depths = iter([1, 1, 2])
        # После исчерпания держим последнее значение, чтобы цикл не упал.
        consumer.dlq_depth = AsyncMock(side_effect=lambda: next(depths, 2))
        levels: list[str] = []

        with patch("modules.broker.rabbitmq.consumer.logger") as fake_logger:
            fake_logger.log.side_effect = lambda level, msg, *a: levels.append(level)
            # Один непрерывный цикл: перезапуск сторожа сбросил бы предыдущее
            # значение и сделал бы проверку эскалации бессмысленной.
            task = asyncio.create_task(consumer._dlq_watch_loop(0.05))
            await asyncio.sleep(0.17)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        self.assertEqual(levels[:3], ["CRITICAL", "WARNING", "CRITICAL"])

    async def test_watch_stops_on_stop_event(self):
        consumer = build_consumer(dlq_check_interval_secs=60)
        consumer.dlq_depth = AsyncMock(return_value=0)
        consumer._start_dlq_watch()
        task = consumer._dlq_watch_task
        self.assertIsNotNone(task)

        # Событие остановки будит сторожа сразу, а не через минуту.
        consumer._stop_event.set()
        await asyncio.wait_for(task, timeout=2)
        self.assertTrue(task.done())


class DlqWatchLifecycleTest(unittest.IsolatedAsyncioTestCase):
    """Мутационные дыры: сторож можно было выключить целиком незаметно."""

    async def test_start_launches_the_watch(self):
        consumer = build_consumer(dlq_check_interval_secs=60)
        consumer._queue = MagicMock()
        consumer._queue.consume = AsyncMock(return_value="tag")

        await consumer.start()
        try:
            self.assertIsNotNone(consumer._dlq_watch_task)
            self.assertFalse(consumer._dlq_watch_task.done())
        finally:
            await consumer._stop_dlq_watch()

    async def test_start_does_not_launch_second_watch(self):
        consumer = build_consumer(dlq_check_interval_secs=60)
        consumer._start_dlq_watch()
        first = consumer._dlq_watch_task
        consumer._start_dlq_watch()
        try:
            self.assertIs(consumer._dlq_watch_task, first)
        finally:
            await consumer._stop_dlq_watch()

    async def test_stop_cancels_the_watch(self):
        """Иначе задача переживает остановку и продолжает ходить в брокер."""
        consumer = build_consumer(dlq_check_interval_secs=60)
        consumer._start_dlq_watch()
        task = consumer._dlq_watch_task

        await consumer.stop()

        self.assertTrue(task.done())
        self.assertIsNone(consumer._dlq_watch_task)


class ConnectionLossTest(unittest.IsolatedAsyncioTestCase):
    """Обрыв соединения возвращает сообщение в очередь — дубль недопустим."""

    async def test_orphaned_tasks_are_cancelled_on_connection_loss(self):
        consumer = build_consumer()
        started = asyncio.Event()

        async def long_work():
            started.set()
            await asyncio.sleep(30)

        task = asyncio.create_task(long_work())
        consumer._tasks.add(task)
        await started.wait()

        consumer._on_connection_closed()
        await asyncio.sleep(0)

        self.assertTrue(task.cancelled() or task.cancelling())
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def test_planned_stop_does_not_cancel_tasks(self):
        """При штатной остановке задачи гасит stop() со своим grace-периодом."""
        consumer = build_consumer()
        task = asyncio.create_task(asyncio.sleep(0.05))
        consumer._tasks.add(task)
        consumer._stopping = True

        consumer._on_connection_closed()
        await task

        self.assertFalse(task.cancelled())


class ConsumerHealthTest(unittest.IsolatedAsyncioTestCase):
    async def test_health_is_false_when_connected_but_not_consuming(self):
        """Главное обещание: не потребляем — значит больны, даже если живы."""
        consumer = build_consumer()
        alive = MagicMock()
        alive.is_closed = False
        consumer._connection = alive
        consumer._channel = alive
        consumer._started = False

        self.assertFalse(await consumer.health())

        consumer._started = True
        self.assertTrue(await consumer.health())


if __name__ == "__main__":
    unittest.main()
