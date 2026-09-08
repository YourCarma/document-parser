"""Метрики HTTP: свой API и вызовы внешних сервисов.

Клиентские метрики снимаются одним `TraceConfig` на общей сессии из
`AppRuntime` — так они появляются сразу у всех клиентов (watchtower,
webhook_manager, resource_manager, переводчик, определитель языка), и ни один
из них не приходится править. Клиенты, создающие сессию сами (fallback, когда
общая не передана), в метрики не попадают.
"""

import time
from urllib.parse import urlsplit

import aiohttp

from modules.metrics.instruments import instruments
from settings import settings


_DEPENDENCIES: tuple[tuple[str, str], ...] | None = None


def _dependency_prefixes() -> tuple[tuple[str, str], ...]:
    """Пары «префикс URL — имя зависимости». Настройки при работе не меняются."""
    global _DEPENDENCIES
    if _DEPENDENCIES is None:
        raw = (
            (settings.WEBHOOK_MANAGER_URL, "webhook_manager"),
            (settings.WATCHTOWER_URL, "watchtower"),
            (settings.RESOURCE_MANAGER_URL, "resource_manager"),
            (settings.TRANSLATOR_ADDRESS, "translator"),
            (settings.DETECT_LANGUAGE_URL, "language_detector"),
            (settings.VLM_BASE_URL, "vlm"),
        )
        # Длинные префиксы первыми: иначе общий хост перехватил бы совпадение.
        _DEPENDENCIES = tuple(
            sorted(
                ((str(url).rstrip("/"), name) for url, name in raw if url),
                key=lambda item: len(item[0]),
                reverse=True,
            )
        )
    return _DEPENDENCIES


def dependency_name(url: str) -> str:
    """Имя зависимости по URL. Незнакомый адрес — по хосту."""
    text = str(url)
    for prefix, name in _dependency_prefixes():
        if text.startswith(prefix):
            return name
    host = urlsplit(text).hostname
    return host or "unknown"


def _status_outcome(status: int) -> str:
    if status >= 500:
        return "server_error"
    if status >= 400:
        return "client_error"
    return "ok"


def dependency_trace_config() -> aiohttp.TraceConfig:
    """`TraceConfig` для общей сессии: длительность и исход каждого запроса."""
    trace_config = aiohttp.TraceConfig()

    async def on_start(_session, context, params):
        context.metrics_started = time.monotonic()
        context.metrics_dependency = dependency_name(params.url)
        context.metrics_method = params.method

    def record(context, outcome: str, status: str) -> None:
        started = getattr(context, "metrics_started", None)
        if started is None:
            return
        attributes = {
            "dependency": getattr(context, "metrics_dependency", "unknown"),
            "method": getattr(context, "metrics_method", "GET"),
        }
        instruments().dependency_requests.add(1, {**attributes, "status": status})
        instruments().dependency_seconds.record(
            time.monotonic() - started, {**attributes, "outcome": outcome}
        )

    async def on_end(_session, context, params):
        status_code = params.response.status
        record(context, _status_outcome(status_code), str(status_code))

    async def on_exception(_session, context, params):
        record(context, "error", type(params.exception).__name__)

    trace_config.on_request_start.append(on_start)
    trace_config.on_request_end.append(on_end)
    trace_config.on_request_exception.append(on_exception)
    return trace_config


class HTTPMetricsMiddleware:
    """ASGI-мидлварь с метриками своего API.

    Именно ASGI, а не `BaseHTTPMiddleware`: парсер отдаёт файлы стримом, и
    оборачивать их лишним слоем ради счётчика незачем.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        started = time.monotonic()
        # 500 по умолчанию: если ответ не начался, запрос упал в мидлварях.
        state = {"status": 500}

        async def send_wrapper(message):
            if message.get("type") == "http.response.start":
                state["status"] = message.get("status", 500)
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            # Шаблон маршрута, а не сырой путь: сырой путь дал бы новый
            # временной ряд на каждый несуществующий URL.
            route = getattr(scope.get("route"), "path", None) or "unmatched"
            status = state["status"]
            attributes = {"method": scope.get("method", "GET"), "route": route}
            instruments().http_requests.add(
                1, {**attributes, "status": str(status)}
            )
            instruments().http_seconds.record(
                time.monotonic() - started,
                {**attributes, "outcome": _status_outcome(status)},
            )
