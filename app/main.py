import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from concurrent.futures.process import ProcessPoolExecutor
from contextlib import asynccontextmanager
from loguru import logger

from settings import settings
from api.routers import routers

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.warning("Starting service...")
    app.state.executor = ProcessPoolExecutor(max_workers=2)
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

for router in routers:
    app.include_router(router)

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=True,
    )
