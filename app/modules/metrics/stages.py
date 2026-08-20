"""Тайминги конвейера перевода: одна метрика и один спан на этап.

Этапы в `TranslatorV2Service` идут последовательно и переключаются
присваиванием `current_stage`. Трекер встраивается ровно в это место, поэтому
не требует ни вложенных `async with`, ни перестройки кода задачи.
"""

import time

from modules.metrics.instruments import get_tracer, instruments


class StageTracker:
    """Учёт этапов одной задачи перевода.

    Порядок вызовов: `enter()` на каждом переходе, ровно один `finish()` в
    конце. Повторный `finish()` игнорируется — так его безопасно звать из
    `finally`.
    """

    __slots__ = ("_source", "_started", "_stage", "_stage_started", "_span", "_finished")

    def __init__(self, source: str = "http") -> None:
        self._source = source
        self._started = time.monotonic()
        self._stage: str = ""
        self._stage_started: float = self._started
        self._span = None
        self._finished = False

    def enter(self, stage: str) -> str:
        """Закрыть предыдущий этап, начать новый. Возвращает имя этапа."""
        self._close("ok")
        self._stage = stage
        self._stage_started = time.monotonic()
        self._span = get_tracer().start_span(
            f"translate {stage}",
            attributes={"task.stage": stage, "task.source": self._source},
        )
        return stage

    def finish(
        self,
        status: str,
        stage: str = "",
        error: BaseException | None = None,
    ) -> None:
        """Записать терминальный исход задачи."""
        if self._finished:
            return
        self._finished = True
        self._close("ok" if error is None else "failed", error)

        attributes = {
            "status": status,
            "source": self._source,
            "stage": stage or self._stage,
            "error_type": type(error).__name__ if error is not None else "none",
        }
        instruments().tasks.add(1, attributes)
        instruments().task_seconds.record(
            time.monotonic() - self._started,
            {"status": status, "source": self._source},
        )

    def _close(self, outcome: str, error: BaseException | None = None) -> None:
        """Закрыть текущий этап: гистограмма + завершение спана."""
        if not self._stage:
            return
        instruments().task_stage_seconds.record(
            time.monotonic() - self._stage_started,
            {"stage": self._stage, "outcome": outcome, "source": self._source},
        )
        span = self._span
        self._span = None
        if span is None:
            return
        try:
            if error is not None:
                span.record_exception(error)
            span.end()
        except Exception:
            # Наблюдаемость не имеет права влиять на судьбу задачи.
            pass
