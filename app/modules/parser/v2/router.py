from fastapi import (
    APIRouter,
    UploadFile,
    Request,
    Form
)
from datetime import timezone,datetime
from typing import List,Dict
from settings import settings
from .utils import utils
from uuid import uuid4
from .schemas import (
    ParseRequest,
    ParseResponse,
    Task,
    TaskStatus,
    Progress,
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

router = APIRouter(prefix="/v2/parser")

uploader = Uploader()
taskManager = TaskManager()
parser = Parser()

@router.post(
    path="/parse",
    name="Парсинг",
    deprecated=True,
    summary="Парсинг и опциональный перевод загруженных файлов",
    description="""
# Загрузка документов
### Загружает выбранные документы в S3 MiniO, обрабатывает, при необходимости перевод и выдает share-линки на эти документы:
### Входные данные: 
 - **files** `files[]` - выбранные документы документы
 - **translated** `str` - True/False - необходимость перевода
 - **src_lang** `str` - исходный язык
 - **target_lang** `str` - целевой язык
 - **max_num_page** `str` - предельное кол-во исследуемых страниц
### Выходные данные (share-линки к обработанным документам в S3 MiniO):
 - ```python
    class ParseFileResult(BaseModel):
        original_file_share_link:str 
        parse_file_share_link:str
        translated_file_share_link:str

    class ParseResponse(BaseModel):
        results: list[ParseFileResult]
    ```     
""",
    tags=['Parser']
)
async def parse(
    request: Request,
    files: List[UploadFile],
    translated:str = Form(...),
    src_lang:str = Form(...),
    target_lang:str = Form(...),
    max_num_page: str = Form(...) 
) -> ParseResponse:
    
    USER_ID = request.headers.get("X-UserID","guest")
    SERVICE_NAME = settings.SERVICE_NAME

    if target_lang not in settings.ALLOWED_LANGS or src_lang not in settings.ALLOWED_LANGS:
        raise BadRequestError(detail="Невалидный целевой или текущий язык")

    parse_request = ParseRequest(
        translated=bool(True if str(translated).lower() == 'true' else False), 
        src_lang=src_lang,
        target_lang=target_lang,
        max_num_page=int(max_num_page),
    )
    
    if len(files) == 0:
        raise BadRequestError(detail="Загрузите файлы для обработки")
    
    for file in files:
        if not uploader.validate(file.content_type):
            raise ContentNotSupportedError(detail=f"Тип файла {file.filename} не поддерживается сервисом")

    file_share_links = uploader.upload_to_s3_cloud(
        files=files,
    )
    
    parse_results = []
    for file_share_link in file_share_links:
        
        filename = utils.extract_filename(file_share_link,ext=True)
        
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
                ),
                service=SERVICE_NAME,
                created_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
                updated_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
                response_data=f"Инициализировал таск {TASK_KEY}",    
            )                    
        )
                
        resp_update_response_data = await taskManager.update_response_data(
            key=TASK_KEY,
            response_data=f"Принял файл {filename} в обработку",
        )        
                
        parse_response = await parser.parse(
            file_share_link=file_share_link,
            TASK_KEY=TASK_KEY,
            parse_request=parse_request,
        )
        
        resp_update_progress = await taskManager.update_progress(
            key=TASK_KEY,
            progress=Progress(
                progress=1.0,
                status=TaskStatus.READY,
            )
        )
        
        resp_update_response_data = await taskManager.update_response_data(
            key=TASK_KEY,
            response_data=f"Завершил обработку файла {filename}"
        )
        
        parse_results.append({filename:parse_response})   
    
    return ParseResponse(
        results=parse_results
    )                        