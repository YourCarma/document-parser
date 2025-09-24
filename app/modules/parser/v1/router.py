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
from modules.parser.v1.schemas import ParserRequest
from modules.parser.v1.service import ParserFactory
from modules.parser.v1.utils import save_file, delete_file

router = APIRouter(prefix="/v1/parser")

@router.post(
    path="/parse",
    name="Парсинг",
    summary="Парсинг и опциональный перевод загруженных файлов",
    description="""   
""",
    tags=['Parser']
)
async def parse(
        parser_data: ParserRequest = Depends()):    
    try: 
        file = parser_data.file
        file_path = await save_file(file)
        parser = ParserFactory(file_path).get_parser()
        text = parser.parse()
        return text
    except Exception as e:
        logger.error(f"Error in request: {e}")
        raise e
    finally:
        await delete_file(file_path)