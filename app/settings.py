from typing import List
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit
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

    # --- Отмена и таймауты ---
    TASK_CANCEL_CHECK_TTL_SECS: float = 5.0
    PARSE_TIMEOUT_SECS: int = 900
    SOFFICE_TIMEOUT_SECS: int = 180
    TASK_TIMEOUT_SECS: int = 3000        # заведомо ниже consumer_timeout брокера

    # --- Скачивание исходников ---
    MAX_DOWNLOAD_FILE_SIZE_MB: int = 200

    # --- Брокер ---
    BROKER_ENABLED: bool = False
    BROKER_TYPE: str = "rabbitmq"

    # Либо целый URL, либо части (для k8s-секретов). URL приоритетнее.
    RMQ_URL: str = ""
    RMQ_HOST: str = "localhost"
    RMQ_PORT: int = 5672
    RMQ_USER: str = "guest"
    RMQ_PASSWORD: str = "guest"
    RMQ_VHOST: str = "/"

    # Exchange, очередь и биндинг между ними создаёт task_gateway. Мы их не
    # объявляем, только проверяем существование.
    RMQ_EXCHANGE: str = "document-parser.tasks"
    # Подтверждено на dev-брокере: exchange объявлен как direct, не topic.
    # Используется только при passive-проверке и в логах.
    RMQ_EXCHANGE_TYPE: str = "direct"
    RMQ_QUEUE: str = "document-parser.queue"
    # Только для диагностики: биндинг создаёт продюсер, мы его не трогаем.
    RMQ_ROUTING_KEYS: List[str] = ["document-parser.translate"]
    RMQ_PREFETCH_COUNT: int = 3
    RMQ_DLX: str = "document-parser.dlx"
    RMQ_DLQ: str = "document-parser.dlq"
    RMQ_RETRY_QUEUE: str = "document-parser.retry"
    RMQ_RETRY_DELAY_SECS: int = 30
    RMQ_MAX_RETRIES: int = 3
    RMQ_RECONNECT_INTERVAL_SECS: int = 5
    RMQ_CONNECT_TIMEOUT_SECS: int = 15
    RMQ_CONSUMER_TAG: str = "document-parser"
    # Объявлять ли НАШИ служебные объекты (DLX, DLQ, retry-очередь). Чужих
    # exchange и очереди этот флаг не касается — их не объявляем никогда.
    # false имеет смысл только там, где у пользователя нет прав на configure.
    RMQ_DECLARE_TOPOLOGY: bool = True
    # consumer_timeout брокера. Задан явно в rabbitmq.conf нашего RMQ
    # (consumer_timeout = 3600000). Используется только для валидации и логов,
    # не для логики: если значение на брокере изменят, поправить и здесь.
    RMQ_ACK_DEADLINE_SECS: int = 3600
    RMQ_ACK_SAFETY_MARGIN_SECS: int = 120
    RMQ_SHUTDOWN_GRACE_SECS: int = 60

    # Написание имени сервиса у гейтвея не подтверждено: по умолчанию берём
    # сегмент из task_type ("document-parser.translate" -> "document-parser").
    TASK_KEY_SERVICE_FROM_TASK_TYPE: bool = True

    # Куда кладём результат перевода из очереди. {task_id} подставляется.
    TRANSLATE_OUTPUT_PREFIX: str = "translated/{task_id}"

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
    def MAX_DOWNLOAD_FILE_SIZE_BYTES(self) -> int:
        return self.MAX_DOWNLOAD_FILE_SIZE_MB * 1024 * 1024

    @property
    def RMQ_CONNECTION_URL(self) -> str:
        """URL целиком либо собранный из частей. Пароль URL-экранируется."""
        if self.RMQ_URL:
            return self.RMQ_URL
        vhost = quote(self.RMQ_VHOST.lstrip("/"), safe="")
        return (
            f"amqp://{quote(self.RMQ_USER, safe='')}:"
            f"{quote(self.RMQ_PASSWORD, safe='')}@"
            f"{self.RMQ_HOST}:{self.RMQ_PORT}/{vhost}"
        )

    @property
    def RMQ_SAFE_URL(self) -> str:
        """URL без пароля — единственный вариант, допустимый в логах."""
        try:
            parts = urlsplit(self.RMQ_CONNECTION_URL)
            if parts.password is None:
                return self.RMQ_CONNECTION_URL
            netloc = f"{parts.username or ''}:***@{parts.hostname or ''}"
            if parts.port:
                netloc = f"{netloc}:{parts.port}"
            return urlunsplit(
                (parts.scheme, netloc, parts.path, parts.query, parts.fragment)
            )
        except Exception:
            # В логах лучше заглушка, чем риск утечки пароля.
            return "amqp://***"

    @property
    def ARTIFACTS_PATH(cls):
        return Path(__file__).parent.parent.joinpath("ml")
    
settings = Settings()
