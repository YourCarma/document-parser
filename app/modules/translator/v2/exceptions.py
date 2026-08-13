class TaskTimeout(Exception):
    """Истёк лимит времени на этап задачи (`PARSE_TIMEOUT_SECS`).

    Общий таймаут задачи даёт встроенный `TimeoutError` из `asyncio.timeout`,
    отдельного класса под него не нужно.
    """
