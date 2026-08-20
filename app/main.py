import uvicorn
from fastapi import FastAPI, Request, Response

from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from contextlib import asynccontextmanager
from loguru import logger

from modules.broker.abc.factory import BrokerFactory
from modules.broker.dispatcher import build_default_dispatcher
from modules.metrics import (
    HTTPMetricsMiddleware,
    setup_observability,
    shutdown_observability,
)
from runtime import AppRuntime
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
    logger.info("webhook_manager task key format: '{{user_id}}:{}:{{task_id}}'",
                settings.SERVICE_NAME)
    runtime = AppRuntime.create()
    runtime.attach(app)
    app.state.runtime = runtime
    app.state.broker = None
    try:
        if settings.BROKER_ENABLED:
            consumer = BrokerFactory.create(runtime, build_default_dispatcher())
            try:
                # Ошибка подключения пробрасывается осознанно: не консюмящий
                # воркер в проде хуже упавшего.
                await consumer.connect()
                await consumer.start()
            except Exception:
                # До app.state.broker дело не дошло, значит finally его не
                # погасит: закрываем соединение здесь, иначе оно повиснет.
                await consumer.stop()
                raise
            app.state.broker = consumer
        else:
            logger.info("Broker: disabled (BROKER_ENABLED=false)")
        yield
    finally:
        logger.info("Shutting down document-parser service")
        if app.state.broker is not None:
            # Сначала консюмер: он пользуется сессией и пулом из runtime.
            await app.state.broker.stop()
        await runtime.shutdown()
        # Последним: телеметрия должна пережить остальных, чтобы дослать
        # события их остановки.
        shutdown_observability()

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
- Асинхронный перевод — **только из очереди**: задачу ставит клиент через
  `task_gateway`, сервис забирает её из RabbitMQ, а прогресс и результат
  публикует в `webhook_manager`. Правила формирования задачи отдаёт сам
  сервис: `GET /api/v1/contract.md`.

## Внешние зависимости

- VLM для OCR и full-VLM-парсинга PDF.
- Сервис перевода текста.
- Сервис определения языка.
- `webhook_manager`, `watchtower`, `resource_manager` для задач из очереди.
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
    ],
)



app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=settings.CORS_ALLOW_CREDENTIALS,
    allow_methods=["*"],
    allow_headers=["*"],
)
    
app.add_middleware(HTTPMetricsMiddleware)

# До старта приложения: авто-инструментация FastAPI добавляет свою мидлварь, а
# после старта стек мидлварей уже собран и менять его нельзя.
setup_observability(app)

for router in routers:
    app.include_router(router)


@app.get('/', tags=['System'], response_class=HTMLResponse)
async def get_root():
    return """
        <a href="/docs">ДОКУМЕНТАЦИЯ</a>
    """

@app.get('/health', tags=['System'])
async def health_check(request: Request, response: Response):
    """Готовность сервиса, включая состояние консюмера.

    Отдаём 503, если консюмер включён, но не потребляет: под, который молча
    не разбирает очередь, для оркестратора должен выглядеть больным.
    """
    broker = getattr(request.app.state, "broker", None)
    if broker is None:
        return {"status": "Ok", "broker": "disabled"}

    try:
        report = await broker.health_report()
    except Exception as exc:
        logger.error("Health: failed to poll the consumer: {}", exc)
        response.status_code = 503
        return {"status": "Error", "broker": {"healthy": False, "error": str(exc)}}

    if not report.get("healthy"):
        response.status_code = 503
        return {"status": "Error", "broker": report}
    return {"status": "Ok", "broker": report}

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=True if not settings.PRODUCTION_MODE else False,
        
    )
