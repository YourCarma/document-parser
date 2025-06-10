from pydantic import BaseModel,Field
from enum import Enum
from fastapi import File,UploadFile,Form
from dataclasses import dataclass, field
from typing import Dict,List
from uuid import UUID,uuid4
from datetime import datetime
from typing import Optional,Dict,Any

class ExtractRequest(BaseModel):
    translated:bool = Field(...,title="Флажок для необходимости перевода")
    target_lang: str = Field(..., title="Исходный язык", description="🌍 Язык для перевода", example="en")
    src_lang:str = Field(..., title="Исходный язык", description="🌍 Язык документов", example="ru")
    target_conv_format:str = Field(..., title="Выбранный формат конвертации", description="🎯 Целевой формат", example="md")
    extracted_elements:list[str] = Field(default=["tables", "pictures"], description="Элементы для извлечения",example=["tables", "pictures"])
    max_num_page: int = Field(...,title="Кол-во страниц для распознавания")

class UploadDocumentsRequest(BaseModel):
    src_lang: str = Form(..., title="Исходный язык", description="🌍 Язык документов", example="ru")
    target_conv_format: str = Form(..., title="Выбранный формат конвертации", description="🎯 Целевой формат", example="md")
    extracted_elements: List[str] = Form(default=["tables", "pictures"], description="Элементы для извлечения",example=["tables", "pictures"])

class ConvertTextRequest(BaseModel):
    text: str = Field(...,description="Исходный текст",example="text")
    voice_name: str = Field(...,description="Выбранный голос",example="Alice")

class Progress(BaseModel):
    progress: float = 0.0 # значение от 0.0 до 1.0
    status: str = "Pending"
        
class Task(BaseModel):
    task_id: UUID
    user_id: UUID
    # created_at: datetime
    # updated_at: datetime
    progress: Progress
    # response_data: Optional[Dict[str,Any]] = None 

class TaskStatus(Enum):
   PENGING="Pending"
   AWAITING="Awaiting"
   PROCESSING="Processing"
   READY="Ready"
   ERROR="Error"