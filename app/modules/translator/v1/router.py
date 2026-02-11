from datetime import timezone, datetime
from typing import List, Dict
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from loguru import logger
from fastapi.responses import FileResponse

from settings import settings
from modules.translator.v1.schemas import TranslatorRequest, TranslatorTextResponse
from modules.parser.v1.abc.factory import ParserFactory
from modules.parser.v1.utils import save_file, delete_file, run_in_process
from modules.translator.v1.service import CustomModelTranslator
from modules.parser.v1.schemas import ParserMods, ParserParams, FileFormats

router = APIRouter(prefix="/api/v1/parser")

@router.post(path="/translator/text",
             name="Translating to MD text",
             summary="Translating files to MD text",
             description=f"""
    ## Перевод файлов в WORD ФАЙЛ
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
 4. `source_language`: `str` - Исходный язык перевода в формате `iso639-1`
 5. `target_language`: `str` - Целевой язык перевода в формате `iso639-1`
 
  ### Возвращаемый объект:
 ```python
 {TranslatorTextResponse.model_fields}
 ```    
    """,
             tags=['Translator'])
async def translate_file_to_text(request_fastapi: Request, translator_data: TranslatorRequest = Depends()) -> TranslatorTextResponse:
    try:
        source_language = translator_data.source_language
        target_language = translator_data.target_language
        logger.debug(f"Source language: {source_language}")
        logger.debug(f"Target language: {target_language}")
        file = translator_data.file
        file_path = await save_file(file)
        parser_params = ParserParams(
            file_path=file_path,
            parse_images=translator_data.parse_images,
            include_image_in_output=translator_data.include_image_in_output,
            full_vlm_pdf_parse=translator_data.full_vlm_pdf_parse)
        
        parser = ParserFactory(parser_params).get_parser()
        parsed = await run_in_process(parser.parse, request_fastapi.app.state.executor, ParserMods.TO_FILE)
        translator = CustomModelTranslator(Path(parsed), source_language, target_language, translator_data.include_image_in_output)
        transalted = await translator.translate(ParserMods.TO_TEXT)
        instance = TranslatorTextResponse(parsed_text=transalted)
        return instance
    except Exception as e:
        logger.error(f"Error in request: {e}")
        raise e
    finally:
        await delete_file(file_path)

@router.post(path="/translator/file",
             name="Transalting file",
             summary="Translating to MD file",
             description=f"""   
    ## Перевод файлов в MD ФАЙЛ
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
 4. `source_language`: `str` - Исходный язык перевода в формате `iso639-1`
 5. `target_language`: `str` - Целевой язык перевода в формате `iso639-1`
 
  ### Возвращаемый объект:
 ```python
 {FileResponse}
 ``` 
    """,
             tags=['Translator'])
async def translate_file_to_file(request_fastapi: Request, translator_data: TranslatorRequest = Depends()):
    try:
        source_language = translator_data.source_language
        target_language = translator_data.target_language
        logger.debug(f"Source language: {source_language}")
        logger.debug(f"Target language: {target_language}")
        file = translator_data.file
        file_path = await save_file(file)
        parser_params = ParserParams(
            file_path=file_path,
            parse_images=translator_data.parse_images,
            include_image_in_output=translator_data.include_image_in_output,
            full_vlm_pdf_parse=translator_data.full_vlm_pdf_parse)
        
        parser = ParserFactory(parser_params).get_parser()
        parsed = await run_in_process(parser.parse, request_fastapi.app.state.executor, ParserMods.TO_DOCLING)
        translator = CustomModelTranslator(parsed, source_language, target_language, translator_data.include_image_in_output)
        # transalted_path = await translator.translate(ParserMods.TO_FILE)
        transalted_path = await translator.translate_docling(ParserMods.TO_FILE, parsed)
        return FileResponse(path=transalted_path, filename=f"{Path(file_path).stem}(переведенный).md")
    except Exception as e:
        logger.error(f"Error in request: {e}")
        raise e
    finally:
        await delete_file(file_path)
        
@router.post(path="/translator/file/word",
             name="Transalting file to WORD",
             summary="Translating to WORD file",
             description=f"""   
    ## Перевод файлов в WORD ФАЙЛ
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
 4. `source_language`: `str` - Исходный язык перевода в формате `iso639-1`
 5. `target_language`: `str` - Целевой язык перевода в формате `iso639-1`
 
  ### Возвращаемый объект:
 ```python
 {FileResponse}
 ``` 
    """,
             tags=['Translator'])
async def translate_file_to_word(request_fastapi: Request, translator_data: TranslatorRequest = Depends()):
    try:
        source_language = translator_data.source_language
        target_language = translator_data.target_language
        logger.debug(f"Source language: {source_language}")
        logger.debug(f"Target language: {target_language}")
        file = translator_data.file
        file_path = await save_file(file)
        parser_params = ParserParams(
            file_path=file_path,
            parse_images=translator_data.parse_images,
            include_image_in_output=translator_data.include_image_in_output,
            full_vlm_pdf_parse=translator_data.full_vlm_pdf_parse)
        
        parser = ParserFactory(parser_params).get_parser()
        parsed = await run_in_process(parser.parse, request_fastapi.app.state.executor, ParserMods.TO_DOCLING)
        translator = CustomModelTranslator(parsed, source_language, target_language, translator_data.include_image_in_output, max_concurrency=settings.TRANSALTOR_MAX_CONCURRENCY)
        # transalted_path = await translator.translate(ParserMods.TO_WORD)
        transalted_path = await translator.translate_docling(ParserMods.TO_WORD, parsed)
        return FileResponse(path=transalted_path, filename=f"{Path(file_path).stem}(переведенный).docx")
    except Exception as e:
        logger.error(f"Error in request: {e}")
        raise e
    finally:
        await delete_file(file_path)
        



