from fastapi import APIRouter,UploadFile,File,Form
from typing import List
from settings import settings
from openai import OpenAI 
from fastapi.responses import FileResponse
from uuid import uuid4
from .schemas import (
    ConvertTextRequest,
    ExtractRequest,
    Task,
    TaskStatus,
    Progress
)
import base64
from fastapi.websockets import WebSocket,WebSocketDisconnect
from .service import (
    Extractor,
    Uploader,
    TaskManager,
)
from .exceptions import BadRequestError
import asyncio,mimetypes
from datetime import datetime
from pathlib import Path

router = APIRouter(prefix="/parser")

uploader = Uploader()
openAiClient = OpenAI(
    base_url=settings.OPENAI_URL,
    api_key=settings.OPENAI_KEY,
)
extractor = Extractor()
taskManager = TaskManager()

USER_ID = uuid4()

@router.post(
    path="/upload",
    summary="Эндпоинт для загрузки документов",
    description="""
# Загрузка документов
### Загружает выбранные документы на сервер:
 - **Исходный документ сохраняется в директорию в `/scratch/[filename_currentdate]/src`**
 - **Готовит дерево директорий для хранение извлеченых элементов документа(таблиц,картинок), перевода(опционально), и текстового контента в выбранном формате**
### Входные данные: 
 - **files** `files[]` - документы
 - **src_lang** `str` - исходный язык документов
 - **target_conv_format** `str` - выбранный формат конвертации
 - **extracted_elements** `str` - дополнительные элементы в документе (картинки,таблицы) - опционально
### Выходные данные:
 - **paths** `list[str]` - список путей на сервере к загруженным документам 
""",
    tags=['Uploader'],
    )
async def upload(
    files: List[UploadFile] = File(..., title="Исходные файлы", description="📄 Файлы для конвертации"),
    src_lang: str = Form(..., title="Исходный язык", description="🌍 Язык документов", example="ru"),
    target_conv_format: str = Form(..., title="Выбранный формат конвертации", description="🎯 Целевой формат", example="md"),
    extracted_elements: List[str] = Form(default=["tables", "pictures"], description="Элементы для извлечения",example=["tables", "pictures"]),   
)->list:
  
    paths = []  
    for file in files:
        if file is None:
            raise BadRequestError(detail="Файл не загружен")
    
        uploader.validate(
            file.content_type,
            src_lang,
            target_conv_format,
        )
    
        scratch_src_file_path = await uploader.save_src_file_and_prepare_scratch_dir(
            file=file,
            elements=extracted_elements,
        )
        
        paths.append(scratch_src_file_path)
    
    return paths

@router.websocket(
    path="/extract",
    name="Парсинг",
)
async def extract(ws: WebSocket):
    await ws.accept()
    
    body_response = {}
    
    while True:
        try:
            await ws.send_json({"type":"connection","body":"connection-ok"})
            
            extract_data = await ws.receive_json()
                
            extract_request = ExtractRequest(
                    src_lang=extract_data['src_lang'],
                    target_conv_format=extract_data['target_conv_format'],
                    extracted_elements=extract_data['extracted_elements'],
                    translated=bool(extract_data['translated'] if str(extract_data['translated']).lower() in ['true','false'] else False), 
                    target_lang=extract_data['target_lang'],   
                    max_num_page=extract_data['max_num_page'],
                )
                
            paths = extract_data['paths']
                
            prefer_description = bool(extract_data['prefer_description'] if str(extract_data['prefer_description']).lower() in ['true','false'] else False)
                
            for file_path in paths:
                    # #---------------Взаимодействие с веб-хук менеджером-------------------
                    
                    task_id = uuid4()
                    key = f"{USER_ID}:{settings.SERVICE_NAME}:{task_id}"
                    task = Task(
                        task_id=task_id,
                        user_id=USER_ID,
                        progress=Progress(
                            progress=0.0,
                            status="PENDING"
                        )
                    )
                    
                    storage_response = await taskManager.storage(
                        key=key,
                        task=task,
                    )
                    
                    await ws.send_json(
                        {
                            "type":"info",
                            "body":{
                                "filename":f"{task_id}",
                                "status":f"Инициализировал задачу: {storage_response['code']} : {storage_response['message']}"
                            }
                        }
                    )
                    await asyncio.sleep(0.5)
                    
                    #----------------------------------------------------------------------
                    mime_type,_ = mimetypes.guess_type(file_path)
                    
                    if (
                        mime_type in ['image/png','image/webp','image/jpeg'] and prefer_description 
                    ):
                        await ws.send_json(
                            {
                                "type":"info",
                                "body":{
                                    "filename":f"{file_path.split("/")[-1]}",
                                    "status":"Преобразую картинку...",
                                },
                            })
                        await asyncio.sleep(0.5)
                        
                        await uploader.resize_image(file_path)
                        
                        await ws.send_json(
                            {
                                "type":"info",
                                "body":{
                                    "filename":f"{file_path.split("/")[-1]}",
                                    "status":"Изучаю картинку...",
                                },
                            })
                        await asyncio.sleep(0.5)
                        
                        with open(file_path,'rb') as img:
                            content = img.read()
                            
                        image_url = f"data:image/jpeg;base64,{base64.b64encode(content).decode('utf-8')}"
                        
                        await ws.send_json(
                            {
                                "type":"info",
                                "body":{
                                    "filename":f"{file_path.split("/")[-1]}",
                                    "status":"Формирую запрос к LLM..."
                                }
                            }
                        )
                        await asyncio.sleep(0.5)
                        
                        openai_response = openAiClient.chat.completions.create(
                            model="LocalModel",
                            messages = [
                                {
                                    "role": "user",
                                    "content": [
                                        {"type": "text", "text": "Что изображено на картинке? Ответь одним предложением"},
                                        {
                                            "type": "image_url",
                                            "image_url": {
                                                "url": image_url
                                            }
                                        },
                                    ]      
                                },
                            ]
                        ).choices[0].message.content
                        
                        
                        update_progress_response = await taskManager.update_progress(
                            key=key,
                            progress=Progress(
                                progress=0.5,
                                status="PROCESSING",
                            ),
                        )
                        
                        await ws.send_json(
                            {
                                "type":"info",
                                "body":{
                                    "filename":f"{task_id}",
                                    "status":f"Обновил прогресс задачи на 0.5: {update_progress_response}"
                                }
                            }
                        )
                        await asyncio.sleep(0.5)
                            
                            
                        await ws.send_json(
                                {
                                    "type":"info",
                                    "body":{
                                        "filename":f"{file_path.split("/")[-1]}",
                                        "status":"Ответ от LLM..."
                                    }
                                }
                            )
                        await asyncio.sleep(0.5)
                            
                        extract_response = {
                                f"{file_path.split("/")[-1]}":{
                                    "text":"",
                                    "summary":openai_response,
                                    "images":"",
                                    "pictures":"",
                                }
                            }
                            
                        body_response.update(extract_response)
                            
                        update_progress_response = await taskManager.update_progress(
                                key=f"{USER_ID}:{settings.SERVICE_NAME}:{task_id}",
                                progress=Progress(
                                    progress=1.0,
                                    status="READY",
                                )
                            )
                            
                        await ws.send_json(
                                {
                                    "type":"info",
                                    "body":{
                                        "filename":f"{task_id}",
                                        "status":f"Завершил задачу: {update_progress_response}"
                                    }
                                }
                            )
                        await asyncio.sleep(0.5)
                            
                        await ws.send_json(
                                {
                                    "type":"info",
                                    "body":{
                                        "filename":"",
                                        "status":"Перехожу к следующему файлу..."
                                    }
                                }
                            )
                        await asyncio.sleep(0.5)
                            
                    else:                                    
                        with open(file_path,"rb") as f:
                            src_file = UploadFile(
                                file=f.read(),
                                filename=file_path.split('/')[-1]
                            )

                        scratch_dir_path = str(Path(file_path).parent.parent)
                        
                        extract_response = await extractor.extract(
                            ws=ws,
                            scratch_dir_path=scratch_dir_path,
                            src_file=src_file,
                            extract_request=extract_request
                        )
                    
                        update_progress_response = await taskManager.update_progress(
                            key=f"{USER_ID}:{settings.SERVICE_NAME}:{task_id}",
                            progress=Progress(
                                progress=0.5,
                                status="PROGRESS",
                            )
                        )
                            
                        await ws.send_json(
                                {
                                "type":"info",
                                "body":{
                                    "filename":f"{task_id}",
                                    "status":f"Обновил прогресс задачи на 0.5: {update_progress_response}"
                                }
                            }
                        )
                        await asyncio.sleep(0.5)

                        body_response.update(extract_response)

                        update_progress_response = await taskManager.update_progress(
                            key=f"{USER_ID}:{settings.SERVICE_NAME}:{task_id}",
                            progress=Progress(
                                progress=1.0,
                                status="READY",
                            )
                        )
                        await ws.send_json(
                            {
                                "type":"info",
                                "body":{
                                    "filename":f"{task_id}",
                                    "status":f"Завершил выполнение задачи: {update_progress_response}"
                                }
                            }
                        )
                        await asyncio.sleep(0.5)
                        
            await ws.send_json({
                "type":"all-files-ended",
                "body":body_response,
            })
            break
                
        except WebSocketDisconnect as e:
            await ws.send_json({"error":e})
            break
        except Exception as e:
            await ws.send_json({"error":e})
            await ws.close()
            break

@router.post(
    path="/voice",
    summary="Эндпоинт для конвертации текста в аудиофайл",
    description="""
# Озвучивание
## Запрос к сервису Sova-TTS на конвертацию текста в голос. Файл сохраняется `/mnt/king/sova.git/services/text_to_speech/audios/{audio_filename}` 
### Входные данные:
 - **text** `str` - извлеченный текст
 - **voice_name** `str` - голос для озвучки 
### Выходные данные:
 - **path_to_audiofile** `str` - путь к аудиофайлу
""",
    tags=['TTS']
    )
async def to_voice(request: ConvertTextRequest) -> FileResponse:
    text = request.text
    voice_name = request.voice_name
    
    full_path_to_audio = extractor.text_to_speech(
        message=text,
        voice_name=voice_name,
    )

    path_to_audio = str(str(full_path_to_audio).split("text_to_speech")[-1].lstrip("/"))

    return path_to_audio