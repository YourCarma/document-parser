"""Фабрика консюмеров: BROKER_TYPE -> реализация."""

from typing import TYPE_CHECKING

from modules.broker.abc.abc import BrokerConsumerABC
from modules.broker.exceptions import UnsupportedBrokerType
from modules.broker.dispatcher import TaskDispatcher
from settings import settings

if TYPE_CHECKING:  # pragma: no cover
    from runtime import AppRuntime


class BrokerFactory:
    """BROKER_TYPE -> реализация консюмера."""

    @staticmethod
    def create(
        runtime: "AppRuntime",
        dispatcher: TaskDispatcher,
        broker_type: str | None = None,
    ) -> BrokerConsumerABC:
        """Собрать консюмер по типу брокера."""
        resolved = (broker_type or settings.BROKER_TYPE or "").strip().lower()
        if resolved == "rabbitmq":
            # Импорт внутри метода: без RabbitMQ-режима aio_pika
            # импортировать незачем.
            from modules.broker.rabbitmq.consumer import RabbitMQConsumer

            return RabbitMQConsumer(runtime, dispatcher)
        raise UnsupportedBrokerType(f"Неизвестный BROKER_TYPE: '{broker_type or settings.BROKER_TYPE}'")
