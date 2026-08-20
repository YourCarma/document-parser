"""Семафор, который знает свою загрузку.

Насыщение семафоров — первое, что нужно видеть при жалобе «сервис тормозит»:
и парсинг, и перевод ограничены именно ими, и очередь ожидающих корутин
растёт задолго до того, как вырастет время задачи.
"""

import asyncio

from modules.metrics.registry import register_semaphore


class TrackedSemaphore(asyncio.Semaphore):
    """`asyncio.Semaphore` с учётом занятых слотов и ожидающих корутин.

    Поведение базового класса не меняется: считаем только вход и выход, чтобы
    подмена была безопасной везде, где семафор уже используется.
    """

    def __init__(self, value: int, name: str) -> None:
        super().__init__(value)
        self.name = name
        self.capacity = value
        self.in_use = 0
        self.waiting = 0
        register_semaphore(self)

    async def acquire(self) -> bool:
        self.waiting += 1
        try:
            acquired = await super().acquire()
        finally:
            self.waiting -= 1
        self.in_use += 1
        return acquired

    def release(self) -> None:
        super().release()
        # max: у голого `release()` без парного `acquire()` (так делают, чтобы
        # вернуть слот заранее) счётчик не имеет права уйти в минус.
        self.in_use = max(0, self.in_use - 1)
