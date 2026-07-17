import uvicorn
import asyncio
import aiohttp
from fastapi import FastAPI

from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from concurrent.futures.process import ProcessPoolExecutor
from contextlib import asynccontextmanager
from loguru import logger

from settings import settings
from api.routers import routers


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize shared resources for request handling and background tasks."""
    GREETINGS = r"""
     __                             __
 ___/ /__  ______ ____ _  ___ ___  / /________  ___ ________ ___ ____
/ _  / _ \/ __/ // /  ' \/ -_) _ \/ __/___/ _ \/ _ `/ __(_-</ -_) __/
\_,_/\___/\__/\_,_/_/_/_/\__/_//_/\__/   / .__/\_,_/_/ /___/\__/_/
                                        /_/
    """
    logger.info(GREETINGS)
    app.state.executor = ProcessPoolExecutor(max_workers=settings.PARSER_WORKERS)
    app.state.parser_semaphore = asyncio.Semaphore(settings.PARSER_WORKERS)
    app.state.translation_semaphore = asyncio.Semaphore(
        settings.TRANSLATOR_MAX_CONCURRENCY,
    )
    timeout = aiohttp.ClientTimeout(
        total=None,
        connect=settings.EXTERNAL_CONNECT_TIMEOUT_SECS,
        sock_read=settings.EXTERNAL_READ_TIMEOUT_SECS,
    )
    connector = aiohttp.TCPConnector(
        limit=settings.EXTERNAL_HTTP_CONNECTION_LIMIT,
    )
    app.state.http_session = aiohttp.ClientSession(
        timeout=timeout,
        connector=connector,
    )
    try:
        yield
    finally:
        logger.info("Остановка сервиса document-parser")
        await app.state.http_session.close()
        await asyncio.to_thread(
            app.state.executor.shutdown,
            wait=True,
            cancel_futures=True,
        )

app = FastAPI(
    title="Document Parser",
    lifespan=lifespan,
    version="0.11.0-wh",
    summary=(
        "Сервис для парсинга документов, OCR по изображениям и перевода "
        "документов в синхронном и асинхронном режимах."
    ),
    description="""
## Назначение

`document-parser` преобразует документы в Markdown и Word, а также запускает
перевод содержимого через внешние сервисы.

## Основные сценарии

- `Parser V1` — парсинг документа в текст, `.md` или `.docx`.
- `Translator V1` — синхронный перевод документа в рамках одного HTTP-запроса.
- `Translator V2` — асинхронный перевод с `task_id`, прогрессом и загрузкой
  исходного и итогового файла в облачное хранилище.

## Внешние зависимости

- VLM для OCR и full-VLM-парсинга PDF.
- Сервис перевода текста.
- Сервис определения языка.
- `webhook_manager`, `watchtower`, `resource_manager` для async-сценария.
""",
    openapi_tags=[
        {
            "name": "System",
            "description": "Служебные методы для проверки доступности сервиса.",
        },
        {
            "name": "Parser V1",
            "description": (
                "Синхронный парсинг документов в Markdown-текст, `.md` и `.docx`."
            ),
        },
        {
            "name": "Translator V1",
            "description": (
                "Синхронный перевод документов. Результат возвращается в ответе "
                "на тот же запрос."
            ),
        },
        {
            "name": "Translator V2",
            "description": (
                "Асинхронный перевод документов с прогрессом, `task_id` и "
                "загрузкой файлов в облачное хранилище."
            ),
        },
    ],
)



app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=settings.CORS_ALLOW_CREDENTIALS,
    allow_methods=["*"],
    allow_headers=["*"],
)
    
for router in routers:
    app.include_router(router)


@app.get('/', tags=['System'], response_class=HTMLResponse)
async def get_root():
    return """
        <a href="/docs">ДОКУМЕНТАЦИЯ</a>
    """

@app.get('/health', tags=['System'])
async def health_check():
    return {
        'status': "Ok",
    }

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=True if not settings.PRODUCTION_MODE else False,
        
    )
