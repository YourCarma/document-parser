from pydantic import BaseModel,Field
from enum import Enum
from typing import List,Dict
class ParseRequest(BaseModel):
    translated:bool = Field(...,title="Флажок перевода")
    target_lang: str = Field(..., title="Целевой язык", description="🌍 Язык для перевода", example="en")
    src_lang:str = Field(..., title="Исходный язык", description="🌍 Язык документов", example="ru")
    max_num_page: int = Field(...,title="Кол-во страниц для распознавания")

class ParseFileResult(BaseModel):
    original_file_share_link:str 
    parse_file_share_link:str
    translated_file_share_link:str

class ParseResponse(BaseModel):
    results: list

class Progress(BaseModel):
    progress: float
    status: str

class Task(BaseModel):
    task_id: str
    user_id: str
    service: str
    created_at:str
    updated_at: str
    progress: Progress
    response_data: str

class TranslateRequest(BaseModel):
    text: str
    with_dict: bool = True
    src_lang: str
    target_lang: str

class FileShareLinkRequest(BaseModel):
    dir_path: str
    expired_secs: int
    file_name: str
    only_relative_path: bool 
       
class TaskStatus(Enum):
   PENGING="PENDING"
   AWAITING="AWAITING"
   PROCESSING="PROCESSING"
   READY="READY"
   ERROR="ERROR"