from typing import List
from pathlib import Path
from dotenv import load_dotenv

from pydantic_settings import BaseSettings, SettingsConfigDict

env_file_path = Path(__file__).parent.parent.joinpath(
    ".env.document-parser").__str__()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=env_file_path)

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


load_dotenv(env_file_path, override=True)
settings = Settings()
