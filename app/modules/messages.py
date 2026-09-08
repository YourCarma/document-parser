"""Пользовательские тексты ошибок задач.

Одни и те же строки публикуются в webhook_manager и HTTP-конвейером, и
консюмером, поэтому живут отдельно от обоих транспортов.
"""

MSG_UNKNOWN_TASK_TYPE: str = "Неизвестный тип задачи"
MSG_INVALID_PAYLOAD: str = "Неверный формат данных задачи"
MSG_UNSUPPORTED_FORMAT: str = "Формат файла не поддерживается"
MSG_FILE_NOT_FOUND: str = "Файл не найден в хранилище"
MSG_NO_BUCKET: str = "Не найдено хранилище пользователя"
MSG_FILE_TOO_LARGE: str = "Файл слишком большой"
MSG_BROKEN_DOCUMENT: str = "Не удалось обработать документ"
MSG_TIMEOUT: str = "Превышено время обработки"
MSG_UPSTREAM_UNAVAILABLE: str = "Сервис временно недоступен, попробуйте позже"
MSG_LANGUAGE_UNDETECTED: str = "Не удалось определить язык документа"
MSG_CANCELLED: str = "Задача отменена"
MSG_RETRY_SCHEDULED: str = "Временный сбой, повторная попытка {attempt}/{total}"
