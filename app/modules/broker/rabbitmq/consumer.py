"""Консюмер RabbitMQ на aio-pika.

Единственное место (вместе с `config.py`), где известно про AMQP.
"""

import asyncio
import time
from typing import TYPE_CHECKING, Any, Mapping

import aio_pika
from loguru import logger
from pamqp.commands import Basic

from modules.broker.abc.abc import BrokerConsumerABC
from modules.broker.dispatcher import TaskDispatcher
from modules.broker.errors import MessageAction, classify_error
from modules.broker.keys import build_task_key, service_segment_for
from modules.broker.rabbitmq.config import RabbitMQConfig
from modules.broker.reporting import report_task_error, report_task_retry
from modules.broker.schemas import TaskType, parse_envelope, payload_user_id_differs
from modules.messages import MSG_UPSTREAM_UNAVAILABLE

if TYPE_CHECKING:  # pragma: no cover
    from runtime import AppRuntime


_ATTEMPT_HEADER: str = "x-attempt"
_ORIGINAL_ROUTING_KEY_HEADER: str = "x-original-routing-key"
_ERROR_HEADER: str = "x-last-error"


def read_attempt(headers: Mapping[str, Any] | None) -> int:
    """Число уже сделанных попыток.

    Основной источник — свой заголовок `x-attempt`. Резервный — сумма `count`
    в `x-death` (его проставляет брокер при dead-letter). Берём максимум,
    чтобы счётчик не обнулился при ручной переливке сообщения.
    """
    if not headers:
        return 0

    own = 0
    raw_own = headers.get(_ATTEMPT_HEADER)
    if raw_own is not None:
        try:
            own = int(raw_own)
        except (TypeError, ValueError):
            own = 0

    from_death = 0
    raw_death = headers.get("x-death")
    if isinstance(raw_death, (list, tuple)):
        for entry in raw_death:
            if not isinstance(entry, Mapping):
                continue
            try:
                from_death += int(entry.get("count", 0))
            except (TypeError, ValueError):
                continue

    return max(0, own, from_death)


class RabbitMQConsumer(BrokerConsumerABC):
    """Потребление задач из RabbitMQ с retry-очередью и DLQ."""

    def __init__(
        self,
        runtime: "AppRuntime",
        dispatcher: TaskDispatcher,
        config: RabbitMQConfig | None = None,
    ) -> None:
        self._runtime = runtime
        self._dispatcher = dispatcher
        self._config = config or RabbitMQConfig.from_settings()
        self._connection = None
        self._channel = None
        self._publish_channel = None
        self._queue = None
        self._consumer_tag: str | None = None
        self._tasks: set[asyncio.Task] = set()
        self._stopping: bool = False
        self._started: bool = False
        # Будит задачи, ждущие паузу перед возвратом сообщения в очередь.
        self._stop_event: asyncio.Event = asyncio.Event()
        # Состояние топологии: чужие объекты могут оказаться не такими, как мы
        # их объявляем, и от этого зависит поведение retry и reject.
        self._dlq_ready: bool = False
        self._retry_queue_ready: bool = False
        self._callbacks_attached: bool = False
        # Сторож DLQ и последнее известное число сообщений в ней (-1 — не знаем).
        self._dlq_watch_task: asyncio.Task | None = None
        self._dlq_depth: int = -1
        # Отдельный канал под опрос DLQ: падение опроса не должно ронять
        # канал публикации копий в DLQ и retry.
        self._stats_channel = None

    # --- жизненный цикл -------------------------------------------------

    async def connect(self) -> None:
        cfg = self._config
        if self._connection is not None and not self._connection.is_closed:
            return

        if self._connection is not None:
            # Соединение закрыто, но объект остался: без явного close()
            # утекут его сокет и колбэки.
            try:
                await self._connection.close()
            except Exception as exc:
                logger.debug("Broker: closing dead connection: {}", exc)
            self._connection = None
            self._callbacks_attached = False

        # После stop() флаг остался бы взведённым, и повторно поднятый
        # консюмер молча возвращал бы в очередь всё, что получает.
        self._stopping = False
        self._stop_event.clear()

        for warning in cfg.validate():
            logger.error("Broker: {}", warning)

        try:
            self._connection = await aio_pika.connect_robust(
                cfg.url,
                timeout=cfg.connect_timeout_secs,
                reconnect_interval=cfg.reconnect_interval_secs,
                client_properties={"connection_name": cfg.consumer_tag},
            )
        except Exception as exc:
            logger.critical(
                "Broker: failed to connect to {}: {}", cfg.safe_url, exc
            )
            raise

        self._channel = await self._connection.channel()
        await self._channel.set_qos(prefetch_count=cfg.prefetch_count)
        # Отдельный канал для retry-публикаций: подтверждения публикации не
        # должны мешать потреблению на основном канале.
        self._publish_channel = await self._connection.channel(
            publisher_confirms=True
        )

        await self._declare_topology()

        if not self._callbacks_attached:
            # connect_robust переподключается сам, соединение то же самое —
            # без флага колбэки копились бы с каждым вызовом connect().
            self._connection.close_callbacks.add(self._on_connection_closed)
            self._connection.reconnect_callbacks.add(
                lambda *_: logger.info("Broker: connection to the broker restored")
            )
            self._callbacks_attached = True

        logger.info(
            "Broker: connected url='{}' exchange='{}' queue='{}' routing_keys={} "
            "prefetch={}",
            cfg.safe_url,
            cfg.exchange,
            cfg.queue,
            list(cfg.routing_keys),
            cfg.prefetch_count,
        )
        logger.info(
            "Broker: task key format '{{user_id}}:{}:{{task_id}}'",
            service_segment_for(TaskType.TRANSLATE.value),
        )

    async def _reopen_channel(self):
        """Пересоздать основной канал.

        Любая ошибка уровня канала (в том числе PRECONDITION_FAILED при
        объявлении) закрывает канал, поэтому продолжать на нём нельзя.
        """
        try:
            if self._channel is not None and not self._channel.is_closed:
                await self._channel.close()
        except Exception:
            pass
        self._channel = await self._connection.channel()
        await self._channel.set_qos(prefetch_count=self._config.prefetch_count)
        return self._channel

    async def _declare_own_objects(self) -> None:
        """Объявить объекты, которыми владеем только мы: DLX, DLQ и retry.

        Заранее их никто не создаёт. Расхождение с уже существующими не роняет
        сервис: работа без счётчика попыток лучше, чем CrashLoopBackOff, — но
        кричим в лог, потому что деградация обязана быть видимой.
        """
        cfg = self._config

        try:
            dlx = await self._channel.declare_exchange(
                cfg.dlx, aio_pika.ExchangeType.FANOUT, durable=True
            )
            dlq = await self._channel.declare_queue(cfg.dlq, durable=True)
            await dlq.bind(dlx)
            self._dlq_ready = True
        except Exception as exc:
            self._dlq_ready = False
            logger.critical(
                "Broker: failed to declare DLX/DLQ ('{}'/'{}'): {}. There will be "
                "nowhere to park permanently failing messages — they will stay "
                "unacknowledged and keep returning to the work queue",
                cfg.dlx,
                cfg.dlq,
                exc,
            )
            await self._reopen_channel()

        if not cfg.retry_queue:
            self._retry_queue_ready = False
            return

        try:
            await self._channel.declare_queue(
                cfg.retry_queue,
                durable=True,
                arguments={
                    "x-message-ttl": cfg.retry_delay_secs * 1000,
                    # Возврат строго в рабочую очередь через default exchange.
                    # Через основной exchange сообщение вернуться не может:
                    # на direct-биндинге ключ retry-очереди ни с чем не
                    # совпадёт, и брокер молча уничтожит сообщение.
                    "x-dead-letter-exchange": "",
                    "x-dead-letter-routing-key": cfg.queue,
                },
            )
            self._retry_queue_ready = True
        except Exception as exc:
            self._retry_queue_ready = False
            logger.critical(
                "Broker: failed to declare the retry queue '{}': {}. It was probably "
                "created with different arguments — delete it and restart the "
                "service. Until then retries go through nack(requeue) with no "
                "attempt counter",
                cfg.retry_queue,
                exc,
            )
            await self._reopen_channel()

    async def _check_foreign_topology(self):
        """Убедиться, что чужие объекты на месте.

        Exchange, рабочая очередь и биндинг между ними принадлежат гейтвею:
        он создаёт их заранее. Мы только проверяем существование через
        passive-объявление — чтобы отсутствие дало внятный текст, а не
        NOT_FOUND из недр брокера при подписке.
        """
        cfg = self._config

        try:
            await self._channel.declare_exchange(cfg.exchange, passive=True)
        except Exception as exc:
            logger.critical(
                "Broker: exchange '{}' not found ({}). It is created by task_gateway — "
                "check RMQ_EXCHANGE or the deployment order",
                cfg.exchange,
                exc,
            )
            raise

        try:
            return await self._channel.declare_queue(cfg.queue, passive=True)
        except Exception as exc:
            logger.critical(
                "Broker: queue '{}' not found ({}). It must be created by "
                "task_gateway — check RMQ_QUEUE or the deployment order",
                cfg.queue,
                exc,
            )
            raise

    async def _declare_topology(self) -> None:
        """Подготовить топологию: свои объекты создаём, чужие — проверяем."""
        cfg = self._config

        if cfg.declare_topology:
            await self._declare_own_objects()
        else:
            logger.warning(
                "Broker: service objects are not declared "
                "(RMQ_DECLARE_TOPOLOGY=false). Retries will go through "
                "nack(requeue) with no counter, DLQ copies will not be published"
            )

        queue = await self._check_foreign_topology()

        self._queue = queue
        declaration = getattr(queue, "declaration_result", None)
        logger.info(
            "Broker: topology ready exchange='{}' queue='{}' messages={} "
            "consumers={} dlq={} retry={}",
            cfg.exchange,
            cfg.queue,
            getattr(declaration, "message_count", "?"),
            getattr(declaration, "consumer_count", "?"),
            "ok" if self._dlq_ready else "unavailable",
            "ok" if self._retry_queue_ready else "unavailable",
        )
        # Exchange, его тип и биндинг — забота гейтвея. Печатаем ожидания,
        # чтобы расхождение с продюсером было видно в логе, а не в тишине.
        logger.info(
            "Broker: expecting an exchange of type '{}' with keys {} (declaring and "
            "binding are the producer's job)",
            cfg.exchange_type,
            list(cfg.routing_keys),
        )

    async def start(self) -> None:
        if self._started:
            return
        if self._queue is None:
            from modules.broker.exceptions import BrokerNotConnected

            raise BrokerNotConnected("Broker: start() до успешного connect()")
        self._consumer_tag = await self._queue.consume(
            self._on_message, consumer_tag=self._config.consumer_tag
        )
        self._started = True
        logger.success(
            "Broker: consuming started queue='{}' consumer_tag='{}'",
            self._config.queue,
            self._consumer_tag,
        )
        self._start_dlq_watch()

    async def stop(self) -> None:
        try:
            self._stopping = True
            await self._stop_dlq_watch()
            # Разбудить задачи, стоящие в паузе перед возвратом сообщения:
            # ждать её до конца grace-периода бессмысленно.
            self._stop_event.set()
            if self._queue is not None and self._consumer_tag:
                try:
                    await self._queue.cancel(self._consumer_tag)
                except Exception as exc:
                    logger.warning("Broker: failed to cancel the subscription: {}", exc)

            if self._tasks:
                logger.info(
                    "Broker: waiting for {} active tasks to finish (grace={}s)",
                    len(self._tasks),
                    self._config.shutdown_grace_secs,
                )
                _, pending = await asyncio.wait(
                    set(self._tasks), timeout=self._config.shutdown_grace_secs
                )
                if pending:
                    # Не подтверждаем: брокер сам вернёт сообщения в очередь.
                    logger.warning(
                        "Broker: {} tasks did not finish in time, cancelling them — "
                        "their messages will return to the queue",
                        len(pending),
                    )
                    for task in pending:
                        task.cancel()
                    await asyncio.gather(*pending, return_exceptions=True)

            if self._connection is not None and not self._connection.is_closed:
                await self._connection.close()
        except Exception as exc:
            logger.error("Broker: error while stopping the consumer: {}", exc)
        finally:
            self._connection = None
            self._channel = None
            self._publish_channel = None
            self._queue = None
            self._consumer_tag = None
            self._started = False
            self._dlq_watch_task = None
            self._stats_channel = None

    async def health(self) -> bool:
        return (
            self._started
            and self._connection is not None
            and not self._connection.is_closed
            and self._channel is not None
            and not self._channel.is_closed
        )

    def _on_connection_closed(self, *_) -> None:
        """Реакция на обрыв соединения: снять осиротевшие задачи.

        Брокер возвращает в очередь все неподтверждённые доставки сразу, а
        `connect_robust` восстанавливает подписку — и то же сообщение приезжает
        второй раз. Задача первой доставки при этом продолжает работать: её ack
        уже никуда не годится (delivery tag протух), но она держит воркеры
        пула, пишет свою ленту прогресса и кладёт результат в тот же объект.
        Отменяем: путь отмены в `_process_message` ничего не подтверждает и не
        публикует, сообщение переиграется одной копией.
        """
        logger.warning("Broker: connection to the broker closed")
        if self._stopping:
            # Штатная остановка: задачи гасит stop() со своим grace-периодом.
            return
        orphans = [task for task in self._tasks if not task.done()]
        if not orphans:
            return
        logger.critical(
            "Broker: connection lost while processing — cancelling {} tasks, "
            "the broker has already requeued their messages",
            len(orphans),
        )
        for task in orphans:
            task.cancel()

    async def health_report(self) -> dict:
        """Подробное состояние для `/health`."""
        return {
            "healthy": await self.health(),
            "consuming": self._started,
            "connected": self._connection is not None
            and not self._connection.is_closed,
            "queue": self._config.queue,
            "dlq": self._config.dlq,
            "dlq_ready": self._dlq_ready,
            "retry_queue_ready": self._retry_queue_ready,
            # -1 — сторож ещё не отработал или не смог опросить DLQ.
            "dlq_depth": self._dlq_depth,
        }

    async def dlq_depth(self) -> int:
        """Число сообщений в DLQ. -1 — узнать не удалось.

        Опрос идёт по выделенному долгоживущему каналу с `robust=False`.
        Два обстоятельства делают именно такую форму обязательной:

        * `RobustChannel.declare_queue` кэширует `declaration_result` для
          robust-объявлений, и повторный passive-declare отдал бы счётчик на
          момент первого вызова. `robust=False` мимо кэша — счётчик свежий.
        * Канал на каждый опрос заводить нельзя: при `NOT_FOUND` брокер
          закрывает канал, `close()` пропускается как уже закрытый, а
          `RobustChannel` тихо восстанавливает его — получаем сироту без
          ссылки и рост числа каналов на каждый неудачный опрос.
        """
        if not self._config.dlq or not self._dlq_ready:
            return -1
        if self._connection is None or self._connection.is_closed:
            return -1

        try:
            if self._stats_channel is None or self._stats_channel.is_closed:
                self._stats_channel = await self._connection.channel()
            result = await (
                await self._stats_channel.get_underlay_channel()
            ).queue_declare(self._config.dlq, passive=True)
            return int(result.message_count or 0)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "Broker: failed to poll the DLQ '{}': {}", self._config.dlq, exc
            )
            return -1

    def _start_dlq_watch(self) -> None:
        interval = self._config.dlq_check_interval_secs
        if interval <= 0 or self._dlq_watch_task is not None:
            return
        task = asyncio.create_task(self._dlq_watch_loop(interval))
        task.add_done_callback(self._on_dlq_watch_done)
        self._dlq_watch_task = task

    def _on_dlq_watch_done(self, task: asyncio.Task) -> None:
        """Сторож умер — сказать об этом и не оставлять поле занятым.

        Иначе единственный сигнал о непустой DLQ исчезает молча, а занятое
        поле не даёт поднять сторожа заново.
        """
        if self._dlq_watch_task is task:
            self._dlq_watch_task = None
        if task.cancelled() or self._stopping:
            return
        exc = task.exception()
        if exc is not None:
            logger.critical(
                "Broker: DLQ watchdog crashed with '{}' — a non-empty DLQ will go "
                "unnoticed until the service is restarted",
                exc,
            )

    async def _stop_dlq_watch(self) -> None:
        task = self._dlq_watch_task
        if task is None:
            return
        self._dlq_watch_task = None
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            # Отменили нас самих, а не сторожа — не проглатывать.
            if asyncio.current_task() is not None and asyncio.current_task().cancelling():
                raise
        except Exception as exc:
            logger.warning("Broker: DLQ watchdog exited with an error: {}", exc)

    async def _dlq_watch_loop(self, interval: int) -> None:
        """Периодически смотреть в DLQ и кричать, если она непустая.

        Дежурному это единственный сигнал: у рабочей очереди нет DLX, копии в
        DLQ кладёт сам сервис, и каждое сообщение там — задача, о провале
        которой пользователь уже знает, а мы ещё нет.
        """
        previous = -1
        first = True
        while not self._stopping:
            if not first:
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=interval)
                    return
                except (asyncio.TimeoutError, TimeoutError):
                    pass
                except asyncio.CancelledError:
                    raise
            # Первый опрос сразу: иначе после рестарта пода /health первую
            # минуту отдаёт «не знаем» вместо фактической глубины.
            first = False

            depth = await self.dlq_depth()
            self._dlq_depth = depth
            if depth > 0:
                # Порог по изменению, а не по факту: иначе непустая DLQ будет
                # писать одно и то же в лог каждую минуту до ручного разбора.
                level = "CRITICAL" if depth != previous else "WARNING"
                logger.log(
                    level,
                    "Broker: DLQ '{}' holds {} messages — those tasks did not complete "
                    "and need manual triage",
                    self._config.dlq,
                    depth,
                )
            elif depth == 0 and previous > 0:
                logger.success("Broker: DLQ '{}' is drained", self._config.dlq)
            previous = depth

    # --- обработка сообщений --------------------------------------------

    async def _on_message(self, message) -> None:
        """Колбэк `queue.consume`.

        Работу делает отдельная Task: долгий перевод в колбэке заблокировал бы
        heartbeat соединения.
        """
        if self._stopping:
            await self._safe_nack(message, requeue=True)
            return
        task = asyncio.create_task(self._process_message(message))
        self._tasks.add(task)
        task.add_done_callback(self._on_task_done)

    def _on_task_done(self, task: asyncio.Task) -> None:
        """Снять задачу с учёта и не потерять её исключение.

        Без чтения `exception()` сбой обработчика ушёл бы в «Task exception was
        never retrieved», а сообщение осталось бы без ack и без nack.
        """
        self._tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.error(
                "Broker: message processing failed with an unhandled error "
                "'{}': {}. The message is not acknowledged and will return to "
                "the queue",
                type(exc).__name__,
                exc,
            )

    async def _process_message(self, message) -> None:
        started = time.monotonic()
        attempt = read_attempt(message.headers)
        task_key = ""
        try:
            envelope = parse_envelope(message.body)
            task_key = build_task_key(envelope)
            if payload_user_id_differs(envelope):
                logger.warning(
                    "Broker: payload.user_id differs from the envelope key='{}'",
                    task_key,
                )
            logger.info(
                "Broker: message received key='{}' task_type='{}' attempt={} "
                "redelivered={}",
                task_key,
                envelope.task_type,
                attempt,
                message.redelivered,
            )

            handler_cls = self._dispatcher.resolve(envelope.task_type)
            handler = handler_cls(self._runtime)
            outcome = await handler.handle(envelope, task_key)
        except asyncio.CancelledError:
            # SIGTERM. Ничего не ack-аем и не публикуем: сообщение вернётся в
            # очередь и переиграется на другой реплике.
            logger.warning(
                "Broker: processing interrupted by service shutdown key='{}'", task_key
            )
            raise
        except Exception as exc:
            await self._finalize_failure(message, task_key, attempt, exc)
            return

        elapsed = time.monotonic() - started
        # ack только после терминального статуса, который опубликовал конвейер.
        await self._safe_ack(message)
        logger.success(
            "Broker: task finished key='{}' status='{}' elapsed={:.1f}s",
            task_key,
            outcome.status,
            elapsed,
        )
        if elapsed > self._config.ack_deadline_secs:
            logger.critical(
                "Broker: processing took {:.0f}s against an ack deadline of {}s — the "
                "broker may have already revoked the delivery (consumer_timeout)",
                elapsed,
                self._config.ack_deadline_secs,
            )

    async def _finalize_failure(
        self,
        message,
        task_key: str,
        attempt: int,
        exc: BaseException,
    ) -> None:
        decision = classify_error(exc)
        next_attempt = attempt + 1
        webhook = self._runtime.webhook_manager()

        # 1. Отмена: работы нет, CANCELLED уже опубликовал конвейер.
        if decision.action is MessageAction.ACK:
            if decision.report and task_key and decision.public_message:
                await report_task_error(webhook, task_key, decision.public_message)
            logger.info(
                "Broker: message acknowledged without retry key='{}': {}",
                task_key,
                exc,
            )
            await self._safe_ack(message)
            return

        # 2. Временный сбой, попытки остались -> retry-очередь.
        if (
            decision.action is MessageAction.RETRY
            and next_attempt <= self._config.max_retries
        ):
            logger.warning(
                "Broker: transient failure key='{}' attempt {}/{}: {}",
                task_key,
                next_attempt,
                self._config.max_retries,
                exc,
            )
            if task_key:
                await report_task_retry(
                    webhook, task_key, next_attempt, self._config.max_retries
                )
            await self._schedule_retry(message, next_attempt)
            return

        # 3. Постоянная ошибка либо исчерпанные попытки -> DLQ.
        if decision.action is MessageAction.RETRY:
            public, report = MSG_UPSTREAM_UNAVAILABLE, True
            logger.error(
                "Broker: retries exhausted key='{}' (failed attempts: {}, "
                "retry limit: {}): {}",
                task_key,
                next_attempt,
                self._config.max_retries,
                exc,
            )
        else:
            public, report = decision.public_message, decision.report
            log = getattr(logger, decision.log_level, logger.error)
            log(
                "Broker: message sent to the DLQ key='{}' error='{}'",
                task_key or "<no key>",
                exc,
            )

        if report and task_key and public:
            await report_task_error(webhook, task_key, public)
        if not task_key:
            logger.critical(
                "Broker: envelope could not be parsed, nowhere to report back to the "
                "user. body_head='{}'",
                message.body[:200],
            )

        # Рабочая очередь чужая и DLX у неё нет, поэтому reject(requeue=False)
        # уничтожил бы сообщение. Кладём копию в свой DLQ сами и только потом
        # подтверждаем оригинал: обратный порядок теряет сообщение.
        if await self._publish_to_dlq(message, task_key, next_attempt, exc):
            await self._safe_ack(message)
            return

        logger.critical(
            "Broker: failed to park the message in the DLQ key='{}' — leaving it "
            "unacknowledged, it will return to the queue",
            task_key or "<no key>",
        )
        await self._delay_before_requeue()
        await self._safe_nack(message, requeue=True)

    async def _publish_to_dlq(
        self,
        message,
        task_key: str,
        attempt: int,
        exc: BaseException,
    ) -> bool:
        """Положить копию сообщения в наш DLQ. True — копия сохранена.

        `attempt` здесь — как и в retry-очереди, число уже провалившихся
        обработок этого сообщения, включая текущую.
        """
        cfg = self._config
        if not cfg.dlq or not self._dlq_ready:
            logger.error(
                "Broker: DLQ '{}' is unavailable, nowhere to park the message", cfg.dlq
            )
            return False

        headers = dict(message.headers or {})
        headers[_ATTEMPT_HEADER] = attempt
        # setdefault, а не присваивание: на втором круге routing_key — это уже
        # имя retry-очереди, и настоящий ключ продюсера был бы потерян.
        headers.setdefault(_ORIGINAL_ROUTING_KEY_HEADER, message.routing_key)
        headers[_ERROR_HEADER] = f"{type(exc).__name__}: {exc}"[:500]
        copy = aio_pika.Message(
            body=message.body,
            headers=headers,
            content_type=message.content_type,
            message_id=message.message_id,
            correlation_id=message.correlation_id,
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
        )
        # Через default exchange прямо в очередь: не зависит ни от типа
        # exchange, ни от биндингов.
        if not await self._publish_copy(copy, cfg.dlq):
            # Очередь могли удалить на ходу — больше не делаем вид, что она есть.
            self._dlq_ready = False
            return False
        logger.info(
            "Broker: message parked in the DLQ '{}' key='{}'",
            cfg.dlq,
            task_key or "<no key>",
        )
        return True

    async def _publish_copy(self, copy, routing_key: str) -> bool:
        """Опубликовать копию сообщения в очередь по её имени.

        Успех — только `Basic.Ack` от брокера. Публикация в несуществующую
        очередь исключения не даёт: с `mandatory=True` брокер возвращает
        сообщение (`Basic.Return`), и без этой проверки неотмаршрутизированная
        копия считалась бы сохранённой, а оригинал был бы подтверждён.
        """
        try:
            result = await self._publish_channel.default_exchange.publish(
                copy, routing_key=routing_key, mandatory=True
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(
                "Broker: publishing to the queue '{}' failed: {}", routing_key, exc
            )
            return False

        if isinstance(result, Basic.Ack):
            return True

        delivery = getattr(result, "delivery", result)
        logger.critical(
            "Broker: the broker rejected the message copy for the queue '{}' "
            "(response {}). The queue was most likely deleted or recreated on "
            "the fly — recreate it and restart the service",
            routing_key,
            type(delivery).__name__,
        )
        return False

    async def _schedule_retry(self, message, attempt: int) -> bool:
        """Копия сообщения в retry-очередь + ack оригинала.

        Через `nack(requeue=True)` счётчик попыток не сохранить: заголовки
        вернувшегося сообщения консюмер изменить не может. Retry-очередь с TTL
        и dead-letter напрямую в рабочую очередь даёт и счётчик, и паузу,
        и — главное — не держит слот prefetch на время ожидания.
        """
        if not self._config.retry_queue or not self._retry_queue_ready:
            # Деградация: повтор без счётчика, не переживает рестарт. Пауза
            # обязательна — иначе при лежащем upstream и prefetch>1 получаем
            # горячий цикл, который завалит записями и брокер, и webhook_manager.
            await self._delay_before_requeue()
            await self._safe_nack(message, requeue=True)
            return False

        headers = dict(message.headers or {})
        headers[_ATTEMPT_HEADER] = attempt
        # setdefault, а не присваивание: на втором круге routing_key — это уже
        # имя retry-очереди, и настоящий ключ продюсера был бы потерян.
        headers.setdefault(_ORIGINAL_ROUTING_KEY_HEADER, message.routing_key)
        copy = aio_pika.Message(
            body=message.body,
            headers=headers,
            content_type=message.content_type,
            message_id=message.message_id,
            correlation_id=message.correlation_id,
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
        )
        if not await self._publish_copy(copy, self._config.retry_queue):
            # Иначе повтор превратился бы в исчезновение задачи: оригинал
            # подтверждён, а копии нет — пользователь навсегда в PROCESSING.
            self._retry_queue_ready = False
            await self._delay_before_requeue()
            await self._safe_nack(message, requeue=True)
            return False
        # Публикуем ДО ack: падение между ними даст дубль, обратный порядок —
        # потерю сообщения.
        await self._safe_ack(message)
        return True

    async def _delay_before_requeue(self) -> None:
        """Выдержать паузу перед возвратом сообщения в очередь.

        Возврат через nack происходит мгновенно: без паузы сообщение вернётся
        к нам в тот же миг и будет крутиться на полной скорости. Пауза
        прерывается остановкой сервиса — держать ради неё слот prefetch и
        половину grace-периода незачем.
        """
        delay = max(0, self._config.retry_delay_secs)
        if not delay:
            return
        logger.info(
            "Broker: pausing {}s before returning the message to the queue "
            "(retry queue unavailable)",
            delay,
        )
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=delay)
        except (asyncio.TimeoutError, TimeoutError):
            return
        logger.info("Broker: pause interrupted by service shutdown")

    # --- подтверждения ---------------------------------------------------

    async def _safe_ack(self, message) -> None:
        try:
            await message.ack()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(
                "Broker: failed to acknowledge the message (the channel may have been "
                "closed by the broker on consumer_timeout): {}",
                exc,
            )

    async def _safe_nack(self, message, requeue: bool) -> None:
        try:
            await message.nack(requeue=requeue)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(
                "Broker: failed to return the message to the queue (the channel may "
                "have been closed by the broker on consumer_timeout): {}",
                exc,
            )

