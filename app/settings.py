from typing import List
from pathlib import Path
from dotenv import load_dotenv
import os

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
            env_file=os.getenv('ENV_FILE', Path(__file__).parent.parent.joinpath(".env.dev").__str__()))
    
    SERVICE_NAME: str
    HOST: str
    PORT: int
    ML_DIR: str = str(Path(__file__).parent.parent / "ml")

    VLM_BASE_URL: str = "localhost:8097"
    VLM_MODEL_NAME: str = "Qwen2.5-VL-7B-Instruct-Q6_K"
    VLM_API_KEY: str = "no-key-required"

    TRANSLATOR_ADDRESS: str = "http://localhost:8000"
    TRANSLATE_URI: str = "/translate/text"

    SENTRY_GLITCH: str = "http://ef23c6cc19484d09b9a5d536d1a75436@192.168.0.15:8001/5"
    SENTRY_ENVIRONMENT: str = "dev"

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
        'application/octet-stream',
    ]

    @property
    def TRANSALTOR_TRANSLATE_URL(cls):
        return f"{cls.TRANSLATOR_ADDRESS}{cls.TRANSLATE_URI}"
    
    @property
    def ARTIFACTS_PATH(cls):
        return Path(__file__).parent.parent.joinpath("ml")
    
settings = Settings()
