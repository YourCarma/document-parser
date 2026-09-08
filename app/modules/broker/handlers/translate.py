"""Обработчик задачи `document-parser.translate` из очереди."""

from typing import ClassVar

from loguru import logger
from pydantic import BaseModel, ValidationError

from modules.broker.abc.abc import HandlerOutcome, TaskHandlerABC
from modules.broker.exceptions import (
    InvalidTaskPayload,
    TaskPipelineFailed,
    UnsupportedSourceFormat,
)
from modules.broker.schemas import TaskEnvelope, TaskType, TranslatePayload
from modules.parser.v1.schemas import ParserParams
from modules.parser.v1.utils import is_supported_extension
from modules.resource_manager.exceptions import BucketNotFound
from modules.translator.v2.service import TranslatorV2Service
from modules.translator.v2.sources import WatchtowerSource
from modules.webhook_manager.cancellation import WebhookCancellationToken
from modules.webhook_manager.schemas import TaskStatus
from modules.webhook_manager.service import WebhookManagerService
from settings import settings


class TranslateHandler(TaskHandlerABC):
    """Перевод документа, пришедший из очереди."""

    task_type: ClassVar[str] = TaskType.TRANSLATE.value
    payload_model: ClassVar[type[BaseModel]] = TranslatePayload

    async def handle(self, envelope: TaskEnvelope, task_key: str) -> HandlerOutcome:
        try:
            payload = TranslatePayload.model_validate(envelope.payload)
        except ValidationError as exc:
            raise InvalidTaskPayload(
                f"payload задачи {envelope.task_id} невалиден: {exc}"
            ) from exc

        webhook = self.runtime.webhook_manager()
        watchtower = self.runtime.watchtower()
        resource_manager = self.runtime.resource_manager()

        status = await self._preflight(webhook, task_key)
        if status is TaskStatus.READY:
            # Переигровка после consumer_timeout: работа уже сделана.
            return HandlerOutcome(TaskStatus.READY, "задача уже выполнена")
        if status is TaskStatus.CANCELLED:
            return HandlerOutcome(TaskStatus.CANCELLED, "отменена до старта")

        # Расширение проверяем ДО скачивания: незачем тянуть из хранилища
        # файл, для которого нет парсера.
        if not is_supported_extension(payload.file_path):
            raise UnsupportedSourceFormat(payload.file_path)

        # Бакет только по user_id: класть его в сообщение значит позволить
        # продюсеру записать что угодно в чужое хранилище.
        bucket = await resource_manager.get_user_bucket(envelope.user_id)
        if not bucket:
            raise BucketNotFound(envelope.user_id)

        cancellation = WebhookCancellationToken(webhook, task_key)
        source = WatchtowerSource(watchtower, payload.file_path)
        output_prefix = payload.output_prefix or settings.TRANSLATE_OUTPUT_PREFIX.format(
            task_id=envelope.task_id
        )
        parser_params = ParserParams(
            file_path="",
            parse_images=payload.parse_images,
            include_image_in_output=payload.include_image_in_output,
            full_vlm_pdf_parse=payload.full_vlm_pdf_parse,
        )

        # Экземпляр сервиса — на одно сообщение: в нём копится состояние
        # задачи (_last_progress, last_error). create_task не вызываем —
        # задачу уже создал гейтвей.
        service = TranslatorV2Service(
            webhook=webhook,
            watchtower=watchtower,
            resource_manager=resource_manager,
            translation_semaphore=self.runtime.translation_semaphore,
            parser_semaphore=self.runtime.parser_semaphore,
            http_session=self.runtime.http_session,
        )
        status = await service.run_translation_task(
            user_id=envelope.user_id,
            task_id=envelope.task_id,
            task_key=task_key,
            file_path="",
            original_filename="",
            source_language=payload.source_language,
            target_language=payload.target_language,
            parser_params=parser_params,
            executor=self.runtime.executor,
            cancellation=cancellation,
            source=source,
            bucket=bucket,
            output_prefix=output_prefix,
        )

        if status == TaskStatus.READY:
            return HandlerOutcome(TaskStatus.READY)
        if status == TaskStatus.CANCELLED:
            return HandlerOutcome(TaskStatus.CANCELLED)
        raise TaskPipelineFailed(service.last_error, service.last_stage)

    async def _preflight(
        self,
        webhook: WebhookManagerService,
        task_key: str,
    ) -> TaskStatus | None:
        """Прочитать текущий статус задачи. READY/CANCELLED -> работу не начинаем."""
        try:
            task = await webhook.get_task(task_key)
        except Exception as exc:
            logger.warning(
                "Broker: failed to read task status key='{}': {}",
                task_key,
                exc,
            )
            return None

        if task is None:
            logger.warning(
                "Broker: task not found in webhook_manager key='{}' — SERVICE_NAME "
                "spelling is likely out of sync with the gateway",
                task_key,
            )
            return None
        return task.progress.status
