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
    async def test_depth_is_unknown_without_connection(self):
        consumer = build_consumer()
        self.assertEqual(await consumer.dlq_depth(), -1)

    async def test_depth_uses_fresh_channel_each_time(self):
        """aio-pika кэширует declaration_result: канал обязан быть новым."""
        consumer = build_consumer()
        channels = []

        def make_channel():
            channel = MagicMock()
            channel.is_closed = False
            queue = MagicMock()
            queue.declaration_result.message_count = 4
            channel.declare_queue = AsyncMock(return_value=queue)
            channel.close = AsyncMock()
            channels.append(channel)
            return channel

        connection = MagicMock()
        connection.is_closed = False
        connection.channel = AsyncMock(side_effect=lambda *a, **kw: make_channel())
        consumer._connection = connection

        first = await consumer.dlq_depth()
        second = await consumer.dlq_depth()

        self.assertEqual((first, second), (4, 4))
        self.assertEqual(len(channels), 2)
        for channel in channels:
            channel.declare_queue.assert_awaited_once_with(
                "document-parser.dlq", passive=True
            )
            channel.close.assert_awaited_once()

    async def test_depth_is_unknown_when_queue_is_missing(self):
        consumer = build_consumer()
        channel = MagicMock()
        channel.is_closed = False
        channel.declare_queue = AsyncMock(side_effect=RuntimeError("NOT_FOUND"))
        channel.close = AsyncMock()
        connection = MagicMock()
        connection.is_closed = False
        connection.channel = AsyncMock(return_value=channel)
        consumer._connection = connection

        self.assertEqual(await consumer.dlq_depth(), -1)


class DlqWatchTest(unittest.IsolatedAsyncioTestCase):
    async def test_watch_does_not_start_when_disabled(self):
        consumer = build_consumer(dlq_check_interval_secs=0)
        consumer._start_dlq_watch()
        self.assertIsNone(consumer._dlq_watch_task)

    async def test_watch_logs_and_remembers_depth(self):
        consumer = build_consumer(dlq_check_interval_secs=1)
        consumer.dlq_depth = AsyncMock(return_value=3)
        messages: list[str] = []

        with patch("modules.broker.rabbitmq.consumer.logger") as fake_logger:
            fake_logger.critical.side_effect = lambda msg, *a: messages.append(msg)
            fake_logger.warning.side_effect = lambda msg, *a: messages.append(msg)
            consumer._start_dlq_watch()
            await asyncio.sleep(1.3)
            await consumer._stop_dlq_watch()

        self.assertEqual(consumer._dlq_depth, 3)
        self.assertTrue(any("DLQ" in m for m in messages), messages)

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


if __name__ == "__main__":
    unittest.main()
