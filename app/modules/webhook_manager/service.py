import json
from datetime import datetime, timezone

import aiohttp
from loguru import logger

from modules.webhook_manager.schemas import (
    Task, TaskCreation, TaskProgress, TaskStatus,
    ProgressUpdate, ResponseDataUpdate,
)
from settings import settings


class WebhookManagerService:
    def __init__(self, base_url: str):
        self.base_url = base_url

    def _make_key(self, user_id: str, task_id: str) -> str:
        return f"{user_id}:{settings.SERVICE_NAME}:{task_id}"

    async def create_task(self, user_id: str, task_id: str, response_data: dict) -> str:
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
        payload = TaskCreation(key=key, task=task)
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self.base_url}/storage/task",
                json=payload.model_dump(mode="json"),
            ) as resp:
                body = await resp.text()
                if resp.status not in (200, 201):
                    raise Exception(
                        f"WebhookManager create_task вернул [{resp.status}] "
                        f"для key='{key}': {body}"
                    )
                logger.info(f"Task created: {key}")
        return key

    async def update_progress(self, key: str, progress: float, status: TaskStatus):
        payload = ProgressUpdate(
            key=key,
            progress=TaskProgress(progress=progress, status=status),
        )
        async with aiohttp.ClientSession() as session:
            async with session.patch(
                f"{self.base_url}/storage/update_progress",
                json=payload.model_dump(mode="json"),
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    raise Exception(
                        f"WebhookManager update_progress [{resp.status}] "
                        f"key='{key}' progress={progress} status={status}: {body}"
                    )

    async def update_response_data(self, key: str, response_data: dict):
        payload = ResponseDataUpdate(
            key=key,
            response_data=json.dumps(response_data, ensure_ascii=False),
        )
        async with aiohttp.ClientSession() as session:
            async with session.patch(
                f"{self.base_url}/storage/update_response_data",
                json=payload.model_dump(mode="json"),
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logger.warning(
                        f"WebhookManager update_response_data [{resp.status}] "
                        f"key='{key}': {body}"
                    )
