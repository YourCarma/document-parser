"""Реестр объектов, которые опрашиваются наблюдаемыми метриками.

Наблюдаемые метрики (`create_observable_gauge`) вызываются экспортёром раз в
интервал и должны где-то взять текущее состояние. Держим здесь слабые ссылки:
реестр не обязан продлевать жизнь семафорам и пулу процессов.
"""

import weakref
from typing import Any

from modules.metrics.instruments import observation

_semaphores: "weakref.WeakSet[Any]" = weakref.WeakSet()
_process_pools: "weakref.WeakSet[Any]" = weakref.WeakSet()


def register_semaphore(semaphore: Any) -> None:
    """Добавить семафор в опрос. Идемпотентно."""
    _semaphores.add(semaphore)


def register_process_pool(pool: Any) -> None:
    """Добавить холдер пула процессов в опрос. Идемпотентно."""
    _process_pools.add(pool)


def clear() -> None:
    """Забыть всё. Нужно тестам и повторной инициализации."""
    _semaphores.clear()
    _process_pools.clear()


def observe_semaphores(field: str) -> list:
    """Значения `field` по всем зарегистрированным семафорам."""
    results = []
    for semaphore in list(_semaphores):
        value = getattr(semaphore, field, None)
        if value is None:
            continue
        point = observation(value, {"semaphore": getattr(semaphore, "name", "unknown")})
        if point is not None:
            results.append(point)
    return results


def observe_process_pools() -> list:
    """Поколение каждого зарегистрированного пула процессов."""
    results = []
    for pool in list(_process_pools):
        generation = getattr(pool, "generation", None)
        if generation is None:
            continue
        point = observation(generation, {"pool": "parser"})
        if point is not None:
            results.append(point)
    return results
