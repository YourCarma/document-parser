import uvicorn
from fastapi import FastAPI
import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.starlette import StarletteIntegration
from sentry_sdk.integrations.logging import LoggingIntegration

from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from concurrent.futures.process import ProcessPoolExecutor
from contextlib import asynccontextmanager
from loguru import logger

from settings import settings
from api.routers import routers

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.warning("Starting service...")
    app.state.executor = ProcessPoolExecutor(max_workers=2)
    # sentry_sdk.init(
    #     dsn=settings.SENTRY_GLITCH,
    #     integrations=[
    #         FastApiIntegration(),
    #         StarletteIntegration(),
    #         LoggingIntegration(level=None, event_level=None),
    #     ],
    #     environment=settings.SENTRY_ENVIRONMENT,
    #     traces_sample_rate=1.0
    # )
    yield
    logger.warning("Closing service...")
    app.state.executor.shutdown()

app = FastAPI(title="Document Parser",
              lifespan=lifespan,
              description=""" """,
              version="0.3.1-without-wh")



origins = ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# @app.middleware("http")
# async def capture_exceptions_middleware(request: Request, call_next):
#     try:
#         response = await call_next(request)
#         return response
#     except Exception as exc:
#         with sentry_sdk.push_scope() as scope:
#             scope.set_context("request", {
#                 "url": str(request.url),
#                 "method": request.method,
#                 "headers": dict(request.headers),
#                 "query_params": dict(request.query_params),
#             })
#             sentry_sdk.capture_exception(exc)
#         raise exc
    
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
        reload=True,
    )
