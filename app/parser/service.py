from settings import settings
import asyncio,requests
from PIL import Image
from fastapi import UploadFile
from .schemas import (
    ExtractRequest,
    Task,
    Progress,
    TaskStatus,
)
from fastapi.websockets import WebSocket,WebSocketDisconnect
from typing import List
from .exceptions import (
    ContentNotSupportedError,
    InternalServerError
    )
from pathlib import Path
import json,yaml
from datetime import datetime,timezone
from abc import ABC
from docling.backend.pypdfium2_backend import PyPdfiumDocumentBackend
from docling.datamodel.base_models import InputFormat
from docling.document_converter import (
    DocumentConverter,
    PdfFormatOption,
    WordFormatOption,
)
from docling.datamodel.pipeline_options import (
    PdfPipelineOptions,
    EasyOcrOptions,
    )
from docling_core.types.doc import PictureItem, TableItem
from docling.pipeline.simple_pipeline import SimplePipeline
from docling.pipeline.standard_pdf_pipeline import StandardPdfPipeline
from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions
from docling_core.types.doc import ImageRefMode, TableItem, TextItem

class TaskManager(ABC):
    
    def __init__(self):
        self.url = settings.TASK_MANAGER_URL
    
    async def storage(self,key:str,task:Task):
        resp = requests.post(
            url=self.url,
            json={
                "key":key,
                "task":{
                    "created_at":datetime.now(timezone.utc).isoformat(),
                    "updated_at":datetime.now(timezone.utc).isoformat(),
                    "progress":{
                        "progress":task.progress.progress,
                        "status":task.progress.status,
                    },
                    "task_id":str(task.task_id),
                    "user_id":str(task.user_id),
                    "response_data":""
                },
            }
        )
        return resp.json()
        
    async def update_progress(self,key:str,progress: Progress):
        resp = requests.patch(
            url=self.url,
            json={
                "key":key,
                "progress":{
                    "progress":progress.progress,
                    "status":progress.status,
                }
            }
        )
        return resp.content.decode()
        

    async def update_response_data(self,key:str,response_data:str):
        resp = requests.patch(
            url=self.url,
            json={
                "key":key,
                "response_data":response_data
            }
        )
        return resp.content.decode()

class Uploader(ABC):
    
    def validate(self,
                mime_type: str = None,
                lang: str = None,
                target_conv_format: str = None,
                ):
        if mime_type not in settings.ALLOWED_MIME_TYPES:
            raise ContentNotSupportedError(content_type=f"mime-type:{mime_type}")
    
        if target_conv_format not in settings.ALLOWED_CONVERTED_TYPES:
            raise ContentNotSupportedError(content_type=f"converted-type:{target_conv_format}")
    
        if lang not in settings.ALLOWED_LANGS:
            raise ContentNotSupportedError(content_type=f"lang:{lang}")
    
    async def save_src_file_and_prepare_scratch_dir(self,
                                              file: UploadFile,
                                              elements: List[str],
                                              ) -> str:
        
        scratch_dir_path = Path(settings.SCRATCH_DIR) / f"{file.filename}_{datetime.now().strftime("%d.%m.%y-%H.%M")}"
        scratch_dir_path.mkdir(parents=True,exist_ok=True)
        
        scratch_src_file_path = Path(scratch_dir_path) / "src"
        scratch_src_file_path.mkdir(parents=True,exist_ok=True)
        
        file_path = scratch_src_file_path / file.filename
        with open(file_path,"wb") as buff:
            buff.write(await file.read())
        
        if "tables" in elements:
            scratch_file_tables_path = Path(scratch_dir_path) / "tables"
            scratch_file_tables_path.mkdir(parents=True,exist_ok=True) 
        
        if "pictures" in elements:
            scratch_file_pictures_path = Path(scratch_dir_path) / "pictures"
            scratch_file_pictures_path.mkdir(parents=True,exist_ok=True)
        
        return str(scratch_src_file_path / file.filename)
    
    async def resize_image(self,image_path:str,size=1024):
        with Image.open(image_path) as img:
            width, height = img.size
            left = (width - size) // 2
            top = (height - size) // 2
            right = left + size
            bottom = top + size
            
            img_cropped = img.crop((left, top, right, bottom))
            img_cropped.save(image_path)

            img.save(image_path)
    
class Extractor(
    Uploader,
    TaskManager
):
    
    def text_to_speech(self,message,voice_name) -> str:
        audio_filename = requests.post(
            url=settings.TTS_URL,
            json={
                "message":message,
                "voice_name":voice_name,
            }
        ).json()['audio_name']
        
        return settings.settings.AUDIOS_PATH+audio_filename
         
    def translate_text(self,text:str,target_lang:str,src_lang:str):
        try:    
            resp = requests.post(
                url=settings.TRANSLATOR_URL,
                json={
                    'text':text,
                    'target_language': target_lang,
                    'source_language':src_lang,
                }
            )
        except ValueError as e:
            return str(e)
        
        translated_text = resp.json()['text']
        return translated_text

    async def extract(self,
                      ws: WebSocket,
                      scratch_dir_path:str,
                      src_file:UploadFile,
                      extract_request:ExtractRequest,
        ):
        """
            Cyrillic is only compatible with English, try lang_list=["ru","rs_cyrillic","be","bg","uk","mn","en"]
            Arabic is only compatible with English, try lang_list=["ar","fa","ur","ug","en"]
        """        
        await ws.send_json({
            "type":"info",
            "body":{
                "filename":f"{src_file.filename}",
                "status":"Начал работу с файлом"
                },
            })
        await asyncio.sleep(0.5)
        
        tables_path = []
        pictures_path = []
        
        langs: List[str] = [extract_request.src_lang,'en']       
        ocr_options = EasyOcrOptions(
            lang=langs,
            force_full_page_ocr=True,
            model_storage_directory=settings.ML_DIR,
            download_enabled=False,   
        )
        
        await ws.send_json({
            "type":"info",
            "body":{
                "filename":f"{src_file.filename}",
                "status":"Подготовил EasyOCR для распознавания",
                },
            })
        await asyncio.sleep(0.5)
        
        pipeline_options = PdfPipelineOptions()
        pipeline_options.accelerator_options = AcceleratorOptions(num_threads=8,device=AcceleratorDevice.CPU)
        """
            Для нейросетей/графики → GPU (cuda:0).
            Для небольших данных → CPU (меньше накладных расходов).
            Для серверов с несколькими GPU → Явно указывайте cuda:index.
            Для отладки → Принудительно CPU.
        """
        pipeline_options.images_scale = 2.0
        pipeline_options.generate_page_images = True
        pipeline_options.generate_table_images = True
        pipeline_options.do_ocr = True
        pipeline_options.generate_picture_images = True
        pipeline_options.ocr_options = ocr_options

        await ws.send_json({
            "type":"info",
            "body":{
                "filename":f"{src_file.filename}",
                "status":"Задал пайплайны конвертации",
                },
            })
        await asyncio.sleep(0.5)

        format_options = {
            InputFormat.DOCX: WordFormatOption(pipeline_cls=SimplePipeline),
            InputFormat.PDF: PdfFormatOption(pipeline_cls=StandardPdfPipeline,backend=PyPdfiumDocumentBackend ,pipeline_options=pipeline_options),
            InputFormat.IMAGE:PdfFormatOption(pipeline_cls=StandardPdfPipeline,backend=PyPdfiumDocumentBackend,pipeline_options=pipeline_options),
        }
        
        doc_converter = DocumentConverter(
                allowed_formats=[
                    InputFormat.PDF,
                    InputFormat.IMAGE,
                    InputFormat.DOCX,
                    InputFormat.PPTX, 
                ],
                format_options=format_options,
            )

        await ws.send_json({
            "type":"info",
            "body":{
                "filename":f"{src_file.filename}",
                "status":"Инициализировал конвертер",
                },
            })
        await asyncio.sleep(0.5)
        
        conv_result = doc_converter.convert(
            source=Path(scratch_dir_path) / "src" / src_file.filename,
            page_range=(1,extract_request.max_num_page),
            raises_on_error=True,
        )   

        extracted_text = conv_result.document.export_to_markdown()

        await ws.send_json({
            "type":"info",
            "body":{
                "filename":f"{src_file.filename}",
                "status":"Сконвертировал файл"
                },
            })
        await asyncio.sleep(0.5)

        summary_text = ""
        try:
            text = conv_result.document.export_to_text()
            summary_text = requests.post(
                url=settings.SUMMARIZER_URL,
                params={
                    "text":text,
                    "summary_param":"word",
                    "count":20,
                },
            ).content.decode()
        except requests.exceptions.ConnectionError as conn_err:
            summary_text = "Краткая выжимка текста"
        
        table_counter = 0
        if "tables" in extract_request.extracted_elements:
            path_to_tables = Path(scratch_dir_path) / "tables"
            path_to_tables.mkdir(exist_ok=True,parents=True)
            for element, _ in conv_result.document.iterate_items():
                if isinstance(element, TableItem):
                    table_counter += 1
                    element_image_filename = path_to_tables / f"table-{table_counter}.png"
                    tables_path.append("/"+str(element_image_filename).split("sova-parser")[-1].lstrip("/"))
                    with element_image_filename.open("wb") as fp:
                        element.get_image(conv_result.document).save(fp, "PNG")
        
        await ws.send_json({
            "type":"info",
            "body":{
                "filename":f"{src_file.filename}",
                "status":f"Обнаружил {table_counter} таблиц"
            },
        })
        await asyncio.sleep(0.5)
            
        await ws.send_json({
            "type":"info",
            "body":{
                "filename":f"{src_file.filename}",
                "status":"Начал поиск картинок"
            },
        })
        await asyncio.sleep(0.5)
        
        picture_counter = 0
        if "pictures" in extract_request.extracted_elements:
            path_to_pictures = Path(scratch_dir_path) / "pictures"
            path_to_pictures.mkdir(exist_ok=True,parents=True)
            for element, _ in conv_result.document.iterate_items():    
                if isinstance(element,PictureItem):
                    picture_counter += 1
                    element_image_filename = path_to_pictures / f"picture-{picture_counter}.png"
                    pictures_path.append("/"+str(element_image_filename).split("sova-parser")[-1].lstrip("/"))                     
                    with element_image_filename.open("wb") as fp:
                        element.get_image(conv_result.document).save(fp, "PNG")
        
        await ws.send_json({
            "type":"info",
            "body":{
                "filename":f"{src_file.filename}",
                "status":f"Обнаружил {picture_counter} картинок"
            },
        })
        await asyncio.sleep(0.5)
            
        
        if extract_request.translated:
            await ws.send_json({
                "type":"info",
                "body":{
                    "filename":f"{src_file.filename}",
                    "status":"Начал перевод текста"
                }
            })
            
            path_to_translate = Path(scratch_dir_path) / "translate" / extract_request.target_lang
            path_to_translate.mkdir(exist_ok=True,parents=True)
            
            copy_document = conv_result.document.model_copy(deep=True)
            for element, _ in copy_document.iterate_items():
                if isinstance(element, TextItem):
                    element.orig = element.text
                    element.text = self.translate_text(text=element.text,target_lang=extract_request.target_lang,src_lang=extract_request.src_lang)

                elif isinstance(element, TableItem): 
                    for cell in element.data.table_cells:
                        cell.text = self.translate_text(text=cell.text,target_lang=extract_request.target_lang,src_lang=extract_request.src_lang)

            md_file = path_to_translate / f"{copy_document.name}-translated.md"
            with open(md_file,"w",encoding="utf-8") as fp:
                fp.write(copy_document.export_to_markdown())
            
            extracted_text = copy_document.export_to_text()
             
        await ws.send_json({
            "type":"info",
            "body":{
                "filename":f"{src_file.filename}",
                "status":f"Конвертирую в {extract_request.target_conv_format} выбранный файл"  
            },
        })
        await asyncio.sleep(0.5)
            
        scratch_file_path_with_lang = Path(scratch_dir_path) / extract_request.src_lang

        match extract_request.target_conv_format:
            case "md":
                scratch_file_path_with_lang_and_ext = scratch_file_path_with_lang / "md"
                scratch_file_path_with_lang_and_ext.mkdir(parents=True,exist_ok=True)
                scratch_file_path = scratch_file_path_with_lang_and_ext / f"{conv_result.input.file.stem}.md"
                # conv_result.document.save_as_markdown(scratch_file_path,image_mode=ImageRefMode.EMBEDDED)
                with open(scratch_file_path,"w",encoding="utf-8") as fp:
                    fp.write(conv_result.document.export_to_markdown())
                # extracted_text = conv_result.document.export_to_markdown()
                 
                await ws.send_json({
                    "type":"info",
                    "body":{
                        "filename":f"{src_file.filename}",
                        "status":"Сконвертировал файл в md"
                }})
                
                await asyncio.sleep(0.5)
                    
            case "json":
                scratch_file_path_with_lang_and_ext = scratch_file_path_with_lang / "json"
                scratch_file_path_with_lang_and_ext.mkdir(parents=True,exist_ok=True)
                scratch_file_path = scratch_file_path_with_lang_and_ext / f"{conv_result.input.file.stem}.json"
                # conv_result.document.save_as_json(scratch_dir_path,image_mode=ImageRefMode.EMBEDDED)
                with open(scratch_file_path,"w",encoding="utf-8") as fp:
                    fp.write(json.dumps(conv_result.document.export_to_dict()))
                
                await ws.send_json({
                    "type":"info",
                    "body":{
                        "filename":f"{src_file.filename}",
                        "status":"Сконвертировал файл в json"
                }})
                await asyncio.sleep(0.5)
                
            case "yaml":
                scratch_file_path_with_lang_and_ext = scratch_file_path_with_lang / "yaml"
                scratch_file_path_with_lang_and_ext.mkdir(parents=True,exist_ok=True)
                scratch_file_path = scratch_file_path_with_lang_and_ext /f"{conv_result.input.file.stem}.yaml"
                # conv_result.document.save_as_yaml(scratch_dir_path,image_mode=ImageRefMode.REFERENCED)
                with open(scratch_file_path,"w",encoding="utf-8") as fp:
                    fp.write(yaml.safe_dump(conv_result.document.export_to_dict()))
                    
                await ws.send_json({
                    "type":"info",
                    "body":{
                        "filename":f"{src_file.filename}",
                        "status":"Сконвертировал файл в yaml"
                }})
                await asyncio.sleep(0.5)
                
            case "txt":
                scratch_file_path_with_lang_and_ext = scratch_file_path_with_lang / "txt"
                scratch_file_path_with_lang_and_ext.mkdir(parents=True,exist_ok=True)
                scratch_file_path = scratch_file_path_with_lang_and_ext / f"{conv_result.input.file.stem}.txt"
                with open(scratch_file_path,"w",encoding="utf-8") as fp:
                    fp.write(conv_result.document.export_to_text())
                    
                await ws.send_json({
                    "type":"info",
                    "body":{
                        "filename":f"{src_file.filename}",
                        "status":"Сконвертировал файл в txt"
                }})
                await asyncio.sleep(0.5)        
                
        await ws.send_json({"type":"file-ended","body":f"{src_file.filename}"})
        await asyncio.sleep(0.5)
                
        return {
            f"{src_file.filename}":{
                "text":extracted_text,
                "summary":summary_text,
                "images":pictures_path,
                "tables":tables_path,
            },
        }