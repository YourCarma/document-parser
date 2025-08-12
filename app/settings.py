from pydantic_settings import BaseSettings
from typing import List
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

class Settings(BaseSettings):
    SERVICE_NAME: str
    HOST: str
    PORT: int
    ML_DIR: str = str(Path(__file__).parent.parent / "ml")
    
    OUTPUT_FORMAT: str
    
    TASK_MANAGER_ENDPOINT: str
    S3_CLOUD_ENDPOINT: str
    MINIO_ENDPOINT: str
    CLOUD_BUCKET_NAME: str
    
    ALLOWED_LANGS: List[str] = ["ru","en","ar","fr","uk"] #iso-639
    ALLOWED_MIME_TYPES: List[str] = [
        "image/jpeg",
        "image/webp",
        "image/png",
        "image/tiff",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.oasis.opendocument.text",
        "application/pdf",
        "text/html",
    ]
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

settings = Settings()