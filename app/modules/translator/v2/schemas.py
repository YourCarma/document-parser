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
        description=(
            "Object key оригинального файла внутри бакета пользователя. "
            "Именно ключ, а не ссылка: ссылка протухает по сроку."
        ),
        examples=["documents/report.pdf"],
    )
    translated_file: str = Field(
        default="",
        description="Object key переведённого файла внутри бакета пользователя.",
        examples=["translated/5fb0b68c/report_(переведённый).docx"],
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


class TranslationOutcome(BaseModel):
    """Результат этапа перевода документа."""

    file_path: str = Field(description="Путь к временному переведённому .docx.")
    untranslated_count: int = Field(
        default=0,
        description="Сколько элементов не переведено.",
    )
