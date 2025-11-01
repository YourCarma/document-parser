from fastapi import HTTPException
from loguru import logger
from fastapi import status

class InvalidLanguageCode(HTTPException):
    def __init__(self, status=status.HTTP_406_NOT_ACCEPTABLE, detail = None, headers = None):
        logger.error("Invalid language code!")
        super().__init__(
            status_code=status, 
            detail=detail,
        )
