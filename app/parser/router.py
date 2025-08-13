from fastapi import (
    APIRouter,
    UploadFile,
    File,
    Request,
    Form
)
from typing import List
from settings import settings
from uuid import uuid4
from .schemas import (
    ParseRequest,
    Task,
    TaskStatus,
    Progress
)
from .service import (
    Parser,
    Uploader,
    TaskManager,
)
from .exceptions import (
    BadRequestError,
    ContentNotSupportedError
)

from datetime import datetime
from pathlib import Path

router = APIRouter(prefix="/parser")

uploader = Uploader()
taskManager = TaskManager()
parser = Parser()

@router.post(
    path="/parse",
    name="Парсинг",
)
async def parse(
    request: Request,
    files: List[UploadFile],
    translated:str = Form(...),
    src_lang:str = Form(...),
    target_lang:str = Form(...),
    max_num_page: str = Form(...) 
):
    USER_ID = request.headers.get("X-UserID","guest")
    SERVICE_NAME = settings.SERVICE_NAME

    parse_request = ParseRequest(
        translated=bool(translated if str(translated).lower() in ['true','false'] else False), 
        src_lang=src_lang,
        target_lang=target_lang,
        max_num_page=int(max_num_page),
    )
    
    if len(files) == 0:
        raise BadRequestError("Загрузите файлы для обработки")
    
    for file in files:
        if not uploader.validate(file.content_type):
            raise ContentNotSupportedError(detail=f"Тип контента:{file.content_type} не поддерживается сервисом")
    
    file_share_links = uploader.upload_to_s3_cloud(files=files)
    
    body_resp = {}
    for file_share_link in file_share_links:
    
        TASK_ID = str(uuid4())
        
        TASK_KEY = f"{USER_ID}:{SERVICE_NAME}:{TASK_ID}"
        
        storage_task_response = await taskManager.storage_task(
            key=TASK_KEY,
            task=Task(
            task_id=TASK_ID,
            user_id=USER_ID,
            progress=Progress(
                progress=0.1,
                status=TaskStatus.PROCESSING,
                )    
            )                    
        )
                
        extract_response = await parser.parse(
            file_share_link=file_share_link,
            TASK_KEY=TASK_KEY,
            parse_request=parse_request,
        )
        
        body_resp.update({'file':extract_response})   
    
    return body_resp                        