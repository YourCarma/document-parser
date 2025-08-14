from fastapi import (
    APIRouter,
    UploadFile,
    Request,
    Form
)
import json
from typing import List
from settings import settings
from .utils import utils
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

router = APIRouter(prefix="/parser")

uploader = Uploader()
taskManager = TaskManager()
parser = Parser()

@router.post(
    path="/parse",
    name="Парсинг",
    summary="Парсинг и опциональный перевод загруженных файлов",
    description="""
# Загрузка документов
### Загружает выбранные документы в S3 MiniO:
 - [Дерево директорий]
### Входные данные: 
 - **files** `files[]` - выбранные документы документы
 - **translated** `str` - True/False - необходимость перевода
 - **src_lang** `str` - исходный язык
 - **target_lang** `str` - целевой язык
 - **max_num_page** `str` - предельное кол-во исследуемых страниц
### Выходные данные (share-линки к обработанным документам в S3 MiniO):
 - ```python
    [
        {
            "file_1":{
                "parse_file_share_link":"parse_file_share_link_1",
                "translated_file_share_link":"translate_file_share_link_1",
            }
        },
        {
            "file_2":{
                "parse_file_share_link":"parse_file_share_link_2",
                "translated_file_share_link":"translate_file_share_link_2",
            }
        },
    ]
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
    
    body_resp = []
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
                
        resp_update_response_data = await taskManager.update_response_data(
            key=TASK_KEY,
            response_data=json.dumps({
                "message":f"Принял файл {str(file_share_link).split("/")[-1]} в обработку",
            }),
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
        
        filename = utils.extract_filename(file_share_link,ext=True)
        
        resp_update_response_data = await taskManager.update_response_data(
            key=TASK_KEY,
            response_data=json.dumps({
                "message":f"Завершил обработку файла {filename}"
            })
        )
        
        body_resp.append({filename:parse_response})   
    
    return body_resp                        