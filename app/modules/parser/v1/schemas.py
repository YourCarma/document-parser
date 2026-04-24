from typing import Optional, Union
import enum
from pathlib import Path

from pydantic import BaseModel, field_validator, Field
from fastapi import UploadFile, File
from loguru import logger

from settings import settings
from modules.parser.v1.exceptions import ContentNotSupportedError


class ParserRequest(BaseModel):
    file: UploadFile = File(description="Файл, который нужно распознать.")
    parse_images: Optional[bool] = Field(
        description=(
            "Распознавать встроенные изображения через VLM. Увеличивает время "
            "обработки и требует доступного VLM-сервиса."
        ),
        default=False,
    )
    include_image_in_output: Optional[bool] = Field(
        description=(
            "Встраивать изображения исходного документа в итоговый Markdown в "
            "виде ссылок или data payload. Может заметно увеличить размер ответа."
        ),
        default=False,
    )
    full_vlm_pdf_parse: Optional[bool] = Field(
        description=(
            "Полностью обрабатывать PDF через VLM вместо стандартного пайплайна "
            "Docling. Используется для сложных PDF, но работает медленнее."
        ),
        default=False,
    )
    
    @field_validator("file", mode="after")  
    @classmethod
    def is_allowed_mime_typy(cls, file: UploadFile) -> UploadFile:
        logger.debug(
            "Проверка входного файла: filename='{}' mime='{}' size={}",
            file.filename,
            file.content_type,
            file.size,
        )
        if file.content_type not in settings.ALLOWED_MIME_TYPES:
            file_extension = file.filename.split(".")[-1]
            raise ContentNotSupportedError(f"Данный формат файла \"{file_extension}\" не поддерживается")
        logger.debug("MIME type поддерживается: filename='{}'", file.filename)
        return file
    
class ParserTextResponse(BaseModel):
    parsed_text: str = Field(
        description="Распознанный текст документа в формате Markdown.",
        examples=["## Заголовок\n\nТекст документа"],
    )

class DocLingAPIVLMOptionsParams(BaseModel):
    model: str = Field(description="Имя VLM-модели, используемой для OCR.")
    max_tokens: Optional[int] = Field(default=4096)
    skip_special_tokens: Optional[bool] = Field(default=False)

class FileFormats(list[str], enum.Enum):
    IMAGE = [".jpg", ".jpeg", ".png", ".tiff", ".bmp", ".webp"]
    PDF = [".pdf"]
    DOC = [".docx", ".odt", ".doc", '.rtf']
    PPTX = [".pptx", ".odp"]
    XLSX = [".xlsx", ".ods"]
    HTML = [".html"]
    TXT = [".txt"]

class ParserMods(str, enum.Enum):
    TO_TEXT = "to_text"
    TO_FILE = "to_file"
    TO_DOCLING = "to_docling"
    TO_WORD = "to_word"
    
class ParserParams(BaseModel):
    file_path: Union[str, Path] = Field(description="Путь к временному файлу на диске.")
    parse_images: Optional[bool] = Field(
        description="Признак OCR по встроенным изображениям.",
        default=False,
    )
    include_image_in_output: Optional[bool] = Field(
        description="Признак встраивания изображений в итоговый Markdown.",
        default=False,
    )
    full_vlm_pdf_parse: Optional[bool] = Field(
        description="Признак полного VLM-парсинга PDF.",
        default=False,
    )
    
    
class ConvertationOutputs(str, enum.Enum):
    DOCUMENTS = "docx"
    PRESENTATION = "pptx"
