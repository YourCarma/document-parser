from pydantic_settings import BaseSettings
from typing import List
from pathlib import Path

class Settings(BaseSettings):
    HOST: str = '0.0.0.0'
    PORT: int = 1337
    # SERVICE_NAME = "Sova-Parser"
    DOCUMENTS_DIR:str = str(Path(__file__).parent.parent / "documents")
    SCRATCH_DIR:str = str(Path(__file__).parent.parent / "scratch")
    ML_DIR: str = str(Path(__file__).parent.parent / "ml")
    
    ALLOWED_LANGS: List[str] = ["ru","en","ar","fr","uk"]
    ALLOWED_MIME_TYPES: List[str] = [
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/doc",
        "application/msword",
        "application/pdf",
        "image/png",
        "image/jpeg",
        "image/webp",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/vnd.ms-powerpoint",
    ]
    ALLOWED_CONVERTED_TYPES: List[str] = ["md","json","yaml","txt"]
    TTS_URL: str = "http://192.168.0.59:5000/tts/converted"
    AUDIOS_PATH: str = "/mnt/king/sova.git/services/text_to_speech/audios/"
    SUMMARIZER_URL: str = "http://192.168.0.67:8009/llm_tools/summary_text"
    TASK_MANAGER_URL: str = "http://192.168.0.35:10001/storage/task"
    OPENAI_URL: str = "http://192.168.0.59:8091/v1"
    OPENAI_KEY: str = "sk-no-key-required"
    TRANSLATOR_URL: str = "http://192.168.0.59:8001/translator/text"
    SERVICE_NAME: str = "Sova-Parser"
    
settings = Settings()