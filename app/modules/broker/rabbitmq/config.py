"""Разбор env-настроек RabbitMQ в неизменяемый конфиг."""

from dataclasses import dataclass

from settings import settings


@dataclass(frozen=True, slots=True)
class RabbitMQConfig:
    url: str
    safe_url: str
    exchange: str
    # Только для диагностики: exchange объявляет продюсер, мы его тип не
    # применяем — но расхождение с ожидаемым должно быть видно в логе.
    exchange_type: str
    queue: str
    routing_keys: tuple[str, ...]
    prefetch_count: int
    dlx: str
    dlq: str
    retry_queue: str
    retry_delay_secs: int
    max_retries: int
    reconnect_interval_secs: int
    connect_timeout_secs: int
    consumer_tag: str
    declare_topology: bool
    ack_deadline_secs: int
    dlq_check_interval_secs: int
    shutdown_grace_secs: int

    @classmethod
    def from_settings(cls) -> "RabbitMQConfig":
        return cls(
            url=settings.RMQ_CONNECTION_URL,
            safe_url=settings.RMQ_SAFE_URL,
            exchange=settings.RMQ_EXCHANGE,
            exchange_type=settings.RMQ_EXCHANGE_TYPE,
            queue=settings.RMQ_QUEUE,
            routing_keys=tuple(settings.RMQ_ROUTING_KEYS),
            prefetch_count=max(1, settings.RMQ_PREFETCH_COUNT),
            dlx=settings.RMQ_DLX,
            dlq=settings.RMQ_DLQ,
            retry_queue=settings.RMQ_RETRY_QUEUE,
            retry_delay_secs=settings.RMQ_RETRY_DELAY_SECS,
            max_retries=max(0, settings.RMQ_MAX_RETRIES),
            reconnect_interval_secs=settings.RMQ_RECONNECT_INTERVAL_SECS,
            connect_timeout_secs=settings.RMQ_CONNECT_TIMEOUT_SECS,
            consumer_tag=settings.RMQ_CONSUMER_TAG,
            declare_topology=settings.RMQ_DECLARE_TOPOLOGY,
            ack_deadline_secs=settings.RMQ_ACK_DEADLINE_SECS,
            dlq_check_interval_secs=max(0, settings.RMQ_DLQ_CHECK_INTERVAL_SECS),
            shutdown_grace_secs=settings.RMQ_SHUTDOWN_GRACE_SECS,
        )

    def validate(self) -> list[str]:
        """Вернуть список предупреждений о конфигурации. Не бросает."""
        warnings: list[str] = []

        margin = settings.RMQ_ACK_SAFETY_MARGIN_SECS
        if settings.TASK_TIMEOUT_SECS + margin > self.ack_deadline_secs:
            warnings.append(
                f"TASK_TIMEOUT_SECS={settings.TASK_TIMEOUT_SECS} слишком близок к "
                f"предполагаемому consumer_timeout={self.ack_deadline_secs} — при "
                "длинном документе брокер разорвёт канал и задача пойдёт по кругу"
            )

        if settings.PARSER_WORKERS < self.prefetch_count:
            warnings.append(
                f"PARSER_WORKERS={settings.PARSER_WORKERS} меньше "
                f"prefetch={self.prefetch_count}, параллелизм только на бумаге"
            )

        return warnings
