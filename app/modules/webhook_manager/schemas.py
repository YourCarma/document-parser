from datetime import datetime
from enum import Enum

from pydantic import BaseModel


class TaskStatus(str, Enum):
    PENDING = "PENDING"
    AWAITING = "AWAITING"
    PROCESSING = "PROCESSING"
    READY = "READY"
    ERROR = "ERROR"


class TaskProgress(BaseModel):
    progress: float
    status: TaskStatus


class Task(BaseModel):
    task_id: str
    user_id: str
    service: str
    progress: TaskProgress
    created_at: datetime
    updated_at: datetime
    response_data: str  # JSON string


class TaskCreation(BaseModel):
    key: str
    task: Task


class ProgressUpdate(BaseModel):
    key: str
    progress: TaskProgress


class ResponseDataUpdate(BaseModel):
    key: str
    response_data: str  # JSON string
