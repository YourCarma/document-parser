from typing import Optional
import json

from fastapi import HTTPException, Response
import aiohttp
from loguru import logger
import asyncio


async def post_request(url: str, payload: dict) -> Response:
    async with aiohttp.ClientSession() as session:
            try:
                async with session.post(url, json=payload, timeout=10000000 ) as resp:
                    response_body = await resp.read()
                    
                    if resp.status >= 400:
                        raise HTTPException(
                            status_code=resp.status,
                            detail=response_body.decode("utf-8") if response_body else None
                        )
                    
                    elif resp.status == 500:
                        logger.warning("Internal server error")
                        return ""
                    return await resp.json()
                        
            except aiohttp.ClientError as e:
                raise HTTPException(status_code=502, detail=f"Upstream error: {str(e)}")
            
def retry(times, exceptions):
    """
    Retry Decorator
    Retries the wrapped function/method `times` times if the exceptions listed
    in ``exceptions`` are thrown
    :param times: The number of times to repeat the wrapped function/method
    :type times: Int
    :param Exceptions: Lists of exceptions that trigger a retry attempt
    :type Exceptions: Tuple of Exceptions
    """
    def decorator(func):
        def newfn(*args, **kwargs):
            attempt = 0
            while attempt < times:
                try:
                    return func(*args, **kwargs)
                except exceptions:
                    logger.warning(
                        'Exception thrown when attempting to run %s, attempt '
                        '%d of %d' % (func, attempt, times)
                    )
                    attempt += 1
            return func(*args, **kwargs)
        return newfn
    return decorator