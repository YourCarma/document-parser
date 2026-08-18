"""Реестр обработчиков: task_type -> класс `TaskHandlerABC`."""

from loguru import logger

from modules.broker.abc.abc import TaskHandlerABC
from modules.broker.exceptions import UnknownTaskType
from modules.broker.keys import normalize_service_name


def _action_of(task_type: str) -> str:
    """Действие — часть после последней точки, нормализованная."""
    return normalize_service_name(task_type.rsplit(".", 1)[-1])


class TaskDispatcher:
    """Реестр task_type -> класс обработчика."""

    def __init__(self) -> None:
        self._handlers: dict[str, type[TaskHandlerABC]] = {}

    def register(self, handler_cls: type[TaskHandlerABC]) -> None:
        """Зарегистрировать обработчик по его `task_type`."""
        key = normalize_service_name(handler_cls.task_type)
        if key in self._handlers:
            raise ValueError(
                f"Обработчик для task_type '{handler_cls.task_type}' уже зарегистрирован"
            )
        self._handlers[key] = handler_cls

    def resolve(self, task_type: str) -> type[TaskHandlerABC]:
        """Найти обработчик, терпя расхождение написания имени сервиса."""
        normalized = normalize_service_name(str(task_type or ""))
        handler = self._handlers.get(normalized)
        if handler is not None:
            return handler

        # Написание имени сервиса у гейтвея не подтверждено, поэтому
        # сопоставляем по действию — оно у нас и у продюсера общее.
        action = _action_of(normalized)
        matches = [
            (registered, cls)
            for registered, cls in self._handlers.items()
            if _action_of(registered) == action
        ]
        if len(matches) == 1:
            registered, cls = matches[0]
            logger.warning(
                "Broker: task_type '{}' не совпал точно, сопоставлен по действию "
                "с '{}' — вероятно, расходится написание имени сервиса",
                task_type,
                registered,
            )
            return cls

        raise UnknownTaskType(str(task_type))

    @property
    def task_types(self) -> tuple[str, ...]:
        return tuple(
            handler_cls.task_type for handler_cls in self._handlers.values()
        )


def build_default_dispatcher() -> TaskDispatcher:
    """Реестр по умолчанию."""
    # Импорт внутри функции: хендлер тянет весь конвейер перевода, а
    # диспетчер сам по себе должен оставаться лёгким.
    from modules.broker.handlers.translate import TranslateHandler

    dispatcher = TaskDispatcher()
    dispatcher.register(TranslateHandler)
    return dispatcher
