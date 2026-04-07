from typing import Optional

from pydantic import BaseModel, Field


class TranslatorResponseData(BaseModel):
    """Снимок состояния async-задачи, который хранится в webhook_manager."""

    original_language: str = Field(
        description="Исходный язык документа или язык, определённый автоматически.",
        examples=["en"],
    )
    target_language: str = Field(
        description="Целевой язык перевода.",
        examples=["ru"],
    )
    original_file: str = Field(
        default="",
        description="Share-ссылка на оригинальный файл в хранилище.",
    )
    translated_file: str = Field(
        default="",
        description="Share-ссылка на переведённый файл в хранилище.",
    )
    text_status: str = Field(
        default="Задача принята",
        description="Человекочитаемый статус для UI и операторской диагностики.",
        examples=["Перевожу... 45/120 элементов"],
    )
    error: Optional[str] = Field(
        default=None,
        description="Техническая ошибка, если задача завершилась со статусом ERROR.",
    )


class TranslatorV2Response(BaseModel):
    task_id: str = Field(
        description="Идентификатор созданной асинхронной задачи.",
        examples=["8d6d5d4e-b4cb-4cf0-8d46-0e6e23a6b469"],
    )
    key: str = Field(
        description="Ключ задачи в формате `user_id:service:task_id`.",
        examples=["user-42:document-parser:8d6d5d4e-b4cb-4cf0-8d46-0e6e23a6b469"],
    )
