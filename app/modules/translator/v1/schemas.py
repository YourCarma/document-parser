from typing import Optional, Union
import enum
from pathlib import Path

from iso639 import Lang
from pydantic import BaseModel, field_validator, Field
from fastapi import UploadFile, File, status
from loguru import logger

from settings import settings
from modules.parser.v1.exceptions import ContentNotSupportedError
from modules.translator.v1.exceptions import InvalidLanguageCode


class TranslatorRequest(BaseModel):
    file: UploadFile = File(description="Файл для парсинга")
    parse_images: Optional[bool]  = Field(description="Необходимо распознавать вложенные изображения (Необходимо наличие VLM)", default=False)
    include_image_in_output: Optional[bool] = Field(description="Вшивать изображения в текст вида `base64`", default=False)
    source_language: Optional[str] = Field(description="Исходынй язык перевода", default="en")
    target_language: Optional[str] = Field(description="Целевой язык перевода", default="ru")

    @field_validator('source_language', 'target_language', mode="after")
    def convert_to_iso639(cls, lang: str) -> str:
        lang = lang.strip()
        if lang == "auto":
            return lang
        if lang is None:
            return None
        if len(lang) == 2 and lang.isalpha():
            try:
                Lang(lang)
                return lang.lower()
            except:
                pass
        try:
            lang = Lang(lang)
            return lang.pt1.lower() if lang.pt1 else lang.pt3.lower()
        except Exception:
            raise InvalidLanguageCode(detail="Неверный формат кода языка. Проверьте соответсвие на iso639")

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

class TranslatorTextResponse(BaseModel):
    parsed_text: str = Field(description="Распознанный текст в формате markdown", examples=["## Heading"])


class CustomTranslatorBody(BaseModel):
    text: str = Field(description="Original text")
    source_language: str = Field(description="Source language")
    target_language: str = Field(description="Target language")
