from uuid import uuid4

import aiohttp
from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request
from loguru import logger

from modules.parser.v1.schemas import FileFormats, ParserParams
from modules.parser.v1.utils import save_file
from modules.resource_manager.service import ResourceManagerService
from modules.translator.v1.schemas import TranslatorRequest
from modules.translator.v2.schemas import TranslatorResponseData, TranslatorV2Response
from modules.translator.v2.service import TranslatorV2Service
from modules.watchtower.service import WatchtowerService
from modules.webhook_manager.service import WebhookManagerService
from settings import settings

router = APIRouter(prefix="/api/v2/parser")


@router.post(
    path="/translator/file/word",
    name="[V2] Асинхронный перевод документа в Word",
    summary="Создать async-задачу перевода и сразу вернуть `task_id`",
    description=f"""
## Назначение

Принимает файл, создаёт задачу в `webhook_manager` и сразу возвращает
`task_id` и `key`. Парсинг, перевод и загрузка файлов выполняются в фоне.

### Заголовки
- `X-User-ID` **(обязательный)** — идентификатор пользователя.

### Поддерживаемые форматы
* **DOC**: `{FileFormats.DOC.value}`
* **PDF**: `{FileFormats.PDF.value}`
* **XLSX**: `{FileFormats.XLSX.value}`
* **IMAGES**: `{FileFormats.IMAGE.value}`
* **HTML**: `{FileFormats.HTML.value}`
* **PPTX**: `{FileFormats.PPTX.value}`
* **TXT**: `{FileFormats.TXT.value}`

### Прогресс задачи
Прогресс отслеживается через `webhook_manager` по ключу `user_id:service:task_id`.

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

### Этапы async-задачи
- создание задачи;
- получение bucket пользователя;
- upload оригинального файла;
- парсинг документа;
- перевод элементов;
- upload переведённого файла;
- публикация статуса `READY`.
""",
    tags=["Translator V2"],
    response_model=TranslatorV2Response,
)
async def translate_file_to_word_v2(
    request: Request,
    background_tasks: BackgroundTasks,
    x_user_id: str = Header(
        ...,
        alias="X-User-ID",
        description="Идентификатор пользователя, от имени которого создаётся задача.",
    ),
    translator_data: TranslatorRequest = Depends(),
) -> TranslatorV2Response:
    task_id = str(uuid4())

    webhook = WebhookManagerService(settings.WEBHOOK_MANAGER_URL)
    watchtower = WatchtowerService(settings.WATCHTOWER_URL)
    resource_manager = ResourceManagerService(settings.RESOURCE_MANAGER_URL)

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

    logger.info(
        "TranslatorV2: задача поставлена в очередь task_id='{}' user_id='{}'",
        task_id,
        x_user_id,
    )
    return TranslatorV2Response(task_id=task_id, key=task_key)
