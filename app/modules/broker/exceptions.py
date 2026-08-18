"""Типизированные ошибки транспорта задач.

Все — обычные `Exception` с непустым `args`: любое исключение отсюда может
пересечь границу процесса вместе с задачей и обязано пережить pickle.
"""

from modules.resource_manager.exceptions import BucketNotFound


class BrokerError(Exception):
    """Базовая ошибка транспорта задач."""


class InvalidEnvelope(BrokerError):
    """Конверт не разобран: не JSON, не объект, нет task_id/user_id/task_type.

    Ключ задачи собрать нельзя, значит сообщить пользователю об ошибке некуда.
    """


class UnknownTaskType(BrokerError):
    """В сообщении task_type, для которого нет зарегистрированного обработчика."""

    def __init__(self, task_type: str):
        self.task_type = task_type
        super().__init__(f"Неизвестный task_type: '{task_type}'")


class InvalidTaskPayload(BrokerError):
    """payload не прошёл валидацию схемы конкретного task_type."""


class UnsupportedSourceFormat(BrokerError):
    """Расширение исходного файла не поддерживается ни одним парсером."""

    def __init__(self, file_path: str):
        self.file_path = file_path
        super().__init__(f"Расширение файла '{file_path}' не поддерживается")


class TaskPipelineFailed(BrokerError):
    """Конвейер вернул ERROR.

    Публичный статус конвейер уже опубликовал сам — решение по очереди
    принимаем по `cause`, а свой текст поверх чужого не пишем.
    """

    def __init__(self, cause: BaseException | None = None, stage: str = ""):
        self.cause = cause
        self.stage = stage
        super().__init__(
            f"Конвейер завершился ошибкой на этапе '{stage}': {cause}"
        )


class BrokerNotConnected(BrokerError):
    """Операция требует установленного соединения с брокером."""


class UnsupportedBrokerType(BrokerError):
    """В BROKER_TYPE указан тип, для которого нет реализации."""


__all__ = [
    "BrokerError",
    "BucketNotFound",
    "InvalidEnvelope",
    "UnknownTaskType",
    "InvalidTaskPayload",
    "UnsupportedSourceFormat",
    "TaskPipelineFailed",
    "BrokerNotConnected",
    "UnsupportedBrokerType",
]
