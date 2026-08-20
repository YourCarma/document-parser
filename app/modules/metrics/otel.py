"""Инициализация OpenTelemetry: метрики и трейсы.

Два экспортных пути работают одновременно и независимо:

* OTLP push (HTTP/protobuf) в коллектор — основной путь и для метрик, и для
  трейсов;
* Prometheus pull на отдельном порту — чтобы метрики можно было увидеть
  глазами при отладке и снять скрейпом без коллектора.

Всё здесь — best-effort: любая ошибка настройки наблюдаемости пишется в лог,
но не мешает сервису работать. Не собирать метрики хуже, чем собирать, но
несравнимо лучше, чем не обрабатывать документы.
"""

import os
import socket

from loguru import logger

from modules.metrics.instruments import (
    HISTOGRAM_BUCKETS,
    METER_NAME,
    get_meter,
    observable_callbacks,
    reset_instruments,
)
from settings import settings


_configured: bool = False
_disabled_logged: bool = False
_meter_provider = None
_tracer_provider = None


def _parse_headers(raw: str) -> dict[str, str]:
    """`k1=v1,k2=v2` -> словарь. Мусорные пары молча пропускаются."""
    headers: dict[str, str] = {}
    for chunk in str(raw or "").split(","):
        if "=" not in chunk:
            continue
        key, value = chunk.split("=", 1)
        key = key.strip()
        if key:
            headers[key] = value.strip()
    return headers


def _endpoint(signal: str) -> str:
    """Полный URL сигнала: экспортёру нужен путь, а не только база."""
    return f"{settings.OTEL_EXPORTER_OTLP_ENDPOINT.rstrip('/')}/v1/{signal}"


def setup_observability(app=None) -> None:
    """Поднять провайдеры и авто-инструментацию.

    Вызывать до старта приложения: авто-инструментация FastAPI добавляет
    мидлварь, а после старта стек мидлварей уже собран.

    Провайдеры поднимаются один раз на процесс, а вот `app` инструментируется
    при каждом вызове. Так надо: `python main.py` импортирует модуль дважды —
    сначала как `__main__`, затем uvicorn по строке `"main:app"` — и запросы
    обслуживает второй объект приложения. Общий на процесс флаг «уже
    настроено» оставил бы его без трейсов, хотя метрики бы работали.
    """
    global _configured, _disabled_logged, _meter_provider, _tracer_provider

    if not settings.OTEL_ENABLED:
        if not _disabled_logged:
            _disabled_logged = True
            logger.info("Observability: disabled (OTEL_ENABLED=false)")
        return

    try:
        from opentelemetry import metrics, trace
        from opentelemetry.sdk.resources import Resource
    except ImportError as exc:
        if not _disabled_logged:
            _disabled_logged = True
            logger.warning(
                "Observability: opentelemetry packages are not installed ({}), "
                "metrics and traces are disabled",
                exc,
            )
        return

    if _configured:
        _instrument_app(app)
        return

    resource = Resource.create(
        {
            "service.name": settings.SERVICE_NAME,
            "service.version": settings.OTEL_SERVICE_VERSION,
            "service.namespace": "sova",
            "deployment.environment": settings.DEPLOY_ENVIRONMENT,
            # Реплик может быть несколько, и различать их обязательно: иначе
            # сумма по подам выглядит как один странно себя ведущий процесс.
            "service.instance.id": f"{socket.gethostname()}:{os.getpid()}",
        }
    )

    # Два независимых переключателя: push в коллектор и pull-эндпоинт. Провайдер
    # нужен, если включён хотя бы один из них.
    if settings.OTEL_METRICS_ENABLED or settings.METRICS_HTTP_ENABLED:
        _meter_provider = _setup_metrics(metrics, resource)
    if settings.OTEL_TRACES_ENABLED:
        _tracer_provider = _setup_traces(trace, resource)

    _instrument_libraries()
    _configured = True
    _instrument_app(app)
    logger.success(
        "Observability: enabled service='{}' env='{}' otlp='{}' metrics_port={}",
        settings.SERVICE_NAME,
        settings.DEPLOY_ENVIRONMENT,
        settings.OTEL_EXPORTER_OTLP_ENDPOINT if settings.OTEL_METRICS_ENABLED
        or settings.OTEL_TRACES_ENABLED else "off",
        settings.METRICS_HTTP_PORT if settings.METRICS_HTTP_ENABLED else "off",
    )


def _setup_metrics(metrics, resource):
    """MeterProvider с OTLP-ридером и Prometheus-ридером."""
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.metrics.view import (
        ExplicitBucketHistogramAggregation,
        View,
    )

    readers = []

    if settings.OTEL_METRICS_ENABLED:
        try:
            from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
                OTLPMetricExporter,
            )

            readers.append(
                PeriodicExportingMetricReader(
                    OTLPMetricExporter(
                        endpoint=_endpoint("metrics"),
                        headers=_parse_headers(settings.OTEL_EXPORTER_OTLP_HEADERS),
                        timeout=settings.OTEL_EXPORT_TIMEOUT_SECS,
                    ),
                    export_interval_millis=settings.OTEL_METRIC_EXPORT_INTERVAL_MS,
                )
            )
        except Exception as exc:
            logger.warning(
                "Observability: OTLP metric exporter is unavailable: {}", exc
            )

    if settings.METRICS_HTTP_ENABLED:
        try:
            from opentelemetry.exporter.prometheus import PrometheusMetricReader
            from prometheus_client import start_http_server

            readers.append(PrometheusMetricReader())
            # Свой порт, а не роут в FastAPI: метрики должны отдаваться и
            # тогда, когда приложение занято длинным парсингом, и их не
            # обязательно выставлять наружу вместе с API.
            start_http_server(
                port=settings.METRICS_HTTP_PORT, addr=settings.METRICS_HTTP_HOST
            )
        except OSError as exc:
            # Занятый порт — обычное дело при reload и нескольких воркерах.
            logger.warning(
                "Observability: metrics port {} is busy ({}), pull endpoint is off",
                settings.METRICS_HTTP_PORT,
                exc,
            )
        except Exception as exc:
            logger.warning("Observability: Prometheus reader is unavailable: {}", exc)

    if not readers:
        logger.warning("Observability: no metric readers configured")
        return None

    views = [
        View(
            instrument_name=name,
            aggregation=ExplicitBucketHistogramAggregation(boundaries=list(buckets)),
        )
        for name, buckets in HISTOGRAM_BUCKETS.items()
    ]

    provider = MeterProvider(resource=resource, metric_readers=readers, views=views)
    metrics.set_meter_provider(provider)
    # Инструменты, созданные до провайдера, остались бы на прокси-метере.
    reset_instruments()
    _register_observables()
    return provider


def _register_observables() -> None:
    """Наблюдаемые метрики: их значения опрашиваются экспортёром."""
    meter = get_meter()
    for name, description, callback in observable_callbacks():
        try:
            meter.create_observable_gauge(
                name, callbacks=[callback], unit="", description=description
            )
        except Exception as exc:
            logger.warning(
                "Observability: failed to register the observable '{}': {}", name, exc
            )


def _setup_traces(trace, resource):
    """TracerProvider с батч-экспортом в OTLP."""
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased

    provider = TracerProvider(
        resource=resource,
        sampler=ParentBased(TraceIdRatioBased(settings.OTEL_TRACES_SAMPLER_RATIO)),
    )
    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )

        provider.add_span_processor(
            BatchSpanProcessor(
                OTLPSpanExporter(
                    endpoint=_endpoint("traces"),
                    headers=_parse_headers(settings.OTEL_EXPORTER_OTLP_HEADERS),
                    timeout=settings.OTEL_EXPORT_TIMEOUT_SECS,
                )
            )
        )
    except Exception as exc:
        logger.warning("Observability: OTLP span exporter is unavailable: {}", exc)

    trace.set_tracer_provider(provider)
    return provider


def _instrument_app(app) -> None:
    """Инструментировать конкретный объект FastAPI-приложения.

    Повторный вызов на уже инструментированном приложении безопасен: сам
    инструментор это отслеживает.
    """
    if app is None:
        return
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        # /health опрашивает оркестратор раз в несколько секунд, /metrics —
        # сами метрики: спаны по ним не несут информации, но заметно шумят.
        FastAPIInstrumentor.instrument_app(app, excluded_urls="health,metrics")
    except Exception as exc:
        logger.warning("Observability: FastAPI instrumentation failed: {}", exc)


def _instrument_libraries() -> None:
    """Глобальная авто-инструментация: aiohttp и aio-pika.

    Каждая в своём try: отсутствие одного пакета не должно лишать трейсов
    остальные.
    """
    try:
        from opentelemetry.instrumentation.aiohttp_client import (
            AioHttpClientInstrumentor,
        )

        AioHttpClientInstrumentor().instrument()
    except Exception as exc:
        logger.warning("Observability: aiohttp instrumentation failed: {}", exc)

    try:
        from opentelemetry.instrumentation.aio_pika import AioPikaInstrumentor

        AioPikaInstrumentor().instrument()
    except Exception as exc:
        logger.warning("Observability: aio-pika instrumentation failed: {}", exc)


def shutdown_observability() -> None:
    """Дослать накопленное и погасить провайдеры. Идемпотентно, не бросает."""
    global _configured, _disabled_logged, _meter_provider, _tracer_provider

    if _tracer_provider is not None:
        try:
            # force_flush до shutdown: иначе последние спаны упавшей задачи
            # так и остались бы в батче.
            _tracer_provider.force_flush(
                timeout_millis=settings.OTEL_SHUTDOWN_TIMEOUT_MS
            )
            _tracer_provider.shutdown()
        except Exception as exc:
            logger.warning(
                "Observability: failed to shut down the tracer provider: {}", exc
            )

    if _meter_provider is not None:
        try:
            # Без force_flush: Prometheus-ридер его не поддерживает и на каждой
            # остановке отвечал бы таймаутом. Периодический ридер и так делает
            # последний сбор внутри shutdown().
            _meter_provider.shutdown()
        except Exception as exc:
            logger.warning(
                "Observability: failed to shut down the meter provider: {}", exc
            )

    _tracer_provider = None
    _meter_provider = None
    _configured = False
    _disabled_logged = False
