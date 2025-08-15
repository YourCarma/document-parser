from pydantic import BaseModel,Field
from enum import Enum

class ParseRequest(BaseModel):
    translated:bool = Field(...,title="Флажок перевода")
    target_lang: str = Field(..., title="Целевой язык", description="🌍 Язык для перевода", example="en")
    src_lang:str = Field(..., title="Исходный язык", description="🌍 Язык документов", example="ru")
    max_num_page: int = Field(...,title="Кол-во страниц для распознавания")

class Progress(BaseModel):
    progress: float
    status: str
        
class Task(BaseModel):
    task_id: str
    user_id: str
    progress: Progress
    response_data: str 

class TaskStatus(Enum):
   PENGING="PENDING"
   AWAITING="AWAITING"
   PROCESSING="PROCESSING"
   READY="READY"
   ERROR="ERROR"