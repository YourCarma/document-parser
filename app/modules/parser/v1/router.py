from datetime import timezone, datetime
from typing import List, Dict
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from loguru import logger
from fastapi.responses import FileResponse

from settings import settings
from .exceptions import BadRequestError, ContentNotSupportedError
from modules.parser.v1.schemas import ParserRequest, ParserTextResponse, FileFormats, ParserParams, ParserMods
from modules.parser.v1.abc.factory import ParserFactory
from modules.parser.v1.utils import save_file, delete_file, run_in_process

router = APIRouter(prefix="/api/v1/parser")


@router.post(path="/parse/text",
             name="File parsing",
             summary="Парсинг файлов в MD текст",
             description=f"""   
    ## Парсинг файлов в MD ТЕКСТ
  ### Поддерживаемые MIME-типы:
  ```python 
  {settings.ALLOWED_MIME_TYPES}
  ```
  ### Поддерживаемые форматы:
  
 * **DOC**: `{FileFormats.DOC.value}`
 * **PDF**: `{FileFormats.PDF.value}`
 * **XLSX**: `{FileFormats.XLSX.value}`
 * **IMAGES**: `{FileFormats.IMAGE.value}`
 * **HTML**: `{FileFormats.HTML.value}`
 * **PPTX**: `{FileFormats.PPTX.value}`
 * **TXT**: `{FileFormats.TXT.value}`
  ### Параметры запроса (QUERY):
 1. `parse_images`: `bool | None` - Необходимо распознавать текст на изображениях (**требуется** подключение к VLM, может занимать больше времени)
 2. `include_image_in_output`: `bool | None` - Вшивать изображения исходного документа в **OUTPUT** в виде `base64` (Может излишне нагружать markdown)
 3. `full_vlm_pdf_parse`: `bool | None` - Полный парсинг .pdf с помощью VLM (может занять куда больше времени, возврат без картинок)

  ### Возвращаемый объект:
 ```python
 {ParserTextResponse.model_fields}
 ``` 
    """,
             tags=['Parser'])
async def parse_to_text(request_fastapi: Request, parser_data: ParserRequest = Depends()) -> ParserTextResponse:
    try:
        file = parser_data.file
        file_path = await save_file(file)
        parser_params = ParserParams(
            file_path=file_path,
            parse_images=parser_data.parse_images,
            include_image_in_output=parser_data.include_image_in_output,
            full_vlm_pdf_parse=parser_data.full_vlm_pdf_parse)
        parser = ParserFactory(parser_params).get_parser()
        text = await run_in_process(parser.parse, request_fastapi.app.state.executor, ParserMods.TO_TEXT)
        instance = ParserTextResponse(parsed_text=text)
        return instance
    except Exception as e:
        logger.error(f"Error in request: {e}")
        raise e
    finally:
        await delete_file(file_path)

@router.post(path="/parse/file",
             name="File parsing to **MD FILE**",
             summary="Парсинг файлов в файл",
             description=f"""   
    ## Парсинг файлов в MARKDOWN ФАЙЛ
  ### Поддерживаемые MIME-типы:
  ```python 
  {settings.ALLOWED_MIME_TYPES}
  ```
  ### Поддерживаемые форматы:
  
 * **DOC**: `{FileFormats.DOC.value}`
 * **PDF**: `{FileFormats.PDF.value}`
 * **XLSX**: `{FileFormats.XLSX.value}`
 * **IMAGES**: `{FileFormats.IMAGE.value}`
 * **HTML**: `{FileFormats.HTML.value}`
 * **PPTX**: `{FileFormats.PPTX.value}`
 * **TXT**: `{FileFormats.TXT.value}`
  ### Параметры запроса:
 1. `parse_images`: `bool | None` - Необходимо распознавать текст на изображениях (**требуется** подключение к VLM, может занимать больше времени)
 2. `include_image_in_output`: `bool | None` - Вшивать изображения исходного документа в **OUTPUT** в виде `base64` (Может излишне нагружать markdown)
 3. `full_vlm_pdf_parse`: `bool | None` - Полный парсинг .pdf с помощью VLM (может занять куда больше времени, возврат без картинок)
 
  ### Возвращаемый объект:
 ```python
 {FileResponse}
 ``` 
    """,
             tags=['Parser'])
async def parse_to_file(request_fastapi: Request, parser_data: ParserRequest = Depends()) -> FileResponse:
    try:
        file = parser_data.file
        file_path = await save_file(file)
        parser_params = ParserParams(
            file_path=file_path,
            parse_images=parser_data.parse_images,
            include_image_in_output=parser_data.include_image_in_output,
            full_vlm_pdf_parse=parser_data.full_vlm_pdf_parse)
        parser = ParserFactory(parser_params).get_parser()
        file = await run_in_process(parser.parse, request_fastapi.app.state.executor, ParserMods.TO_FILE)
        return FileResponse(path=file, filename=str(Path(file_path).stem + ".md"))
    except Exception as e:
        logger.error(f"Error in request: {e}")
        raise e
    finally:
        await delete_file(file_path)
        
@router.post(path="/parse/file/word",
             name="File parsing to WORD FILE",
             summary="Парсинг файлов в файл",
             description=f"""   
    ## Парсинг файлов в WORD ФАЙЛ
  ### Поддерживаемые MIME-типы:
  ```python 
  {settings.ALLOWED_MIME_TYPES}
  ```
  ### Поддерживаемые форматы:
  
 * **DOC**: `{FileFormats.DOC.value}`
 * **PDF**: `{FileFormats.PDF.value}`
 * **XLSX**: `{FileFormats.XLSX.value}`
 * **IMAGES**: `{FileFormats.IMAGE.value}`
 * **HTML**: `{FileFormats.HTML.value}`
 * **PPTX**: `{FileFormats.PPTX.value}`
 * **TXT**: `{FileFormats.TXT.value}`
  ### Параметры запроса:
 1. `parse_images`: `bool | None` - Необходимо распознавать текст на изображениях (**требуется** подключение к VLM, может занимать больше времени)
 2. `include_image_in_output`: `bool | None` - Вшивать изображения исходного документа в **OUTPUT** в виде `base64` (Может излишне нагружать markdown)
 3. `full_vlm_pdf_parse`: `bool | None` - Полный парсинг .pdf с помощью VLM (может занять куда больше времени, возврат без картинок)
 
  ### Возвращаемый объект:
 ```python
 {FileResponse}
 ``` 
    """,
             tags=['Parser'])
async def parse_to_file(request_fastapi: Request, parser_data: ParserRequest = Depends()) -> FileResponse:
    try:
        file = parser_data.file
        file_path = await save_file(file)
        parser_params = ParserParams(
            file_path=file_path,
            parse_images=parser_data.parse_images,
            include_image_in_output=parser_data.include_image_in_output,
            full_vlm_pdf_parse=parser_data.full_vlm_pdf_parse)
        parser = ParserFactory(parser_params).get_parser()
        file = await run_in_process(parser.parse, request_fastapi.app.state.executor, ParserMods.TO_WORD)
        return FileResponse(path=file, filename=str(Path(file_path).stem + ".docx"))
    except Exception as e:
        logger.error(f"Error in request: {e}")
        raise e
    finally:
        await delete_file(file_path)
        

