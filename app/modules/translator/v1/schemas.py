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
    file: UploadFile = File(description="Файл, который нужно перевести.")
    parse_images: Optional[bool] = Field(
        description="Распознавать встроенные изображения через VLM перед переводом.",
        default=False,
    )
    include_image_in_output: Optional[bool] = Field(
        description="Встраивать изображения документа в промежуточный Markdown.",
        default=False,
    )
    full_vlm_pdf_parse: Optional[bool] = Field(
        description="Обрабатывать PDF целиком через VLM. Полезно для сложных PDF.",
        default=False,
    )
    source_language: Optional[str] = Field(
        description=(
            "Исходный язык документа в формате ISO 639-1. Можно передать `auto`, "
            "тогда язык будет определён автоматически."
        ),
        default="en",
    )
    target_language: Optional[str] = Field(
        description="Целевой язык перевода в формате ISO 639-1.",
        default="ru",
    )

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
        logger.debug(
            "Проверка входного файла переводчика: filename='{}' mime='{}' size={}",
            file.filename,
            file.content_type,
            file.size,
        )
        if file.content_type not in settings.ALLOWED_MIME_TYPES:
            file_extension = file.filename.split(".")[-1]
            raise ContentNotSupportedError(f"Данный формат файла \"{file_extension}\" не поддерживается")
        logger.debug("MIME type поддерживается: filename='{}'", file.filename)
        return file

class TranslatorTextResponse(BaseModel):
    parsed_text: str = Field(
        description="Переведённый текст документа в формате Markdown.",
        examples=["## Заголовок\n\nПереведённый текст документа"],
    )


class CustomTranslatorBody(BaseModel):
    text: str = Field(description="Исходный текст для перевода.")
    source_language: str = Field(description="Код исходного языка.")
    target_language: str = Field(description="Код целевого языка.")
