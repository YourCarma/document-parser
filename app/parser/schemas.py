from pydantic import BaseModel,Field
from enum import Enum
from fastapi import File,UploadFile,Form
from dataclasses import dataclass, field
from typing import Dict,List
from uuid import UUID,uuid4
from datetime import datetime
from typing import Optional,Dict,Any

class ParseRequest(BaseModel):
    translated:bool = Field(...,title="Флажок перевода")
    target_lang: str = Field(..., title="Целевой язык", description="🌍 Язык для перевода", example="en")
    src_lang:str = Field(..., title="Исходный язык", description="🌍 Язык документов", example="ru")
    max_num_page: int = Field(...,title="Кол-во страниц для распознавания")

class Progress(BaseModel):
    progress: float = 0.0 # значение от 0.0 до 1.0
    status: str = "PENDING"
        
class Task(BaseModel):
    task_id: str
    user_id: str
    # created_at: datetime
    # updated_at: datetime
    progress: Progress
    response_data: Optional[Dict[str,Any]] = None 

class TaskStatus(Enum):
   PENGING="PENDING"
   AWAITING="AWAITING"
   PROCESSING="PROCESSING"
   READY="READY"
   ERROR="ERROR"