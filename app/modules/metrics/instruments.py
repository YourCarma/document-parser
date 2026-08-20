"""Определения инструментов: единственное место, где заводятся метрики.

Два правила, от которых зависит совместимость дашборда с обоими путями
экспорта (OTLP push и Prometheus pull):

1. Единица измерения зашита в имя (`_seconds`, `_bytes`), а поле `unit`
   оставлено пустым. И Prometheus-ридер, и коллектор дописывают суффикс
   единицы к имени сами, делают это по-разному от версии к версии — с пустым
   `unit` имя в Prometheus получается предсказуемым и одинаковым на обоих
   путях, и запросы дашборда не разъезжаются.
2. Атрибуты только с ограниченным множеством значений. Никаких task_id,
   user_id и имён файлов: каждое новое значение — отдельный временной ряд.

Модуль обязан импортироваться и работать без установленного OpenTelemetry:
пакеты наблюдаемости необязательные, и их отсутствие не имеет права ронять
сервис. Без них все инструменты становятся заглушками.
"""

from contextlib import contextmanager
from typing import Any, Callable, Iterable

try:  # pragma: no cover - зависит от окружения, а не от логики
    from opentelemetry import metrics as _otel_metrics
    from opentelemetry import trace as _otel_trace

    OTEL_AVAILABLE = True
except ImportError:  # pragma: no cover
    _otel_metrics = None
    _otel_trace = None
    OTEL_AVAILABLE = False


METER_NAME = "document-parser"
TRACER_NAME = "document-parser"


# --- Заглушки на случай отсутствия пакетов --------------------------------


class _NoOpInstrument:
    """Инструмент, который молча съедает любые измерения."""

    def add(self, amount: float, attributes: dict | None = None) -> None:
        pass

    def record(self, amount: float, attributes: dict | None = None) -> None:
        pass

    def set(self, amount: float, attributes: dict | None = None) -> None:
        pass


class _NoOpSpan:
    def set_attribute(self, *_args, **_kwargs) -> None:
        pass

    def set_status(self, *_args, **_kwargs) -> None:
        pass

    def record_exception(self, *_args, **_kwargs) -> None:
        pass

    def end(self, *_args, **_kwargs) -> None:
        pass

    def is_recording(self) -> bool:
        return False

    def __enter__(self) -> "_NoOpSpan":
        return self

    def __exit__(self, *_exc) -> bool:
        return False


class _NoOpTracer:
    def start_span(self, *_args, **_kwargs) -> _NoOpSpan:
        return _NoOpSpan()

    @contextmanager
    def start_as_current_span(self, *_args, **_kwargs):
        yield _NoOpSpan()


class _NoOpMeter:
    def _instrument(self, *_args, **_kwargs) -> _NoOpInstrument:
        return _NoOpInstrument()

    create_counter = _instrument
    create_up_down_counter = _instrument
    create_histogram = _instrument
    create_gauge = _instrument
    create_observable_gauge = _instrument
    create_observable_up_down_counter = _instrument


def get_meter():
    """Метер SDK либо заглушка."""
    if not OTEL_AVAILABLE:
        return _NoOpMeter()
    return _otel_metrics.get_meter(METER_NAME)


def get_tracer():
    """Трейсер SDK либо заглушка."""
    if not OTEL_AVAILABLE:
        return _NoOpTracer()
    return _otel_trace.get_tracer(TRACER_NAME)


def observation(value: float, attributes: dict | None = None):
    """`Observation` для колбэков наблюдаемых метрик."""
    if not OTEL_AVAILABLE:
        return None
    return _otel_metrics.Observation(value, attributes or {})


# --- Границы гистограмм ----------------------------------------------------

# Дефолтные границы SDK заканчиваются на 10 (они рассчитаны на миллисекунды).
# Здесь всё в секундах, а задача перевода живёт до TASK_TIMEOUT_SECS=3000 —
# без своих границ все наблюдения свалились бы в последний бакет и p95 стал
# бы бессмысленным.
FAST_BUCKETS: tuple[float, ...] = (
    0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60,
)
SLOW_BUCKETS: tuple[float, ...] = (
    0.5, 1, 5, 10, 30, 60, 120, 300, 600, 900, 1800, 3000, 5400,
)

# Имя инструмента -> границы. Читается при сборке `View` в otel.py, то есть
# до создания самих инструментов — поэтому таблица статическая, а не
# заполняется в конструкторе.
HISTOGRAM_BUCKETS: dict[str, tuple[float, ...]] = {
    "document_parser.broker.processing.duration_seconds": SLOW_BUCKETS,
    "document_parser.task.duration_seconds": SLOW_BUCKETS,
    "document_parser.task.stage.duration_seconds": SLOW_BUCKETS,
    "document_parser.http.duration_seconds": SLOW_BUCKETS,
    "document_parser.dependency.duration_seconds": FAST_BUCKETS,
}

# Имена заведённых гистограмм: сверяется с таблицей границ в тестах, чтобы
# новая гистограмма не осталась с дефолтными бакетами «до 10».
DECLARED_HISTOGRAMS: set[str] = set()


class Instruments:
    """Все метрики сервиса. Создаётся один раз, лениво."""

    def __init__(self, meter) -> None:
        def histogram(name: str, description: str):
            DECLARED_HISTOGRAMS.add(name)
            return meter.create_histogram(name, unit="", description=description)

        # --- Очередь ---
        self.broker_messages = meter.create_counter(
            "document_parser.broker.messages",
            unit="",
            description="Сообщения очереди по исходу обработки",
        )
        self.broker_processing_seconds = histogram(
            "document_parser.broker.processing.duration_seconds",
            "Время от получения сообщения до ack/nack",
        )
        self.broker_inflight = meter.create_up_down_counter(
            "document_parser.broker.inflight",
            unit="",
            description="Сообщения, обрабатываемые прямо сейчас",
        )
        self.broker_dlq_depth = meter.create_gauge(
            "document_parser.broker.dlq.depth",
            unit="",
            description="Сообщений в DLQ по данным сторожа (-1 — опрос не удался)",
        )
        self.broker_consuming = meter.create_gauge(
            "document_parser.broker.consuming",
            unit="",
            description="1 — подписка активна, 0 — нет",
        )
        self.broker_connected = meter.create_gauge(
            "document_parser.broker.connected",
            unit="",
            description="1 — соединение с брокером живо, 0 — нет",
        )
        self.broker_topology_ready = meter.create_gauge(
            "document_parser.broker.topology.ready",
            unit="",
            description="Готовность наших объектов: dlq и retry-очередь",
        )

        # --- Задачи перевода ---
        self.tasks = meter.create_counter(
            "document_parser.tasks",
            unit="",
            description="Завершённые задачи перевода по терминальному статусу",
        )
        self.task_seconds = histogram(
            "document_parser.task.duration_seconds",
            "Полное время задачи перевода",
        )
        self.task_stage_seconds = histogram(
            "document_parser.task.stage.duration_seconds",
            "Время этапа конвейера перевода",
        )
        self.translate_items = meter.create_counter(
            "document_parser.translate.items",
            unit="",
            description="Элементы документа, отданные переводчику",
        )

        # --- Внешние зависимости ---
        self.dependency_requests = meter.create_counter(
            "document_parser.dependency.requests",
            unit="",
            description="HTTP-запросы к внешним сервисам по исходу",
        )
        self.dependency_seconds = histogram(
            "document_parser.dependency.duration_seconds",
            "Время ответа внешнего сервиса",
        )

        # --- Свой HTTP-API ---
        self.http_requests = meter.create_counter(
            "document_parser.http.requests",
            unit="",
            description="Запросы к API сервиса",
        )
        self.http_seconds = histogram(
            "document_parser.http.duration_seconds",
            "Время обработки HTTP-запроса",
        )


_instruments: Instruments | None = None


def instruments() -> Instruments:
    """Ленивый синглтон инструментов."""
    global _instruments
    if _instruments is None:
        _instruments = Instruments(get_meter())
    return _instruments


def override_instruments(instance: Instruments | None) -> Instruments | None:
    """Подставить набор инструментов, вернуть предыдущий.

    Нужно тестам: имя `modules.metrics.instruments` в пакете занято функцией
    выше, и добираться до модуля ради подмены пришлось бы через importlib.
    """
    global _instruments
    previous, _instruments = _instruments, instance
    return previous


def reset_instruments() -> None:
    """Пересоздать инструменты на новом метере.

    Нужно после установки провайдера: инструменты, созданные до него, иначе
    остались бы привязанными к прокси-метеру.
    """
    global _instruments
    _instruments = None


def observable_callbacks() -> Iterable[tuple[str, str, Callable[[Any], list]]]:
    """Наблюдаемые метрики: имя, описание, колбэк.

    Отдельно от `Instruments`, потому что их значения не измеряются в коде, а
    опрашиваются экспортёром раз в интервал — импорт реестра здесь ленивый,
    чтобы не тянуть runtime в модуль метрик.
    """
    from modules.metrics.registry import (
        observe_process_pools,
        observe_semaphores,
    )

    return (
        (
            "document_parser.semaphore.in_use",
            "Занятые слоты семафора",
            lambda options: observe_semaphores("in_use"),
        ),
        (
            "document_parser.semaphore.waiting",
            "Корутины в очереди за слотом семафора",
            lambda options: observe_semaphores("waiting"),
        ),
        (
            "document_parser.semaphore.capacity",
            "Размер семафора",
            lambda options: observe_semaphores("capacity"),
        ),
        (
            "document_parser.process_pool.generation",
            "Поколение пула процессов: рост означает пересборку после гибели воркера",
            lambda options: observe_process_pools(),
        ),
    )
