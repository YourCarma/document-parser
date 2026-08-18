import asyncio
import time
from abc import ABC, abstractmethod

from loguru import logger

from modules.webhook_manager.schemas import TaskStatus
from modules.webhook_manager.service import WebhookManagerService
from settings import settings


class TaskCancelled(Exception):
    """Задача отменена пользователем. Пиклюемое исключение с непустым args."""

    def __init__(self, task_key: str, stage: str = ""):
        self.task_key = task_key
        self.stage = stage
        super().__init__(
            f"Задача '{task_key}' отменена (этап: {stage or 'не указан'})"
        )


class CancellationTokenABC(ABC):
    """Источник ответа на вопрос «задачу уже отменили?»."""

    task_key: str

    @abstractmethod
    async def is_cancelled(self) -> bool:
        """Никогда не бросает: любая ошибка опроса означает «не отменена»."""

    async def raise_if_cancelled(self, stage: str = "") -> None:
        if await self.is_cancelled():
            raise TaskCancelled(self.task_key, stage)


class NullCancellationToken(CancellationTokenABC):
    """Заглушка для сценариев без отмены (HTTP-вход, тесты)."""

    def __init__(self, task_key: str = ""):
        self.task_key = task_key

    async def is_cancelled(self) -> bool:
        return False


class WebhookCancellationToken(CancellationTokenABC):
    """Отмена по статусу задачи в webhook_manager.

    Отрицательный ответ кэшируется на TTL, положительный — липкий: после
    первого `True` в сеть больше не ходим.
    """

    def __init__(
        self,
        webhook: WebhookManagerService,
        task_key: str,
        ttl_secs: float | None = None,
    ):
        self._webhook = webhook
        self.task_key = task_key
        self._ttl_secs = (
            settings.TASK_CANCEL_CHECK_TTL_SECS if ttl_secs is None else ttl_secs
        )
        self._cancelled = False
        self._checked_at = 0.0
        self._lock = asyncio.Lock()

    async def is_cancelled(self) -> bool:
        if self._cancelled:
            return True
        if time.monotonic() - self._checked_at < self._ttl_secs:
            return False

        async with self._lock:
            # Double-check: пока ждали лок, соседняя корутина могла уже
            # сходить в webhook_manager.
            if self._cancelled:
                return True
            if time.monotonic() - self._checked_at < self._ttl_secs:
                return False

            try:
                task = await self._webhook.get_task(self.task_key)
            except Exception as exc:
                # Сбой опроса — не повод убивать задачу.
                logger.warning(
                    "Cancellation: failed to check for cancellation key='{}': {}",
                    self.task_key,
                    exc,
                )
                self._checked_at = time.monotonic()
                return False

            if task is None:
                logger.warning(
                    "Cancellation: task not found in webhook_manager key='{}'",
                    self.task_key,
                )
                self._checked_at = time.monotonic()
                return False

            if task.progress.status == TaskStatus.CANCELLED:
                self._cancelled = True
                logger.info("Cancellation: task cancelled key='{}'", self.task_key)
                return True

            self._checked_at = time.monotonic()
            return False


def create_cancellation_token(
    webhook: WebhookManagerService | None,
    task_key: str,
    ttl_secs: float | None = None,
) -> CancellationTokenABC:
    """Собрать токен отмены; без клиента webhook_manager — заглушку."""
    if webhook is None or not task_key:
        return NullCancellationToken(task_key)
    return WebhookCancellationToken(webhook, task_key, ttl_secs)
