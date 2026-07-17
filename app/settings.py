from typing import List
from pathlib import Path
import os
import multiprocessing

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import AliasChoices, Field


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
            env_file=os.getenv('ENV_FILE', Path(__file__).parent.parent.joinpath(".env.dev").__str__()))
    
    SERVICE_NAME: str
    PRODUCTION_MODE: bool = False
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
    TRANSALTOR_MAX_CONCURRENCY: int = Field(
        default=15,
        validation_alias=AliasChoices(
            "TRANSLATOR_MAX_CONCURRENCY",
            "TRANSALTOR_MAX_CONCURRENCY",
        ),
    )

    WEBHOOK_MANAGER_URL: str = "http://localhost:8010"
    WATCHTOWER_URL: str = "http://localhost:8020"
    WATCHTOWER_SHARED_HOST: str = "http://localhost:8020"
    WATCHTOWER_SHARED_PREFIX: str = ""
    RESOURCE_MANAGER_URL: str = "http://localhost:8030"

    POST_REQUEST_TIMEOUT: int = 100
    EXTERNAL_CONNECT_TIMEOUT_SECS: float = 10.0
    EXTERNAL_READ_TIMEOUT_SECS: float = 100.0
    EXTERNAL_HTTP_CONNECTION_LIMIT: int = 100

    CORS_ORIGINS: List[str] = ["*"]
    CORS_ALLOW_CREDENTIALS: bool = True
    
    
    ALLOWED_MIME_TYPES: List[str] = [
        "text/plain",
        "image/jpeg",
        "image/webp",
        "application/rtf",
        "image/png",
        "text/rtf",
        "text/html",
        "image/tiff",
        "message/rfc822",
        "application/msword",
        "application/epub+zip",
        "application/xml",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.oasis.opendocument.text",
        "application/pdf",
        "application/octet-stream",
        "application/xbrl+xml",
        "text/markdown",
        "text/x-markdown",
        "text/xml",
        "application/vnd.oasis.opendocument.presentation",
        "application/vnd.oasis.opendocument.spreadsheet"
    ]

    @property
    def TRANSLATOR_TRANSLATE_URL(cls):
        return f"{cls.TRANSLATOR_ADDRESS}{cls.TRANSLATE_URI}"

    @property
    def TRANSLATOR_MAX_CONCURRENCY(self) -> int:
        """Correctly-spelled alias; the legacy env name remains supported."""
        return self.TRANSALTOR_MAX_CONCURRENCY
    
    @property
    def ARTIFACTS_PATH(cls):
        return Path(__file__).parent.parent.joinpath("ml")
    
settings = Settings()
