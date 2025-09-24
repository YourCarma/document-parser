from fastapi import HTTPException,status

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
        super().__init__(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, 
            detail=detail,
        )
    