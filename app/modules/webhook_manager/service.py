import json
from datetime import datetime, timezone

import aiohttp
from loguru import logger

from modules.webhook_manager.schemas import (
    Task, TaskProgress, TaskStatus,
    TaskCreationV2, ProgressUpdate, ResponseDataUpdate,
)
from settings import settings


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
                if resp.status not in (200, 201):
                    raise Exception(
                        f"WebhookManager create_task вернул [{resp.status}] "
                        f"для key='{key}': {body}"
                    )
                logger.info("WebhookManager: задача создана key='{}'", key)
        await self._with_session(request)
        return key

    async def update_progress(self, key: str, progress: float, status: TaskStatus):
        """Обновить progress/status асинхронной задачи."""
        payload = ProgressUpdate(
            key=key,
            progress=TaskProgress(progress=progress, status=status),
        )
        async def request(session: aiohttp.ClientSession):
            async with session.patch(
                f"{self.base_url}/api/v1/storage/update_progress",
                json=payload.model_dump(mode="json"),
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    raise Exception(
                        f"WebhookManager update_progress [{resp.status}] "
                        f"key='{key}' progress={progress} status={status}: {body}"
                    )
        await self._with_session(request)

    async def update_response_data(self, key: str, response_data: dict):
        """Обновить `response_data` задачи для UI и операторской диагностики."""
        payload = ResponseDataUpdate(
            key=key,
            response_data=json.dumps(response_data, ensure_ascii=False),
        )
        async def request(session: aiohttp.ClientSession):
            async with session.patch(
                f"{self.base_url}/api/v1/storage/update_response_data",
                json=payload.model_dump(mode="json"),
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logger.warning(
                        f"WebhookManager update_response_data [{resp.status}] "
                        f"key='{key}': {body}"
                    )
        await self._with_session(request)
