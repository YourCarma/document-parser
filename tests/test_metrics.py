"""Метрики: инструменты, трекер этапов, семафор, мидлварь, исходы консюмера."""

import asyncio
import contextlib
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from modules.metrics import (
    OTEL_AVAILABLE,
    HTTPMetricsMiddleware,
    Instruments,
    StageTracker,
    TrackedSemaphore,
    dependency_name,
    override_instruments,
)
from modules.metrics import otel
from modules.metrics.instruments import DECLARED_HISTOGRAMS, HISTOGRAM_BUCKETS
from modules.metrics.registry import observe_semaphores
from settings import settings
from test_rabbitmq_consumer import FakeMessage, RecordingHandler, build_consumer

from modules.broker.abc.abc import HandlerOutcome
from modules.watchtower.exceptions import WatchtowerUnavailable
from modules.webhook_manager.schemas import TaskStatus


class RecordingInstrument:
    """Инструмент, складывающий измерения в общий список."""

    def __init__(self, name: str, calls: list) -> None:
        self._name = name
        self._calls = calls

    def add(self, amount, attributes=None):
        self._calls.append((self._name, amount, attributes or {}))

    record = add
    set = add


class RecordingMeter:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def _make(self, name, unit="", description=""):
        return RecordingInstrument(name, self.calls)

    create_counter = _make
    create_up_down_counter = _make
    create_histogram = _make
    create_gauge = _make

    def create_observable_gauge(self, name, callbacks=None, unit="", description=""):
        return RecordingInstrument(name, self.calls)


@contextlib.contextmanager
def recording_instruments():
    """Подменить синглтон инструментов на записывающий."""
    meter = RecordingMeter()
    previous = override_instruments(Instruments(meter))
    try:
        yield meter
    finally:
        override_instruments(previous)


def names(meter: RecordingMeter) -> list[str]:
    return [name for name, _amount, _attrs in meter.calls]


def attrs_for(meter: RecordingMeter, name: str) -> list[dict]:
    return [a for n, _amount, a in meter.calls if n == name]


class InstrumentDefinitionTest(unittest.TestCase):
    def test_every_histogram_has_explicit_buckets(self):
        """Гистограмма без своих границ молча получила бы бакеты «до 10».

        Задачи здесь живут тысячи секунд, поэтому такая гистограмма не
        показывала бы ничего, кроме переполненного последнего бакета.
        """
        Instruments(RecordingMeter())
        self.assertEqual(DECLARED_HISTOGRAMS, set(HISTOGRAM_BUCKETS))

    def test_instrument_names_are_prometheus_safe(self):
        meter = RecordingMeter()
        Instruments(meter)
        for name in DECLARED_HISTOGRAMS:
            self.assertTrue(name.startswith("document_parser."), name)
            # Единица измерения зашита в имя, а не в поле unit: иначе
            # экспортёры дописали бы суффикс каждый по-своему.
            self.assertTrue(name.endswith("_seconds"), name)


class StageTrackerTest(unittest.TestCase):
    def test_stage_and_task_are_recorded(self):
        with recording_instruments() as meter:
            tracker = StageTracker(source="broker")
            tracker.enter("parse document")
            tracker.enter("translate document")
            tracker.finish("ready", "translate document")

        stages = attrs_for(meter, "document_parser.task.stage.duration_seconds")
        self.assertEqual(
            [item["stage"] for item in stages],
            ["parse document", "translate document"],
        )
        self.assertEqual({item["outcome"] for item in stages}, {"ok"})

        tasks = attrs_for(meter, "document_parser.tasks")
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["status"], "ready")
        self.assertEqual(tasks[0]["source"], "broker")
        self.assertEqual(tasks[0]["error_type"], "none")
        self.assertIn("document_parser.task.duration_seconds", names(meter))

    def test_failed_stage_carries_error_type(self):
        with recording_instruments() as meter:
            tracker = StageTracker()
            tracker.enter("translate document")
            tracker.finish("error", "translate document", ValueError("boom"))

        stage = attrs_for(meter, "document_parser.task.stage.duration_seconds")[0]
        self.assertEqual(stage["outcome"], "failed")
        self.assertEqual(
            attrs_for(meter, "document_parser.tasks")[0]["error_type"], "ValueError"
        )

    def test_finish_is_idempotent(self):
        """finish() зовётся из finally, повторный вызов не должен считаться."""
        with recording_instruments() as meter:
            tracker = StageTracker()
            tracker.enter("init")
            tracker.finish("ready")
            tracker.finish("ready")

        self.assertEqual(len(attrs_for(meter, "document_parser.tasks")), 1)


class TrackedSemaphoreTest(unittest.IsolatedAsyncioTestCase):
    async def test_counts_holders_and_waiters(self):
        semaphore = TrackedSemaphore(1, "parser")
        self.assertEqual(semaphore.capacity, 1)

        await semaphore.acquire()
        self.assertEqual(semaphore.in_use, 1)
        self.assertEqual(semaphore.waiting, 0)

        waiter = asyncio.create_task(semaphore.acquire())
        await asyncio.sleep(0)
        self.assertEqual(semaphore.waiting, 1)

        semaphore.release()
        await waiter
        self.assertEqual(semaphore.in_use, 1)
        self.assertEqual(semaphore.waiting, 0)

        semaphore.release()
        self.assertEqual(semaphore.in_use, 0)

    async def test_release_without_acquire_does_not_go_negative(self):
        semaphore = TrackedSemaphore(1, "parser")
        semaphore.release()
        self.assertEqual(semaphore.in_use, 0)

    def test_semaphore_is_registered_for_observation(self):
        semaphore = TrackedSemaphore(2, "translation")
        observed = observe_semaphores("capacity")
        if OTEL_AVAILABLE:
            self.assertTrue(observed)
        # Ссылку держим до конца теста: реестр слабый и без неё семафор
        # успел бы исчезнуть.
        self.assertEqual(semaphore.capacity, 2)


class SetupObservabilityTest(unittest.TestCase):
    @unittest.skipUnless(OTEL_AVAILABLE, "пакеты opentelemetry не установлены")
    def test_second_app_is_instrumented_after_providers_are_ready(self):
        """`python main.py` импортирует модуль дважды.

        Сначала как `__main__`, потом uvicorn по строке `"main:app"` — и
        запросы обслуживает второй объект приложения. Провайдеры при этом
        общие на процесс, поэтому «уже настроено» не должно означать «второе
        приложение инструментировать не надо»: иначе метрики есть, а трейсов
        входящих запросов нет.
        """
        second_app = object()
        with patch.object(otel, "_configured", True), patch.object(
            otel.settings, "OTEL_ENABLED", True
        ), patch.object(otel, "_instrument_app") as instrument:
            otel.setup_observability(second_app)

        instrument.assert_called_once_with(second_app)

    def test_disabled_setup_does_nothing(self):
        with patch.object(otel, "_configured", False), patch.object(
            otel.settings, "OTEL_ENABLED", False
        ), patch.object(otel, "_instrument_app") as instrument:
            otel.setup_observability(object())

        instrument.assert_not_called()


class DependencyNameTest(unittest.TestCase):
    def test_known_service_is_recognised_by_prefix(self):
        url = f"{settings.WATCHTOWER_URL}/api/v1/cloud/bucket/file"
        self.assertEqual(dependency_name(url), "watchtower")

    def test_unknown_host_falls_back_to_hostname(self):
        self.assertEqual(dependency_name("http://example.test:8080/x"), "example.test")


class HTTPMetricsMiddlewareTest(unittest.TestCase):
    def build_app(self) -> FastAPI:
        app = FastAPI()
        app.add_middleware(HTTPMetricsMiddleware)

        @app.get("/items/{item_id}")
        async def read_item(item_id: str):
            return {"item_id": item_id}

        return app

    def test_route_template_is_used_instead_of_raw_path(self):
        """Сырой путь дал бы новый временной ряд на каждый item_id."""
        with recording_instruments() as meter:
            with TestClient(self.build_app()) as client:
                client.get("/items/42")
                client.get("/items/43")

        recorded = attrs_for(meter, "document_parser.http.requests")
        self.assertEqual({item["route"] for item in recorded}, {"/items/{item_id}"})
        self.assertEqual({item["status"] for item in recorded}, {"200"})

    def test_unmatched_path_is_grouped(self):
        with recording_instruments() as meter:
            with TestClient(self.build_app()) as client:
                client.get("/no-such-route")

        recorded = attrs_for(meter, "document_parser.http.requests")
        self.assertEqual(recorded[0]["route"], "unmatched")
        self.assertEqual(recorded[0]["status"], "404")


class ConsumerMetricsTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        RecordingHandler.outcome = HandlerOutcome(TaskStatus.READY)
        RecordingHandler.error = None
        RecordingHandler.calls = []

    async def test_successful_message_is_counted_as_processed(self):
        consumer = build_consumer()
        with recording_instruments() as meter:
            await consumer._process_message(FakeMessage())

        recorded = attrs_for(meter, "document_parser.broker.messages")
        self.assertEqual(recorded[0]["outcome"], "processed")
        self.assertEqual(recorded[0]["task_type"], "document-parser.translate")
        self.assertIn("document_parser.broker.processing.duration_seconds", names(meter))

    async def test_transient_failure_is_counted_as_retried(self):
        RecordingHandler.error = WatchtowerUnavailable("хранилище недоступно")
        consumer = build_consumer()
        with recording_instruments() as meter:
            await consumer._process_message(FakeMessage())

        recorded = attrs_for(meter, "document_parser.broker.messages")
        self.assertEqual(recorded[0]["outcome"], "retried")
        self.assertEqual(recorded[0]["error_type"], "WatchtowerUnavailable")

    async def test_unparsable_body_is_counted_as_dlq_with_unknown_type(self):
        consumer = build_consumer()
        with recording_instruments() as meter:
            await consumer._process_message(FakeMessage(body=b"not json"))

        recorded = attrs_for(meter, "document_parser.broker.messages")
        self.assertEqual(recorded[0]["outcome"], "dlq")
        self.assertEqual(recorded[0]["task_type"], "unknown")

    async def test_state_gauges_follow_consumer_state(self):
        consumer = build_consumer()
        consumer._started = True
        consumer._retry_queue_ready = False
        with recording_instruments() as meter:
            consumer._record_state()

        by_name = {name: (amount, attrs) for name, amount, attrs in meter.calls}
        self.assertEqual(by_name["document_parser.broker.consuming"][0], 1)
        # Соединения в фикстуре нет — значит и метрика обязана это показать.
        self.assertEqual(by_name["document_parser.broker.connected"][0], 0)
        topology = [
            (amount, attrs["object"])
            for name, amount, attrs in meter.calls
            if name == "document_parser.broker.topology.ready"
        ]
        self.assertEqual(topology, [(1, "dlq"), (0, "retry")])


if __name__ == "__main__":
    unittest.main()
