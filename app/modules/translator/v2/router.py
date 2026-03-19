from uuid import uuid4

import aiohttp
from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request
from loguru import logger

from modules.parser.v1.schemas import FileFormats, ParserParams
from modules.resource_manager.service import ResourceManagerService
from modules.translator.v1.schemas import TranslatorRequest
from modules.translator.v2.schemas import TranslatorResponseData, TranslatorV2Response
from modules.translator.v2.service import TranslatorV2Service
from modules.watchtower.service import WatchtowerService
from modules.webhook_manager.service import WebhookManagerService
from modules.parser.v1.utils import save_file
from settings import settings

router = APIRouter(prefix="/api/v2/parser")


@router.post(
    path="/translator/file/word",
    name="[V2] Translate file to WORD (async)",
    summary="Async translation: returns task_id immediately",
    description=f"""
## Асинхронный перевод файла в WORD (V2)

Принимает файл, немедленно создаёт задачу в **webhook_manager** и возвращает `task_id`.
Перевод, загрузка оригинала и результата в облако происходят в фоне.

### Заголовки
- `X-User-ID` **(обязательный)** — идентификатор пользователя

### Поддерживаемые форматы:
* **DOC**: `{FileFormats.DOC.value}`
* **PDF**: `{FileFormats.PDF.value}`
* **XLSX**: `{FileFormats.XLSX.value}`
* **IMAGES**: `{FileFormats.IMAGE.value}`
* **HTML**: `{FileFormats.HTML.value}`
* **PPTX**: `{FileFormats.PPTX.value}`
* **TXT**: `{FileFormats.TXT.value}`

### Прогресс задачи
Отслеживается через **webhook_manager** по ключу `user_id:document-parser:task_id`.

`response_data` содержит JSON:
```json
{{
  "original_language": "en",
  "target_language": "ru",
  "original_file": "<sharelink или пусто>",
  "translated_file": "<sharelink или пусто>",
  "text_status": "Перевожу... 45/120 элементов"
}}
```
    """,
    tags=["Translator V2"],
    response_model=TranslatorV2Response,
)
async def translate_file_to_word_v2(
    request: Request,
    background_tasks: BackgroundTasks,
    x_user_id: str = Header(..., alias="X-User-ID", description="ID пользователя"),
    translator_data: TranslatorRequest = Depends(),
) -> TranslatorV2Response:
    task_id = str(uuid4())

    # Instantiate services
    webhook = WebhookManagerService(settings.WEBHOOK_MANAGER_URL)
    watchtower = WatchtowerService(settings.WATCHTOWER_URL)
    resource_manager = ResourceManagerService(settings.RESOURCE_MANAGER_URL)

    # Build initial response_data and create task in webhook_manager BEFORE returning
    initial_response_data = TranslatorResponseData(
        original_language=translator_data.source_language,
        target_language=translator_data.target_language,
        text_status="Задача принята",
    )
    try:
        task_key = await webhook.create_task(
            user_id=x_user_id,
            task_id=task_id,
            response_data=initial_response_data.model_dump(),
        )
    except aiohttp.ClientConnectorError as e:
        logger.error(f"Webhook Manager unavailable: {e}")
        raise HTTPException(
            status_code=503,
            detail="Сервис менеджера задач недоступен. Попробуйте позже.",
        )

    # Save uploaded file; path is passed to background task
    file_path = await save_file(translator_data.file)
    original_filename = translator_data.file.filename

    parser_params = ParserParams(
        file_path=file_path,
        parse_images=translator_data.parse_images,
        include_image_in_output=translator_data.include_image_in_output,
        full_vlm_pdf_parse=translator_data.full_vlm_pdf_parse,
    )

    service = TranslatorV2Service(
        webhook=webhook,
        watchtower=watchtower,
        resource_manager=resource_manager,
    )

    background_tasks.add_task(
        service.run_translation_task,
        user_id=x_user_id,
        task_id=task_id,
        task_key=task_key,
        file_path=file_path,
        original_filename=original_filename,
        source_language=translator_data.source_language,
        target_language=translator_data.target_language,
        parser_params=parser_params,
        executor=request.app.state.executor,
    )

    logger.info(f"Translation task {task_id} queued for user {x_user_id}")
    return TranslatorV2Response(task_id=task_id, key=task_key)
