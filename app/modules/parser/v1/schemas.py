from typing import Optional, Union
import enum
from pathlib import Path

from pydantic import BaseModel, field_validator, Field
from fastapi import UploadFile, File
from loguru import logger

from settings import settings
from modules.parser.v1.exceptions import ContentNotSupportedError


class ParserRequest(BaseModel):
    file: UploadFile = File(description="Файл для парсинга")
    parse_images: Optional[bool]  = Field(description="Необходимо распознавать вложенные изображения (Необходимо наличие VLM)", default=False)
    include_image_in_output: Optional[bool] = Field(description="Вшивать изображения в текст вида `base64`", default=False)
    
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
    
class ParserResponse(BaseModel):
    parsed_text: str = Field(description="Распознанный текст в формате markdown", examples=["## Heading"])

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
    HTML = [".html"]

class ParserParams(BaseModel):
    file_path: Union[str, Path] = Field(description="Путь к файлу")
    parse_images: Optional[bool]  = Field(description="Необходимо распознавать вложенные изображения (Необходимо наличие VLM)", default=True)
    include_image_in_output: Optional[bool] = Field(description="Вшивать изображения в текст вида `base64`", default=True)
