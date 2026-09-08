import asyncio
from concurrent.futures.process import ProcessPoolExecutor

from loguru import logger

from modules.parser.v1.exceptions import ProcessPoolUnavailable


class ProcessPoolHolder:
    """Владелец `ProcessPoolExecutor`, переживающий гибель воркера (P0-1).

    Если воркер умирает (OOM, SIGKILL), пул становится `BrokenProcessPool`
    навсегда: без пересборки все последующие задачи парсинга падают до
    рестарта сервиса. Холдер позволяет заменить сломанный пул на живой.
    """

    def __init__(self, max_workers: int) -> None:
        self._max_workers = max_workers
        self._executor = ProcessPoolExecutor(max_workers=max_workers)
        self._lock = asyncio.Lock()
        self._closed = False
        self._generation = 0

    @property
    def generation(self) -> int:
        """Номер поколения пула: растёт на каждой пересборке."""
        return self._generation

    async def get(self) -> ProcessPoolExecutor:
        """Вернуть текущий живой пул."""
        async with self._lock:
            if self._closed:
                raise ProcessPoolUnavailable()
            return self._executor

    async def rebuild(
        self,
        broken: ProcessPoolExecutor | None = None,
    ) -> ProcessPoolExecutor:
        """Заменить сломанный пул новым.

        Идемпотентен: если `broken` уже не текущий пул, значит соседняя задача
        успела пересобрать его — просто возвращаем актуальный.
        """
        async with self._lock:
            if self._closed:
                raise ProcessPoolUnavailable()
            if broken is not None and broken is not self._executor:
                return self._executor

            old = self._executor
            try:
                # wait=False: у сломанного пула воркеры уже мертвы, ждать нечего.
                await asyncio.to_thread(old.shutdown, wait=False, cancel_futures=True)
            except Exception as exc:
                logger.warning(
                    "ProcessPoolHolder: failed to shut down the broken pool: {}",
                    exc,
                )

            self._executor = ProcessPoolExecutor(max_workers=self._max_workers)
            self._generation += 1
            logger.error(
                "ProcessPoolHolder: process pool rebuilt generation={} max_workers={}",
                self._generation,
                self._max_workers,
            )
            return self._executor

    async def shutdown(self, wait: bool = True, cancel_futures: bool = True) -> None:
        """Погасить пул и запретить дальнейшую выдачу. Идемпотентно."""
        async with self._lock:
            if self._closed:
                return
            self._closed = True
            executor = self._executor

        await asyncio.to_thread(
            executor.shutdown,
            wait=wait,
            cancel_futures=cancel_futures,
        )
        logger.info("ProcessPoolHolder: process pool stopped")
