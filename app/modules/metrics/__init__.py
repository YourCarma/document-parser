"""Наблюдаемость сервиса: метрики и трейсы поверх OpenTelemetry.

Пакеты `opentelemetry-*` необязательные, а `OTEL_ENABLED` по умолчанию
выключен: без них модуль превращается в набор заглушек и ничего не стоит.
Конфигурация коллектора, Prometheus и дашборд Grafana лежат в `metrics/`.
"""

from modules.metrics.concurrency import TrackedSemaphore
from modules.metrics.http import (
    HTTPMetricsMiddleware,
    dependency_name,
    dependency_trace_config,
)
from modules.metrics.instruments import (
    OTEL_AVAILABLE,
    Instruments,
    get_tracer,
    instruments,
    override_instruments,
)
from modules.metrics.otel import setup_observability, shutdown_observability
from modules.metrics.registry import register_process_pool, register_semaphore
from modules.metrics.stages import StageTracker

__all__ = [
    "HTTPMetricsMiddleware",
    "Instruments",
    "OTEL_AVAILABLE",
    "StageTracker",
    "TrackedSemaphore",
    "dependency_name",
    "dependency_trace_config",
    "get_tracer",
    "instruments",
    "override_instruments",
    "register_process_pool",
    "register_semaphore",
    "setup_observability",
    "shutdown_observability",
]
