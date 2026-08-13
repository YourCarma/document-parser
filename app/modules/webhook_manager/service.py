import asyncio
import json
from datetime import datetime, timezone

import aiohttp
from loguru import logger
from pydantic import ValidationError

from modules.webhook_manager.schemas import (
    Task, TaskProgress, TaskStatus,
    TaskCreationV2, ProgressUpdate, ResponseDataUpdate,
)
from settings import settings


_RETRY_ATTEMPTS: int = 3
_RETRY_BASE_DELAY_SECS: float = 0.5


class _TransientWebhookError(Exception):
    """5xx от webhook_manager: сбой, который имеет смысл повторить."""


class WebhookManagerService:
    """Клиент webhook_manager для создания и обновления async-задач."""

    def __init__(
        self,
        base_url: str,
        session: aiohttp.ClientSession | None = None,
    ):
        self.base_url = base_url
        self.session = session

    async def _with_session(self, operation):
        if self.session is not None:
            return await operation(self.session)
        async with aiohttp.ClientSession() as session:
            return await operation(session)

    async def _with_retries(
        self,
        operation,
        description: str,
        attempts: int = _RETRY_ATTEMPTS,
    ):
        """Повторить операцию при временном сбое webhook_manager.

        Повторяются только сетевые сбои и 5xx. Ошибки клиента (4xx) и любые
        прочие исключения уходят наружу сразу — повтор их не исправит.
        """
        last_exc: Exception | None = None
        for attempt in range(attempts):
            try:
                return await self._with_session(operation)
            except (
                _TransientWebhookError,
                aiohttp.ClientError,
                asyncio.TimeoutError,
            ) as exc:
                last_exc = exc
                if attempt + 1 >= attempts:
                    break
                delay = _RETRY_BASE_DELAY_SECS * 2 ** attempt
                logger.warning(
                    "WebhookManager: {} — попытка {}/{} не удалась ({}), "
                    "повтор через {:.1f} с",
                    description,
                    attempt + 1,
                    attempts,
                    exc,
                    delay,
                )
                await asyncio.sleep(delay)
        logger.error(
            "WebhookManager: {} — попытки исчерпаны ({}): {}",
            description,
            attempts,
            last_exc,
        )
        raise last_exc

    def _make_key(self, user_id: str, task_id: str) -> str:
        return f"{user_id}:{settings.SERVICE_NAME}:{task_id}"

    async def create_task(self, user_id: str, task_id: str, response_data: dict) -> str:
        """Создать запись о задаче и вернуть её составной ключ."""
        key = self._make_key(user_id, task_id)
        now = datetime.now(timezone.utc)
        task = Task(
            task_id=task_id,
            user_id=user_id,
            service=settings.SERVICE_NAME,
            progress=TaskProgress(progress=0, status=TaskStatus.PENDING),
            created_at=now,
            updated_at=now,
            response_data=json.dumps(response_data, ensure_ascii=False),
        )
        payload = TaskCreationV2(task=task)
        async def request(session: aiohttp.ClientSession):
            async with session.post(
                f"{self.base_url}/api/v2/storage/task",
                json=payload.model_dump(mode="json"),
            ) as resp:
                body = await resp.text()
                if resp.status >= 500:
                    raise _TransientWebhookError(
                        f"WebhookManager create_task вернул [{resp.status}] "
                        f"для key='{key}': {body}"
                    )
                if resp.status not in (200, 201):
                    raise Exception(
                        f"WebhookManager create_task вернул [{resp.status}] "
                        f"для key='{key}': {body}"
                    )
                logger.info("WebhookManager: задача создана key='{}'", key)
        await self._with_retries(request, f"create_task key='{key}'")
        return key

    async def update_progress(
        self,
        key: str,
        progress: float,
        status: TaskStatus,
        attempts: int = _RETRY_ATTEMPTS,
    ):
        """Обновить progress/status асинхронной задачи.

        `attempts=1` — для промежуточных публикаций: они best-effort, и платить
        за них backoff-паузами внутри цикла перевода незачем.
        """
        payload = ProgressUpdate(
            key=key,
            progress=TaskProgress(progress=progress, status=status),
        )
        async def request(session: aiohttp.ClientSession):
            async with session.patch(
                f"{self.base_url}/api/v1/storage/update_progress",
                json=payload.model_dump(mode="json"),
            ) as resp:
                if resp.status >= 500:
                    body = await resp.text()
                    raise _TransientWebhookError(
                        f"WebhookManager update_progress [{resp.status}] "
                        f"key='{key}' progress={progress} status={status}: {body}"
                    )
                if resp.status != 200:
                    body = await resp.text()
                    raise Exception(
                        f"WebhookManager update_progress [{resp.status}] "
                        f"key='{key}' progress={progress} status={status}: {body}"
                    )
        await self._with_retries(request, f"update_progress key='{key}'")

    async def update_response_data(
        self,
        key: str,
        response_data: dict,
        attempts: int = _RETRY_ATTEMPTS,
    ):
        """Обновить `response_data` задачи для UI и операторской диагностики.

        Диагностическое поле: недоступность webhook_manager наружу не
        поднимаем, иначе сбой отчётности уронит выполненную задачу.
        """
        payload = ResponseDataUpdate(
            key=key,
            response_data=json.dumps(response_data, ensure_ascii=False),
        )
        async def request(session: aiohttp.ClientSession):
            async with session.patch(
                f"{self.base_url}/api/v1/storage/update_response_data",
                json=payload.model_dump(mode="json"),
            ) as resp:
                if resp.status >= 500:
                    body = await resp.text()
                    raise _TransientWebhookError(
                        f"WebhookManager update_response_data [{resp.status}] "
                        f"key='{key}': {body}"
                    )
                if resp.status != 200:
                    body = await resp.text()
                    logger.warning(
                        f"WebhookManager update_response_data [{resp.status}] "
                        f"key='{key}': {body}"
                    )
        try:
            await self._with_retries(
                request, f"update_response_data key='{key}'", attempts=attempts
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "WebhookManager update_response_data не удалось key='{}': {}",
                key,
                exc,
            )

    async def get_task(self, key: str) -> Task | None:
        """Прочитать текущее состояние задачи.

        Ретраев нет намеренно: метод используется для опроса отмены, который
        и так выполняется по TTL — повторы только удлинили бы проверку.
        """
        async def request(session: aiohttp.ClientSession):
            async with session.get(
                f"{self.base_url}/api/v1/storage/task",
                params={"key": key},
            ) as resp:
                if resp.status == 404:
                    return None
                body = await resp.text()
                if resp.status != 200:
                    raise Exception(
                        f"WebhookManager get_task [{resp.status}] "
                        f"key='{key}': {body}"
                    )
                payload = await resp.json()
                return self._parse_task(payload, key)
        return await self._with_session(request)

    @staticmethod
    def _parse_task(payload, key: str) -> Task:
        """Разобрать ответ get_task: сырая задача либо конверт `{"task": ...}`."""
        try:
            return Task.model_validate(payload)
        except ValidationError:
            if isinstance(payload, dict) and "task" in payload:
                return Task.model_validate(payload["task"])
            raise ValueError(
                f"WebhookManager get_task вернул неожиданный формат key='{key}'"
            )
