from settings import settings
from .utils import utils
import requests
from fastapi import (
    UploadFile,
    File
)
import json
from .schemas import (
    ParseRequest,
    ParseResponse,
    ParseFileResult,
    Task,
    Progress,
    TaskStatus,
    TranslateRequest,
    FileShareLinkRequest
)
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
    PictureDescriptionApiOptions
)
from .exceptions import (
    InternalServerError
)
from docling_core.types.doc import (
    TableItem,
    PictureItem,
)
from docling.pipeline.simple_pipeline import SimplePipeline
from docling.pipeline.standard_pdf_pipeline import StandardPdfPipeline
from docling.datamodel.accelerator_options import (
    AcceleratorDevice,
    AcceleratorOptions
)
from docling_core.types.doc import (
    ImageRefMode, 
    TableItem,
    TextItem
)

class TaskManager(ABC):
    
    def __init__(self):
        self.SERVICE_NAME = settings.SERVICE_NAME
        self.TASK_MANAGER_ENDPOINT = settings.TASK_MANAGER_ENDPOINT
    
    async def storage_task(self,key:str,task:Task):
        resp = requests.post(
            url=f"{self.TASK_MANAGER_ENDPOINT}/storage/task",
            json={
                "key":key,
                "task":task.model_dump(),
            }
        )
        return resp.json()
         
    
    async def update_progress(self,key:str,progress: Progress):
        resp = requests.patch(
            url=f"{self.TASK_MANAGER_ENDPOINT}/storage/update_progress",
            json={
                "key":key,
                "progress":progress.model_dump(),
            }
        )
        return resp.json()
        

    async def update_response_data(self,key:str,response_data:str):
        resp = requests.patch(
            url=f"{self.TASK_MANAGER_ENDPOINT}/storage/update_response_data",
            json={
                "key":key,
                "response_data":response_data,
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
    
    def upload_to_s3_cloud(
        self,
        files: list[UploadFile] = File(...),
    ):
        files_form = utils.compare_files(files=files)
        
        resp = requests.put(
            url=f"{settings.S3_CLOUD_ENDPOINT}/cloud/{settings.CLOUD_BUCKET_NAME}/file/upload",
            files=files_form,
        )  
        
        if resp.status_code != 200:
            raise InternalServerError(detail=f"Ошибка загрузки файлов в корзину {settings.CLOUD_BUCKET_NAME}: {resp.json()['message']}")
        
        file_links = []
        for file in files:
            resp = requests.post(
                url=settings.S3_CLOUD_ENDPOINT+f"/cloud/{settings.CLOUD_BUCKET_NAME}/file/share",
                json=FileShareLinkRequest(
                    dir_path='/',
                    expired_secs=3600,
                    file_name=file.filename,
                    only_relative_path=True,  
                ).model_dump(),
            )        
            
            if resp.status_code != 200:
                raise InternalServerError(detail=f"Ошибка получения share-линки файла {file.filename} в корзине {settings.CLOUD_BUCKET_NAME}")

            share_link = settings.MINIO_ENDPOINT + str(resp.json()['message']).split("?")[0]
            
            file_links.append(share_link)
            
        return file_links


class Translator(ABC):
    def __init__(self):
        self.TRANSLATOR_ENDPOINT = settings.TRANSLATOR_ENDPOINT
    
    def translate_text(self,translateReq:TranslateRequest) -> str: 
        resp = requests.post(
            url=self.TRANSLATOR_ENDPOINT,
            json=translateReq.model_dump(),
        )
                
        if resp.status_code != 200:
           translated_text = f"Ошибка перевода: {resp.json()['detail']}"
        else:
            translated_text = json.dumps(resp.json()['text'])
            
        return translated_text
    
class Parser(
    Uploader,
    TaskManager,
    Translator
):
    def __init__(self):
        self.TASK_MANAGER_ENDPOINT = settings.TASK_MANAGER_ENDPOINT
        self.TRANSLATOR_ENDPOINT = settings.TRANSLATOR_ENDPOINT
    
    async def parse(self,
                    file_share_link:str,
                    TASK_KEY:str,
                    parse_request:ParseRequest,
        ) -> ParseFileResult:
        
        """
            ru && en -> ["ru","rs_cyrillic","be","bg","uk","mn","en"]
            ar && en -> ["ar","fa","ur","ug","en"]
        """     
        
        lang_option = utils.get_langs(param=parse_request.src_lang if parse_request.src_lang is not None else None)
        
        ocr_options = EasyOcrOptions(
            lang=lang_option,
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
        
        # pipeline_options.do_picture_description = True
        # pipeline_options.enable_remote_services=True
        # pipeline_options.picture_description_options = PictureDescriptionApiOptions(
        #     url="http://192.168.0.59:8091/v1",
        #     params=dict(
        #         model="LocalModel",
        #         seed=42,
        #         max_completion_tokens=200
        #     ),
        #     prompt="Что изображено на картинке? Ответь одним предложением",
        #     timeout=90,
        # )

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
            page_range=(1,parse_request.max_num_page),
            raises_on_error=False,
        )   
        
        orig_filename = utils.extract_filename(file_share_link,ext=False)
        orig_filename_with_ext = utils.extract_filename(file_share_link,ext=True)
        
        resp_update_response_data = await self.update_response_data(
            key=TASK_KEY,
            response_data=f"Подготовил конвертер для файла {orig_filename_with_ext}"
        )
        
        resp_update_progress = await self.update_progress(
            key=TASK_KEY,
            progress=Progress(
                progress=0.33,
                status=TaskStatus.PROCESSING,
            )
        )

        parse_file_filename = f"parse_{orig_filename}.{settings.OUTPUT_FORMAT}"
        parse_file_bytes=conv_result.document.export_to_markdown(
            image_mode=ImageRefMode.EMBEDDED,
        ).encode('utf-8')
        
        parse_file_share_link = self.upload_to_s3_cloud(
            files=utils.build_files(
                file_bytes=parse_file_bytes,
                file_filename=parse_file_filename,
            )
        )[0]

        resp_update_progress = await self.update_progress(
            key=TASK_KEY,
            progress=Progress(
                progress=0.66,
                status=TaskStatus.PROCESSING,
            )
        )
        
        resp_update_response_data = await self.update_response_data(
            key=TASK_KEY,
            response_data=f"Обработал и загрузил новый файл {parse_file_filename} в MiniO"
        )
        
        if parse_request.translated:
            for element, _ in conv_result.document.iterate_items():
                if isinstance(element, TextItem):
                    element.text = self.translate_text(
                        translateReq=TranslateRequest(
                            text=element.text,
                            src_lang=parse_request.src_lang,
                            target_lang=parse_request.target_lang,
                        )
                   )

                elif isinstance(element, TableItem): 
                   for cell in element.data.table_cells:
                    cell.text = self.translate_text(
                        translateReq=TranslateRequest(
                            text=cell.text,
                            src_lang=parse_request.src_lang,
                            target_lang=parse_request.target_lang,
                        )
                    )
                
            translate_file_filename = f"translate_{orig_filename}_{parse_request.target_lang}.{settings.OUTPUT_FORMAT}"
            translate_file_bytes=conv_result.document.export_to_markdown(
                image_mode=ImageRefMode.EMBEDDED,
            ).encode('utf-8')
        
            translate_file_share_link = self.upload_to_s3_cloud(
                files=utils.build_files(
                    file_bytes=translate_file_bytes,
                    file_filename=translate_file_filename,
                )
            )[0]
        
            resp_update_response_data = await self.update_response_data(
                key=TASK_KEY,
                response_data=f"Перевел и загрузил новый файл {translate_file_filename} в MiniO",
            )
            
        resp_update_progress = await self.update_progress(
            key=TASK_KEY,
            progress=Progress(
                progress=0.99,
                status=TaskStatus.PROCESSING,
            )
        )
        
        return ParseFileResult(
            original_file_share_link=file_share_link,
            parse_file_share_link=parse_file_share_link,
            translated_file_share_link=translate_file_share_link
        )    