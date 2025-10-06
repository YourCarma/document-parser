from datetime import timezone,datetime
from typing import List,Dict
import tempfile
import os
import aiofiles

from fastapi import (
    APIRouter, Depends, HTTPException
)
from loguru import logger


from settings import settings
from .exceptions import (
    BadRequestError,
    ContentNotSupportedError
)
from modules.parser.v1.schemas import ParserRequest, ParserResponse, FileFormats, ParserParams
from modules.parser.v1.abc.factory import ParserFactory
from modules.parser.v1.utils import save_file, delete_file

router = APIRouter(prefix="/v1/parser")

@router.post(
    path="/parse",
    name="File parsing",
    summary="Парсинг файлов",
    description=f"""   
    ## Парсинг файлов
  ### Поддерживаемые MIME-типы:
  ```python 
  {settings.ALLOWED_MIME_TYPES}
  ```
  ### Поддерживаемые форматы:
  
 * **HTML**: `{FileFormats.DOC.value}`
 * **PDF**: `{FileFormats.PDF.value}`
 * **XLSX**: `{FileFormats.XLSX.value}`
 * **IMAGES**: `{FileFormats.IMAGE.value}`
 * **HTML**: `{FileFormats.HTML.value}`
 * **PPTX**: `{FileFormats.PPTX.value}`
  ### Параметры запроса:
 1. `parse_images`: `bool | None` - Необходимо распознавать текст на изображениях (**требуется** подключение к VLM, может занимать больше времени)
 2. `include_image_in_output`: `bool | None` - Вшивать изображения исходного документа в **OUTPUT** в виде `base64` (Может излишне нагружать markdown)

  ### Возвращаемый объект:
 ```python
 {ParserResponse.model_fields}
 ``` 
    """,
    tags=['Parser']
)
async def parse(
        parser_data: ParserRequest = Depends()) -> ParserResponse:    
    try: 
        file = parser_data.file
        file_path = await save_file(file)
        parser_params = ParserParams(file_path=file_path,
                                     parse_images=parser_data.parse_images,
                                     include_image_in_output=parser_data.include_image_in_output)
        parser = ParserFactory(parser_params).get_parser()
        text = parser.parse()
        instance = ParserResponse(parsed_text=text)
        return instance
    except Exception as e:
        logger.error(f"Error in request: {e}")
        raise e
    finally:
        await delete_file(file_path)