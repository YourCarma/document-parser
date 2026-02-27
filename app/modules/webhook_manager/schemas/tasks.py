from datetime import datetime
from pydantic import BaseModel, Field
from enum import Enum


class Status(Enum, str):
    PENDING = "PENDING"
    AWAITING = "AWAITING"
    PROCESSING = "PROCESSING"
    ERROR = "ERROR"

class Progress(BaseModel):
    progress: float = Field(default=0.0, description="Progress of a task", ge=0.0, le=100)
    status: Status = Field(default=Status.PROCESSING, description="Status of task")
    
class TaskInfo:
    created_at: datetime = Field(default=datetime.now().astimezone())