from fastapi import HTTPException,status
from loguru import logger

class InternalServerError(HTTPException):
    def __init__(self, detail = None):
        super().__init__(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail=detail,
        )

class BadRequestError(HTTPException):
    def __init__(self,detail=None):
        super().__init__(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=detail,
        )

class ContentNotSupportedError(HTTPException):
    def __init__(self,detail:str):
        logger.error("Current MIME type not supported!")
        super().__init__(
            status_code=status.HTTP_406_NOT_ACCEPTABLE, 
            detail=detail,
        )
        
class ServiceUnavailable(HTTPException):
    def __init__(self, service_name: str, service_url: str):
        logger.error(f"Servie \"{service_name}\" at {service_url} unavaialble! Check the connection!")
        super().__init__(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, 
            detail=f"Сервис \"{service_name}\" недоступен. Обратитесь к администратору.",
        )

class TimeoutError(HTTPException):
    def __init__(self):
        logger.error(f"Timeout error!")
        super().__init__(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Время ожидания вышло",
        )


class ProcessPoolUnavailable(HTTPException):
    """Пул процессов парсинга сломан и не восстановился.

    Бросается только в родительском процессе, поэтому непиклюемость
    `HTTPException` здесь безопасна.
    """

    def __init__(self, detail: str = "Пул процессов парсинга недоступен, повторите попытку"):
        super().__init__(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=detail,
        )


class ConversionTimeoutError(Exception):
    """Таймаут LibreOffice.

    Бросается ВНУТРИ воркера `ProcessPoolExecutor`, поэтому это обычный
    `Exception` с непустым `args` — иначе исключение не переживёт pickle.
    """

    def __init__(self, message: str):
        super().__init__(message)
