from settings import settings
import requests
from typing import List
from uuid import uuid4
from io import BytesIO
from PIL import Image
from fastapi import UploadFile,File
from .schemas import (
    ParseRequest,
    Task,
    Progress,
    TaskStatus,
)
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
from .exceptions import (
    InternalServerError
)
from docling_core.types.doc import PictureItem, TableItem
from docling.pipeline.simple_pipeline import SimplePipeline
from docling.pipeline.standard_pdf_pipeline import StandardPdfPipeline
from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions
from docling_core.types.doc import ImageRefMode, TableItem, TextItem

class TaskManager(ABC):
    
    def __init__(self):
        self.SERVICE_NAME = settings.SERVICE_NAME
        self.TASK_MANAGER_ENDPOINT = settings.TASK_MANAGER_ENDPOINT
    
    async def storage_task(self,key:str,task:Task):
        resp = requests.post(
            url=self.TASK_MANAGER_ENDPOINT+"/storage/task",
            json={
                "key":key,
                "task":{
                    "created_at":datetime.now(timezone.utc).isoformat(),
                    "progress":{
                        "progress":task.progress.progress,
                        "status":task.progress.status,
                    },
                    "response_data":"",
                    "service":self.SERVICE_NAME,
                    "task_id":str(task.task_id),
                    "updated_at":datetime.now(timezone.utc).isoformat(),
                    "user_id":str(task.user_id),
                },
            }
        )
        return resp.json()
        
    async def update_progress(self,key:str,progress: Progress):
        resp = requests.patch(
            url=self.TASK_MANAGER_ENDPOINT+"/storage/update_progress",
            json={
                "key":key,
                "progress":{
                    "progress":progress.progress,
                    "status":progress.status,
                },
            }
        )
        return resp.json()
        

    async def update_response_data(self,key:str,response_data:str):
        resp = requests.patch(
            url=self.TASK_MANAGER_ENDPOINT+"/storage/update_response_data",
            json={
                "key":key,
                "response_data":response_data
            }
        )
        return resp.json()

class Uploader(ABC):
    def validate(self,
                mime_type: str = None,
        ) -> bool:
        if mime_type not in settings.ALLOWED_MIME_TYPES:
            return False
        return True
    
    # def create_directory_tree():
    #     pass
    
    def upload_to_s3_cloud(
        self,
        files: list[UploadFile] = File(...),
    ): #TODO - загрузка в s3 cloud в дереве /{user_folder}/document_parser/{filename}/{original} 
        files_form = []
        for file in files:
            file_content = file.file.read()
            files_form.append(
                ("files", (file.filename, file_content, file.content_type))
            )
            file.file.seek(0)
        
        resp = requests.put(
            url=settings.S3_CLOUD_ENDPOINT+f"/cloud/{settings.CLOUD_BUCKET_NAME}/file/upload",
            files=files_form,
        )  
        
        if resp.status_code != 200:
            raise InternalServerError(detail=f"Ошибка загрузки файлов в корзину {settings.CLOUD_BUCKET_NAME}: {resp.json()['message']}")
        
        file_links = []
        for file in files:
            resp = requests.post(
                url=settings.S3_CLOUD_ENDPOINT+f"/cloud/{settings.CLOUD_BUCKET_NAME}/file/share",
                json={
                    'dir_path': '/',
                    'expired_secs':3600,
                    'file_name': file.filename,
                    'only_relative_path':True,
                }
            )        
            
            if resp.status_code != 200:
                raise InternalServerError(detail=f"Ошибка получение share-линки файла {file.filename} в корзине {settings.CLOUD_BUCKET_NAME}")

            share_link = settings.MINIO_ENDPOINT + str(resp.json()['message']).split("?")[0]
            
            file_links.append(share_link)
            
        return file_links
        
class Parser(
    Uploader,
    TaskManager
):
    def __init__(self):
        self.LANGS = settings.ALLOWED_LANGS
        self.TASK_MANAGER_ENDPOINT = settings.TASK_MANAGER_ENDPOINT
    
    async def parse(self,
                    file_share_link:str,
                    TASK_KEY:str,
                    parse_request:ParseRequest = None,
        ):
        """
            Cyrillic is only compatible with English, try lang_list=["ru","rs_cyrillic","be","bg","uk","mn","en"]
            Arabic is only compatible with English, try lang_list=["ar","fa","ur","ug","en"]
        """                
    
        ocr_options = EasyOcrOptions(
            # lang=self.LANGS,
            lang=["ar","fa","ur","ug","en"],
            force_full_page_ocr=True,
            model_storage_directory=settings.ML_DIR,
            download_enabled=False,   
        )
        
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
    
        conv_result = doc_converter.convert(
            source=file_share_link,
            # page_range=(1,parse_request.max_num_page),
            raises_on_error=True,
        )   
        
        resp_update_progress = await self.update_progress(
            key=TASK_KEY,
            progress=Progress(
                progress=0.33,
                status=TaskStatus.PROCESSING,
            )
        )

        parse_file_filename = f"parse_{file_share_link.split('/')[-1].split('.')[0]}.md"
        parse_file_bytes=conv_result.document.export_to_markdown(
            image_mode=ImageRefMode.EMBEDDED,
        ).encode('utf-8')
        
        parse_file_share_link = self.upload_to_s3_cloud(
            files=[UploadFile(
                file=BytesIO(parse_file_bytes),
                filename=parse_file_filename,
                )
            ]
        )

        resp_update_progress = await self.update_progress(
            key=TASK_KEY,
            progress=Progress(
                progress=0.66,
                status=TaskStatus.PROCESSING,
            )
        )
        
        #П if parse_request.translated:
        #     copy_document = conv_result.document.model_copy(deep=True)
        #     for element, _ in copy_document.iterate_items():
        #Е         if isinstance(element, TextItem):
        #             element.orig = element.text
        #             element.text = self.translate_text(
        #Р                 text=element.text,
        #                 src_lang=parse_request.src_lang,
        #                 target_lang=parse_request.target_lang,
        #Е             )

        #         elif isinstance(element, TableItem): 
        #В             for cell in element.data.table_cells:
        #                 cell.text = self.translate_text(
        #О                     text=cell.text,
        #                     src_lang=parse_request.src_lang,
        #Д                    target_lang=parse_request.target_lang,
        #                 )
        
        resp_update_progress = await self.update_progress(
            key=TASK_KEY,
            progress=Progress(
                progress=0.99,
                status=TaskStatus.PROCESSING,
            )
        )
        
        #translated_document = copy_document.export_to_markdown() #TODO -> компановка переведенных элементов и текста в единый документ и загрузка в s3 cloud
        # self.upload_to_s3_cloud()
        
        resp_update_progress = await self.update_progress(
            key=TASK_KEY,
            progress=Progress(
                progress=1.0,
                status=TaskStatus.READY,
            )
        )
        
        return {
            "parse_file_share_link":parse_file_share_link[0],
            "translated_file_share_link":""
        }
    