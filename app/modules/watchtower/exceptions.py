class WatchtowerError(Exception):
    """Базовая ошибка работы с хранилищем watchtower."""


class FileNotFoundInStorage(WatchtowerError):
    """Файла нет в бакете (404). Постоянная ошибка, повтор бесполезен."""


class FileTooLargeError(WatchtowerError):
    """Размер файла превышает лимит. Постоянная ошибка."""


class WatchtowerUnavailable(WatchtowerError):
    """Хранилище недоступно (5xx или сетевой сбой). Ошибка временная."""
