"""Общие ресурсы процесса, не привязанные к HTTP-транспорту.

Одна `ClientSession`, один пул процессов, общие семафоры — и HTTP-роутеры, и
консюмер очереди пользуются одними и теми же объектами.
"""

import asyncio
from dataclasses import dataclass

import aiohttp
from loguru import logger

from modules.metrics import (
    TrackedSemaphore,
    dependency_trace_config,
    register_process_pool,
)
from modules.parser.v1.process_pool import ProcessPoolHolder
from modules.resource_manager.service import ResourceManagerService
from modules.watchtower.service import WatchtowerService
from modules.webhook_manager.service import WebhookManagerService
from settings import settings


@dataclass(slots=True)
class AppRuntime:
    """Общие ресурсы процесса: одна сессия, один пул, общие семафоры."""

    http_session: aiohttp.ClientSession
    executor: ProcessPoolHolder
    parser_semaphore: asyncio.Semaphore
    translation_semaphore: asyncio.Semaphore

    @classmethod
    def create(cls) -> "AppRuntime":
        """Собрать ресурсы. Вызывать только внутри работающего event loop."""
        timeout = aiohttp.ClientTimeout(
            total=None,
            connect=settings.EXTERNAL_CONNECT_TIMEOUT_SECS,
            sock_read=settings.EXTERNAL_READ_TIMEOUT_SECS,
        )
        connector = aiohttp.TCPConnector(
            limit=settings.EXTERNAL_HTTP_CONNECTION_LIMIT,
        )
        executor = ProcessPoolHolder(max_workers=settings.PARSER_WORKERS)
        # Пул опрашивается наблюдаемой метрикой: рост поколения означает, что
        # воркер погиб и пул пересобрали.
        register_process_pool(executor)
        return cls(
            http_session=aiohttp.ClientSession(
                timeout=timeout,
                connector=connector,
                # Один trace_config на общую сессию даёт метрики всех
                # исходящих вызовов сразу, без правок в клиентах.
                trace_configs=[dependency_trace_config()],
            ),
            executor=executor,
            parser_semaphore=TrackedSemaphore(settings.PARSER_WORKERS, "parser"),
            translation_semaphore=TrackedSemaphore(
                settings.TRANSLATOR_MAX_CONCURRENCY, "translation"
            ),
        )

    def attach(self, app) -> None:
        """Разложить ресурсы по `app.state` под историческими именами.

        На `executor`, `parser_semaphore`, `translation_semaphore` и
        `http_session` завязаны три роутера — переименовывать нельзя.
        """
        app.state.executor = self.executor
        app.state.parser_semaphore = self.parser_semaphore
        app.state.translation_semaphore = self.translation_semaphore
        app.state.http_session = self.http_session

    def webhook_manager(self) -> WebhookManagerService:
        """Тонкая обёртка над общей сессией, новых сессий не появляется."""
        return WebhookManagerService(
            settings.WEBHOOK_MANAGER_URL, session=self.http_session
        )

    def watchtower(self) -> WatchtowerService:
        return WatchtowerService(settings.WATCHTOWER_URL, session=self.http_session)

    def resource_manager(self) -> ResourceManagerService:
        return ResourceManagerService(
            settings.RESOURCE_MANAGER_URL, session=self.http_session
        )

    async def shutdown(self) -> None:
        """Закрыть сессию и погасить пул. Идемпотентно, не бросает."""
        try:
            if self.http_session is not None and not self.http_session.closed:
                await self.http_session.close()
        except Exception as exc:
            logger.warning("Runtime: failed to close the HTTP session: {}", exc)
        try:
            await self.executor.shutdown(wait=True, cancel_futures=True)
        except Exception as exc:
            logger.warning("Runtime: failed to shut down the process pool: {}", exc)
