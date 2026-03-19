from typing import Optional

from pydantic import BaseModel, Field


class TranslatorResponseData(BaseModel):
    """Live UI state stored as response_data in webhook_manager."""

    original_language: str
    target_language: str
    original_file: str = ""
    translated_file: str = ""
    text_status: str = "Задача принята"
    error: Optional[str] = None


class TranslatorV2Response(BaseModel):
    task_id: str = Field(description="ID созданной задачи")
    key: str = Field(description="Ключ задачи в формате user_id:service:task_id")
