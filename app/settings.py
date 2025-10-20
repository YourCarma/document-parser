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
settings = Settings()
