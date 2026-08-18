"""Абстракции транспорта задач: консюмер и обработчик одного task_type."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

from pydantic import BaseModel

from modules.broker.schemas import TaskEnvelope
from modules.webhook_manager.schemas import TaskStatus

if TYPE_CHECKING:  # pragma: no cover
    from runtime import AppRuntime


class BrokerConsumerABC(ABC):
    """Транспорт задач. Ничего AMQP-специфичного здесь быть не должно."""

    @abstractmethod
    async def connect(self) -> None:
        """Установить соединение и подготовить топологию. Идемпотентно.

        Ошибка -> исключение наружу: под должен упасть, а не молчать.
        """

    @abstractmethod
    async def start(self) -> None:
        """Начать потребление. Требует выполненного `connect()`."""

    @abstractmethod
    async def stop(self) -> None:
        """Graceful: перестать брать новые, дождаться активных, закрыть
        соединение. Идемпотентно, исключений не бросает."""

    @abstractmethod
    async def health(self) -> bool:
        """True — потребление идёт и соединение живо."""


@dataclass(frozen=True, slots=True)
class HandlerOutcome:
    """Успешный итог обработки. Только терминальные статусы."""

    status: TaskStatus
    detail: str = ""


class TaskHandlerABC(ABC):
    """Обработчик одного task_type. Экземпляр — на одно сообщение."""

    task_type: ClassVar[str]
    payload_model: ClassVar[type[BaseModel]]

    def __init__(self, runtime: "AppRuntime") -> None:
        self.runtime = runtime

    @abstractmethod
    async def handle(self, envelope: TaskEnvelope, task_key: str) -> HandlerOutcome:
        """Выполнить задачу.

        Успех/отмена -> `HandlerOutcome`. Любая проблема -> типизированное
        исключение: решение по очереди принимает консюмер, не хендлер.
        """
