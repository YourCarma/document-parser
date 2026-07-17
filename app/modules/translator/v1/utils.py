from collections.abc import Awaitable, Callable
from functools import wraps
from typing import Any, ParamSpec, TypeVar

from fastapi import HTTPException
import aiohttp
import asyncio
from loguru import logger
from settings import settings


P = ParamSpec("P")
R = TypeVar("R")


class RetryableUpstreamError(HTTPException):
    """An upstream failure that is safe to retry."""


async def _post_with_session(
    session: aiohttp.ClientSession,
    url: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    try:
        async with session.post(
            url,
            json=payload,
            timeout=settings.POST_REQUEST_TIMEOUT,
        ) as resp:
            response_body = await resp.read()
            if resp.status >= 400:
                logger.warning(
                    "Translator upstream returned status={} response_bytes={}",
                    resp.status,
                    len(response_body),
                )
                error_type = (
                    RetryableUpstreamError if resp.status >= 500 else HTTPException
                )
                raise error_type(
                    status_code=resp.status,
                    detail=f"Сервис перевода вернул ошибку ({resp.status})",
                )
            return await resp.json()
    except (asyncio.TimeoutError, aiohttp.ClientConnectionError) as exc:
        raise RetryableUpstreamError(
            status_code=502,
            detail="Сервис перевода временно недоступен",
        ) from exc
    except aiohttp.ClientError as exc:
        raise HTTPException(
            status_code=502,
            detail="Ошибка взаимодействия с сервисом перевода",
        ) from exc


async def post_request(
    url: str,
    payload: dict[str, Any],
    session: aiohttp.ClientSession | None = None,
) -> dict[str, Any]:
    if session is not None:
        return await _post_with_session(session, url, payload)
    async with aiohttp.ClientSession() as owned_session:
        return await _post_with_session(owned_session, url, payload)
            
def retry(
    times: int,
    exceptions: type[BaseException] | tuple[type[BaseException], ...],
) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R]]]:
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
        @wraps(func)
        async def wrapper(*args, **kwargs):
            for attempt in range(times):
                try:
                    return await func(*args, **kwargs)
                except exceptions:
                    if attempt == times - 1:
                        raise
                    await asyncio.sleep(2 ** attempt)
        return wrapper
    return decorator
