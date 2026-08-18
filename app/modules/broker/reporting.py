"""Публикация статусов задачи из транспорта. Всё best-effort.

Сбой отчётности не имеет права превратить штатный reject в необработанное
исключение, поэтому наружу отсюда ничего не летит.
"""

import asyncio
import json
from typing import Any

from loguru import logger

from modules.messages import MSG_RETRY_SCHEDULED
from modules.webhook_manager.schemas import TaskStatus
from modules.webhook_manager.service import WebhookManagerService


async def _merged_response_data(
    webhook: WebhookManagerService,
    task_key: str,
    updates: dict[str, Any],
) -> dict[str, Any]:
    """Слить `updates` с текущим response_data, чтобы не затереть чужие поля."""
    current: dict[str, Any] = {}
    try:
        task = await webhook.get_task(task_key)
        if task is not None and task.response_data:
            parsed = json.loads(task.response_data)
            if isinstance(parsed, dict):
                current = parsed
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning(
            "Broker: не удалось прочитать response_data key='{}': {}", task_key, exc
        )
    return {**current, **updates}


async def report_task_error(
    webhook: WebhookManagerService,
    task_key: str,
    message: str,
    progress: float = 0.0,
) -> None:
    """Опубликовать ERROR с человекочитаемым текстом. Best-effort."""
    try:
        updates = {"text_status": message, "error": message}
        merged = await _merged_response_data(webhook, task_key, updates)
        await webhook.update_progress(task_key, progress, TaskStatus.ERROR)
        await webhook.update_response_data(task_key, merged)
        logger.info(
            "Broker: опубликована ошибка задачи key='{}' message='{}'",
            task_key,
            message,
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning(
            "Broker: не удалось опубликовать ошибку задачи key='{}': {}",
            task_key,
            exc,
        )


async def report_task_retry(
    webhook: WebhookManagerService,
    task_key: str,
    attempt: int,
    total: int,
) -> None:
    """Вернуть задачу в PROCESSING перед повторной попыткой. Best-effort."""
    try:
        text = MSG_RETRY_SCHEDULED.format(attempt=attempt, total=total)
        merged = await _merged_response_data(webhook, task_key, {"text_status": text})
        await webhook.update_progress(task_key, 0, TaskStatus.PROCESSING)
        await webhook.update_response_data(task_key, merged)
        logger.info(
            "Broker: задача возвращена в PROCESSING перед повтором key='{}' {}/{}",
            task_key,
            attempt,
            total,
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning(
            "Broker: не удалось опубликовать повтор задачи key='{}': {}",
            task_key,
            exc,
        )
