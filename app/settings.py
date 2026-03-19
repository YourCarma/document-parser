from typing import List
from pathlib import Path
from dotenv import load_dotenv
import os
import multiprocessing

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
            env_file=os.getenv('ENV_FILE', Path(__file__).parent.parent.joinpath(".env.dev").__str__()))
    
    SERVICE_NAME: str
    HOST: str
    PORT: int
    ML_DIR: str = str(Path(__file__).parent.parent / "ml")
    PARSER_WORKERS: int = multiprocessing.cpu_count()

    VLM_BASE_URL: str = "localhost:8097"
    VLM_MODEL_NAME: str = "Qwen2.5-VL-7B-Instruct-Q6_K"
    VLM_API_KEY: str = "no-key-required"
    VLM_MAX_TOKENS: int = 16000
    VLM_TIMEOUT_SECS: int = 50
    
    TRANSLATOR_ADDRESS: str = "http://localhost:8000"
    TRANSLATE_URI: str = "/translate/text"
    
    
    DETECT_LANGUAGE_URL: str = "http://localhost:10015/api/v1/detect_language"
    TRANSALTOR_MAX_CONCURRENCY: int = 15

    WEBHOOK_MANAGER_URL: str = "http://localhost:8010"
    WATCHTOWER_URL: str = "http://localhost:8020"
    WATCHTOWER_SHARED_HOST: str = "http://localhost:8020"
    RESOURCE_MANAGER_URL: str = "http://localhost:8030"

    
    
    ALLOWED_MIME_TYPES: List[str] = [
        "text/plain",
        "image/jpeg",
        "image/webp",
        "image/png",
        "text/html",
        "image/tiff",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.oasis.opendocument.text",
        "application/pdf",
        "application/octet-stream",
        "application/vnd.oasis.opendocument.text",
        "application/vnd.oasis.opendocument.presentation",
        "application/vnd.oasis.opendocument.spreadsheet"
    ]

    @property
    def TRANSLATOR_TRANSLATE_URL(cls):
        return f"{cls.TRANSLATOR_ADDRESS}{cls.TRANSLATE_URI}"
    
    @property
    def ARTIFACTS_PATH(cls):
        return Path(__file__).parent.parent.joinpath("ml")
    
settings = Settings()
