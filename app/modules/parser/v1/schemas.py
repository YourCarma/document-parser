from typing import Optional
import enum

from pydantic import BaseModel, field_validator, Field
from fastapi import UploadFile, File
from loguru import logger

from settings import settings
from modules.parser.v1.exceptions import ContentNotSupportedError

class ParserRequest(BaseModel):
    file: UploadFile = File(description="Файл для парсинга")

    class Config:
        arbitrary_types_allowed = True
    
    @field_validator("file", mode="after")  
    @classmethod
    def is_allowed_mime_typy(cls, file: UploadFile) -> UploadFile:
        logger.debug(f"Getting file: {file.filename}")
        logger.debug(f"MIME type: {file.content_type}")
        logger.debug(f"File Size: {file.size}")
        if file.content_type not in settings.ALLOWED_MIME_TYPES:
            raise ContentNotSupportedError(f"Данный формат файла \"{file.filename.split(".")[-1]}\" не поддерживается")
        logger.success("File MIME type supported!")
        return file
    

class DocLingAPIVLMOptionsParams(BaseModel):
    model: str = Field(description="Имя модели VLM")
    max_tokens: Optional[int] = Field(default=4096)
    skip_special_tokens: Optional[bool] = Field(default=False)

class FileFormats(enum.Enum):
    IMAGE = [".jpg", ".jpeg", ".png", ".tiff", ".bmp", ".webp"]
    PDF = [".pdf"]
    DOC = [".docx"]
    PPTX = [".pptx"]
    XLSX = [".xlsx"]