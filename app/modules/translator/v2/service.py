import asyncio
import shutil
import tempfile
from contextlib import suppress
from pathlib import Path
from tempfile import NamedTemporaryFile

import aiohttp
import pypandoc
from docling_core.types.doc import DoclingDocument, TableItem, TextItem
from fastapi import HTTPException
from loguru import logger

from modules.messages import (
    MSG_FILE_NOT_FOUND,
    MSG_FILE_TOO_LARGE,
    MSG_LANGUAGE_UNDETECTED,
    MSG_NO_BUCKET,
    MSG_TIMEOUT,
    MSG_UNSUPPORTED_FORMAT,
)
from modules.metrics import StageTracker, instruments
from modules.parser.v1.exceptions import ContentNotSupportedError, ConversionTimeoutError
from modules.parser.v1.schemas import ParserMods, ParserParams
from modules.parser.v1.utils import delete_file, parse_document, run_in_process
from modules.resource_manager.exceptions import BucketNotFound
from modules.resource_manager.service import ResourceManagerService
from modules.translator.v1.exceptions import LanguageNotSupported
from modules.translator.v1.service import CustomModelTranslator
from modules.translator.v1.utils import RetryableUpstreamError
from modules.translator.v2.exceptions import TaskTimeout
from modules.translator.v2.schemas import TranslationOutcome, TranslatorResponseData
from modules.translator.v2.sources import LocalUploadSource, SourceFileProviderABC
from modules.watchtower.exceptions import FileNotFoundInStorage, FileTooLargeError
from modules.watchtower.service import WatchtowerService
from modules.webhook_manager.cancellation import (
    CancellationTokenABC,
    NullCancellationToken,
    TaskCancelled,
)
from modules.webhook_manager.schemas import TaskStatus
from modules.webhook_manager.service import WebhookManagerService
from settings import settings


STAGE_INIT = "init"
STAGE_RESOLVE_BUCKET = "resolve user bucket"
STAGE_FETCH_SOURCE = "fetch source file"
STAGE_UPLOAD_ORIGINAL = "upload original file"
STAGE_PARSE = "parse document"
STAGE_TRANSLATE = "translate document"
STAGE_UPLOAD_TRANSLATED = "upload translated file"

_STAGE_MESSAGES: dict[str, str] = {
    STAGE_INIT: "Ошибка при инициализации задачи",
    STAGE_RESOLVE_BUCKET: "Ошибка при получении ресурсов пользователя",
    STAGE_FETCH_SOURCE: "Ошибка при получении исходного файла",
    STAGE_UPLOAD_ORIGINAL: "Ошибка при загрузке оригинального файла в хранилище",
    STAGE_PARSE: "Ошибка при обработке документа",
    STAGE_TRANSLATE: "Ошибка в сервисе переводчика",
    STAGE_UPLOAD_TRANSLATED: "Ошибка при загрузке переведённого файла в хранилище",
}
_TRANSLATION_TIMEOUT_FALLBACK_SUFFIX = " (ошибка запроса, переведите вручную)"
_UNTRANSLATED_TEXT_STATUS = "Готово. Не переведено элементов: {count}"
_CANCELLED_TEXT_STATUS = "Задача отменена"
_TIMEOUT_TEXT_STATUS = "Превышено время обработки"
# Нижняя граница шага опроса отмены на длинных операциях без своих
# контрольных точек (парсинг): чаще смысла нет, ответ всё равно кэширован.
_MIN_CANCEL_POLL_SECS = 1.0
# Готовые словари атрибутов: элементов в документе тысячи, собирать словарь
# на каждом незачем.
_ITEM_TRANSLATED = {"result": "translated"}
_ITEM_UNTRANSLATED = {"result": "untranslated"}


def _stage_to_user_message(stage: str) -> str:
    return _STAGE_MESSAGES.get(stage, "Ошибка при обработке задачи")


# Тип исключения точнее этапа: «файл не найден» полезнее, чем «ошибка при
# загрузке оригинального файла».
_ERROR_MESSAGES: tuple[tuple[type[BaseException], str], ...] = (
    (FileNotFoundInStorage, MSG_FILE_NOT_FOUND),
    (FileTooLargeError, MSG_FILE_TOO_LARGE),
    (BucketNotFound, MSG_NO_BUCKET),
    (ContentNotSupportedError, MSG_UNSUPPORTED_FORMAT),
    (LanguageNotSupported, MSG_LANGUAGE_UNDETECTED),
    (ConversionTimeoutError, MSG_TIMEOUT),
)


def _exception_to_user_message(exc: BaseException, stage: str) -> str:
    """Сначала по типу исключения, иначе — по этапу (прежнее поведение)."""
    for exc_type, message in _ERROR_MESSAGES:
        if isinstance(exc, exc_type):
            return message
    return _stage_to_user_message(stage)


class TranslatorV2Service:

    def __init__(
        self,
        webhook: WebhookManagerService,
        watchtower: WatchtowerService,
        resource_manager: ResourceManagerService,
        translation_semaphore: asyncio.Semaphore | None = None,
        parser_semaphore: asyncio.Semaphore | None = None,
        http_session: aiohttp.ClientSession | None = None,
    ):
        self.webhook = webhook
        self.watchtower = watchtower
        self.resource_manager = resource_manager
        self.translation_semaphore = translation_semaphore
        self.parser_semaphore = parser_semaphore
        self.http_session = http_session
        # Последний успешно опубликованный прогресс: при отмене публикуем его,
        # чтобы не обнулять шкалу в UI. Отсюда правило «один сервис — одна задача».
        self._last_progress: float = 0.0
        # Причина отказа для транспорта: консюмеру нужно исходное исключение,
        # чтобы решить судьбу сообщения.
        self.last_error: BaseException | None = None
        self.last_stage: str = ""
        # Токен текущей задачи: нужен публикаторам статуса, чтобы не писать
        # PROCESSING поверх уже выставленного пользователем CANCELLED.
        self._cancellation: CancellationTokenABC = NullCancellationToken()

    async def run_translation_task(
        self,
        user_id: str,
        task_id: str,
        task_key: str,
        file_path: str,
        original_filename: str,
        source_language: str,
        target_language: str,
        parser_params: ParserParams,
        executor,
        cancellation: CancellationTokenABC | None = None,
        *,
        source: SourceFileProviderABC | None = None,
        bucket: str | None = None,
        output_prefix: str = "",
    ) -> TaskStatus:
        """Выполнить фоновую задачу перевода и вернуть терминальный статус.

        Исключений не бросает — кроме `asyncio.CancelledError`, который обязан
        пройти наружу, чтобы отмена корутины работала штатно.

        `source is None` -> локальный файл по `file_path` (HTTP-сценарий).
        `bucket is not None` -> resource_manager не опрашивается.
        `output_prefix` — префикс только для переведённого файла.
        """
        cancellation = cancellation or NullCancellationToken(task_key)
        self._cancellation = cancellation
        # Источник различает сценарии: локальный файл приходит из HTTP,
        # готовый провайдер — из очереди. В метриках это разные режимы работы.
        stages = StageTracker(source="http" if source is None else "broker")
        source = source or LocalUploadSource(file_path, original_filename)
        final_status = TaskStatus.ERROR
        # Исход для метрик: у TaskStatus нет отдельных значений для таймаута и
        # внешней отмены, а на графике их надо различать.
        metrics_outcome = "error"
        translated_path: str | None = None
        response_data = TranslatorResponseData(
            original_language=source_language,
            target_language=target_language,
            text_status="Получение ресурсов пользователя...",
        )

        current_stage = stages.enter(STAGE_INIT)
        try:
            async with asyncio.timeout(settings.TASK_TIMEOUT_SECS):
                await cancellation.raise_if_cancelled("до старта")

                current_stage = stages.enter(STAGE_RESOLVE_BUCKET)
                bucket = bucket or await self.resource_manager.get_user_bucket(user_id)
                if not bucket:
                    raise BucketNotFound(user_id)
                logger.info(
                    "TranslatorV2: task started task_id='{}' user_id='{}' bucket='{}' source='{}' target='{}'",
                    task_id,
                    user_id,
                    bucket,
                    source_language,
                    target_language,
                )
                await cancellation.raise_if_cancelled(STAGE_RESOLVE_BUCKET)

                current_stage = stages.enter(STAGE_FETCH_SOURCE)
                await self._update(
                    task_key, response_data, 5, TaskStatus.PROCESSING,
                    "Готовлю исходный файл...",
                )
                # Скачивание из бакета тоже без своих контрольных точек:
                # большой файл едет минутами.
                source_file = await self._await_or_cancel(
                    source.acquire(bucket), cancellation, STAGE_FETCH_SOURCE
                )
                file_path = source_file.local_path
                original_filename = source_file.original_filename
                # Локальный путь известен только здесь: из очереди файл
                # появляется на диске лишь после acquire().
                parser_params.file_path = Path(file_path)

                current_stage = stages.enter(STAGE_UPLOAD_ORIGINAL)
                if source_file.remote_key is None:
                    object_key = await self.watchtower.upload_file(
                        bucket,
                        file_path,
                        original_filename,
                    )
                else:
                    # Из очереди оригинал уже в бакете — повторная заливка была
                    # бы и лишней, и неидемпотентной.
                    object_key = source_file.remote_key
                # Отдаём object key, а не share-ссылку: ссылка протухает по
                # сроку, а ключ в бакете живёт столько же, сколько файл.
                response_data.original_file = object_key
                await self._update(
                    task_key, response_data, 10, TaskStatus.PROCESSING,
                    "Оригинал готов. Парсинг документа...",
                )
                await cancellation.raise_if_cancelled(STAGE_UPLOAD_ORIGINAL)

                current_stage = stages.enter(STAGE_PARSE)
                logger.debug(
                    "TranslatorV2: stage='{}' task_id='{}' filename='{}'",
                    current_stage,
                    task_id,
                    original_filename,
                )
                try:
                    async with asyncio.timeout(settings.PARSE_TIMEOUT_SECS):
                        docling_doc: DoclingDocument = await self._await_or_cancel(
                            run_in_process(
                                parse_document,
                                executor,
                                parser_params,
                                ParserMods.TO_DOCLING,
                                semaphore=self.parser_semaphore,
                            ),
                            cancellation,
                            STAGE_PARSE,
                        )
                except TimeoutError as exc:
                    # Отменяется только ожидание: воркер парсинга останется
                    # занят до конца конвертации, слот семафора вернётся раньше.
                    logger.error(
                        "TranslatorV2: parsing exceeded PARSE_TIMEOUT_SECS "
                        "task_id='{}' timeout={}",
                        task_id,
                        settings.PARSE_TIMEOUT_SECS,
                    )
                    raise TaskTimeout(
                        "Парсинг документа превысил PARSE_TIMEOUT_SECS"
                    ) from exc
                await cancellation.raise_if_cancelled(STAGE_PARSE)
                await self._update(
                    task_key, response_data, 15, TaskStatus.PROCESSING,
                    "Начинаю перевод...",
                )

                current_stage = stages.enter(STAGE_TRANSLATE)
                translator = CustomModelTranslator(
                    source=Path(file_path),
                    source_language=source_language,
                    target_language=target_language,
                    # Режим картинок при экспорте в .docx берётся из переводчика,
                    # поэтому флаг клиента должен дойти именно сюда.
                    include_image_in_output=bool(parser_params.include_image_in_output),
                    max_concurrency=settings.TRANSLATOR_MAX_CONCURRENCY,
                    shared_semaphore=self.translation_semaphore,
                    http_session=self.http_session or self.webhook.session,
                )
                outcome = await self._translate_with_progress(
                    translator, docling_doc, task_key, response_data, cancellation
                )
                translated_path = outcome.file_path
                await cancellation.raise_if_cancelled(STAGE_TRANSLATE)

                current_stage = stages.enter(STAGE_UPLOAD_TRANSLATED)
                await self._update(
                    task_key, response_data, 95, TaskStatus.PROCESSING,
                    "Загружаю переведённый файл...",
                )
                stem = Path(original_filename).stem
                translated_filename = f"{stem}_(переведённый).docx"
                # Пустой префикс не передаём вовсе: HTTP-сценарий кладёт
                # результат в корень бакета ровно как раньше.
                upload_kwargs = {"prefix": output_prefix} if output_prefix else {}
                translated_key = await self.watchtower.upload_file(
                    bucket,
                    translated_path,
                    translated_filename,
                    **upload_kwargs,
                )
                response_data.translated_file = translated_key

                if outcome.untranslated_count > 0:
                    final_text_status = _UNTRANSLATED_TEXT_STATUS.format(
                        count=outcome.untranslated_count
                    )
                else:
                    final_text_status = "Готово"
                await self._publish_terminal(
                    task_key, response_data, 100, TaskStatus.READY, final_text_status
                )
                logger.success(
                    "TranslatorV2: task completed successfully task_id='{}'", task_id
                )
                final_status = TaskStatus.READY
                metrics_outcome = "ready"

        except TaskCancelled as exc:
            self.last_error = None
            logger.info(
                "TranslatorV2: task cancelled task_id='{}' user_id='{}' stage='{}'",
                task_id,
                user_id,
                exc.stage or current_stage,
            )
            await self._publish_terminal(
                task_key,
                response_data,
                self._last_progress,
                TaskStatus.CANCELLED,
                _CANCELLED_TEXT_STATUS,
            )
            final_status = TaskStatus.CANCELLED
            metrics_outcome = "cancelled"
        except asyncio.CancelledError:
            # Корутину гасят снаружи (SIGTERM, закрытие цикла): публиковать
            # статус уже некому и нечем, но временные файлы обязаны уйти.
            logger.warning(
                "TranslatorV2: task coroutine cancelled from outside task_id='{}' stage='{}'",
                task_id,
                current_stage,
            )
            metrics_outcome = "interrupted"
            raise
        except (TimeoutError, TaskTimeout) as exc:
            self.last_error = exc
            self.last_stage = current_stage
            logger.error(
                "TranslatorV2: task exceeded its time limit "
                "task_id='{}' user_id='{}' stage='{}' error='{}'",
                task_id,
                user_id,
                current_stage,
                exc,
            )
            response_data.error = _TIMEOUT_TEXT_STATUS
            await self._publish_terminal(
                task_key,
                response_data,
                0,
                TaskStatus.ERROR,
                _TIMEOUT_TEXT_STATUS,
            )
            final_status = TaskStatus.ERROR
            metrics_outcome = "timeout"
        except Exception as exc:
            self.last_error = exc
            self.last_stage = current_stage
            logger.error(
                "TranslatorV2: task failed task_id='{}' user_id='{}' stage='{}' error='{}'",
                task_id,
                user_id,
                current_stage,
                exc,
            )
            public_error = _exception_to_user_message(exc, current_stage)
            response_data.error = public_error
            await self._publish_terminal(
                task_key,
                response_data,
                0,
                TaskStatus.ERROR,
                public_error,
            )
            final_status = TaskStatus.ERROR
        finally:
            stages.finish(metrics_outcome, current_stage, self.last_error)
            await self._cleanup_files(file_path, translated_path)
            try:
                await source.release()
            except Exception as exc:
                logger.warning(
                    "TranslatorV2: failed to release the file source "
                    "task_id='{}': {}",
                    task_id,
                    exc,
                )

        return final_status

    async def _update(
        self,
        key: str,
        response_data: TranslatorResponseData,
        progress: float,
        status: TaskStatus,
        text_status: str,
    ) -> None:
        """Промежуточная публикация статуса.

        Best-effort (P0-2): сбой webhook_manager не должен ронять задачу,
        которая по существу выполнена.
        """
        # webhook_manager хранит отмену в том же поле status, что и прогресс:
        # наш PROCESSING поверх CANCELLED стёр бы отмену навсегда — задача
        # доработала бы до конца, сколько её ни отменяй.
        if status is TaskStatus.PROCESSING:
            await self._cancellation.raise_if_cancelled(fresh=True)
        response_data.text_status = text_status
        logger.info(
            "TranslatorV2: status update key='{}' progress={} status='{}' text_status='{}'",
            key,
            progress,
            status,
            text_status,
        )
        try:
            # attempts=1: промежуточный статус best-effort, backoff здесь
            # только удлинил бы задачу при лежащем webhook_manager.
            await self.webhook.update_progress(key, progress, status, attempts=1)
            await self.webhook.update_response_data(
                key, response_data.model_dump(), attempts=1
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "TranslatorV2: failed to publish an intermediate status "
                "key='{}' progress={}: {}",
                key,
                progress,
                exc,
            )
            return
        self._last_progress = progress
        logger.debug("TranslatorV2: status updated key='{}'", key)

    async def _publish_terminal(
        self,
        key: str,
        response_data: TranslatorResponseData,
        progress: float,
        status: TaskStatus,
        text_status: str,
    ) -> bool:
        """Терминальная публикация. Ретраи — внутри клиента webhook_manager."""
        response_data.text_status = text_status
        logger.info(
            "TranslatorV2: terminal status key='{}' progress={} status='{}' text_status='{}'",
            key,
            progress,
            status,
            text_status,
        )
        try:
            await self.webhook.update_progress(key, progress, status)
            await self.webhook.update_response_data(key, response_data.model_dump())
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(
                "TranslatorV2: failed to publish the terminal status "
                "key='{}' status='{}': {}",
                key,
                status,
                exc,
            )
            return False
        self._last_progress = progress
        return True

    async def _cleanup_files(self, *paths) -> None:
        """Удалить временные файлы задачи.

        При `CancelledError` (SIGTERM) асинхронное удаление уже невозможно —
        дочищаем синхронно, иначе временные файлы утекут на диск.
        """
        for path in paths:
            if not path:
                continue
            try:
                await delete_file(path)
            except asyncio.CancelledError:
                Path(path).unlink(missing_ok=True)
            except Exception as exc:
                logger.warning(
                    "TranslatorV2: failed to delete the temporary file '{}': {}",
                    path,
                    exc,
                )

    async def _translate_with_progress(
        self,
        translator: CustomModelTranslator,
        docling_doc: DoclingDocument,
        task_key: str,
        response_data: TranslatorResponseData,
        cancellation: CancellationTokenABC | None = None,
    ) -> TranslationOutcome:
        cancellation = cancellation or NullCancellationToken(task_key)
        if translator.source_language == "auto":
            sample_texts = []
            for element, _ in docling_doc.iterate_items():
                if isinstance(element, TextItem) and element.text.strip():
                    sample_texts.append(element.text)
                    if len(sample_texts) >= 3:
                        break
            if sample_texts:
                detected = await translator.detect_language("\n".join(sample_texts))
                if not detected:
                    response_data.error = (
                        "Language Detector не смог определить язык документа. "
                        "Укажите source_language явно вместо 'auto'."
                    )
                    raise LanguageNotSupported(
                        detail=response_data.error
                    )
                translator.source_language = detected
                response_data.original_language = detected
                logger.info(
                    "TranslatorV2: language detected automatically key='{}' language='{}'",
                    task_key,
                    detected,
                )

        text_elements: list[TextItem] = []
        cell_items = []
        for element, _ in docling_doc.iterate_items():
            if isinstance(element, TextItem):
                element.orig = element.text
                text_elements.append(element)
            elif isinstance(element, TableItem):
                for cell in element.data.table_cells:
                    cell_items.append(cell)

        total = len(text_elements) + len(cell_items)
        if total == 0:
            return TranslationOutcome(
                file_path=await self._export_to_word(translator, docling_doc),
                untranslated_count=0,
            )

        completed = [0]
        failed = [0]
        update_every = max(1, total // 20)
        progress_lock = asyncio.Lock()

        async def translate_tracked(text: str) -> str:
            # На каждом элементе, а не только на границе батча: батч из
            # TRANSLATOR_MAX_CONCURRENCY элементов с ретраями живёт долго.
            await cancellation.raise_if_cancelled(STAGE_TRANSLATE)
            try:
                translated = await translator.translate_element_limited(text)
                instruments().translate_items.add(1, _ITEM_TRANSLATED)
                return translated
            except TaskCancelled:
                # Отмена задачи — не деградация одного элемента.
                raise
            except (
                TimeoutError,
                RetryableUpstreamError,
                HTTPException,
                ValueError,
                aiohttp.ClientError,
            ) as exc:
                failed[0] += 1
                instruments().translate_items.add(1, _ITEM_UNTRANSLATED)
                logger.warning(
                    "TranslatorV2: item left untranslated key='{}' error='{}'",
                    task_key,
                    exc,
                )
                return f"{text}{_TRANSLATION_TIMEOUT_FALLBACK_SUFFIX}"
            finally:
                completed[0] += 1
                n = completed[0]
                if n % update_every == 0 or n == total:
                    progress = 15 + 78 * (n / total)  # 15 → 93
                    status_text = f"Перевожу... {n}/{total} элементов"
                    if failed[0] > 0:
                        status_text = f"{status_text} (не переведено: {failed[0]})"
                    logger.debug(
                        "TranslatorV2: translation progress key='{}' progress={:.1f} translated={}/{}",
                        task_key,
                        progress,
                        n,
                        total,
                    )
                    snapshot = {**response_data.model_dump(), "text_status": status_text}

                    async def _send(p=progress, s=snapshot):
                        # Тот же запрет, что и в _update: PROCESSING поверх
                        # CANCELLED убил бы отмену. Шкалу при этом двигаем:
                        # её отдаст терминальная публикация CANCELLED.
                        if await cancellation.is_cancelled(fresh=True):
                            self._last_progress = p
                            return
                        await self.webhook.update_progress(
                            task_key, p, TaskStatus.PROCESSING, attempts=1
                        )
                        await self.webhook.update_response_data(task_key, s, attempts=1)
                        # Отмена посреди перевода публикует _last_progress:
                        # без этой строки шкала в UI откатится на 15%.
                        self._last_progress = p

                    async with progress_lock:
                        try:
                            await _send()
                        except Exception as exc:
                            logger.warning(
                                "TranslatorV2: failed to update "
                                "progress key='{}': {}",
                                task_key,
                                exc,
                            )

        if text_elements:
            await self._translate_in_batches(
                text_elements,
                lambda element: element.text,
                lambda element, translated: setattr(
                    element, "text", translated.replace("`", "*")
                ),
                translate_tracked,
                cancellation,
            )

        if cell_items:
            await self._translate_in_batches(
                cell_items,
                lambda cell: cell.text,
                lambda cell, translated: setattr(
                    cell, "text", translated.replace("`", "*")
                ),
                translate_tracked,
                cancellation,
            )

        return TranslationOutcome(
            file_path=await self._export_to_word(translator, docling_doc),
            untranslated_count=failed[0],
        )

    @staticmethod
    async def _translate_in_batches(items, get_text, set_text, translate, cancellation):
        """Переводить без создания корутины на каждый элемент сразу."""
        batch_size = max(1, settings.TRANSLATOR_MAX_CONCURRENCY)
        for start in range(0, len(items), batch_size):
            await cancellation.raise_if_cancelled(STAGE_TRANSLATE)
            batch = items[start : start + batch_size]
            # return_exceptions: бросить прямо из-под gather нельзя — соседние
            # корутины батча остались бы висеть и дописывать PROCESSING уже
            # после публикации CANCELLED, снова стирая отмену.
            results = await asyncio.gather(
                *(translate(get_text(item)) for item in batch),
                return_exceptions=True,
            )
            failure = next(
                (r for r in results if isinstance(r, BaseException)), None
            )
            if failure is not None:
                raise failure
            for item, translated in zip(batch, results):
                set_text(item, translated)

    @staticmethod
    async def _await_or_cancel(
        awaitable,
        cancellation: CancellationTokenABC,
        stage: str,
    ):
        """Ждать длинную операцию, параллельно опрашивая отмену.

        Внутрь операции не заглядываем: парсинг идёт в отдельном процессе и
        прервать его нельзя — бросаем только ожидание, воркер дорабатывает сам
        (та же семантика, что у PARSE_TIMEOUT_SECS).
        """
        task = asyncio.ensure_future(awaitable)
        poll_secs = max(_MIN_CANCEL_POLL_SECS, settings.TASK_CANCEL_CHECK_TTL_SECS)
        try:
            while True:
                done, _ = await asyncio.wait({task}, timeout=poll_secs)
                if done:
                    return task.result()
                if await cancellation.is_cancelled():
                    raise TaskCancelled(cancellation.task_key, stage)
        except BaseException:
            if not task.done():
                task.cancel()
                with suppress(BaseException):
                    await task
            raise

    @staticmethod
    async def _export_to_word(
        translator: CustomModelTranslator,
        docling_doc: DoclingDocument,
    ) -> str:
        """Экспортировать переведённый `DoclingDocument` во временный `.docx`."""
        return await asyncio.to_thread(
            TranslatorV2Service._export_to_word_sync,
            translator,
            docling_doc,
        )

    @staticmethod
    def _export_to_word_sync(
        translator: CustomModelTranslator,
        docling_doc: DoclingDocument,
    ) -> str:
        artifacts_dir = Path(tempfile.mkdtemp(prefix="artifacts_"))
        try:
            doc_with_refs = docling_doc._make_copy_with_refmode(
                reference_path=artifacts_dir,
                artifacts_dir=artifacts_dir,
                image_mode=translator.image_mode,
                page_no=None,
            )
            markdown = doc_with_refs.export_to_markdown(
                image_mode=translator.image_mode,
                page_break_placeholder=translator.page_break_placeholder,
            )
            with NamedTemporaryFile(suffix=".docx", delete=False) as tmp:
                pypandoc.convert_text(
                    markdown,
                    "docx",
                    format="md",
                    outputfile=tmp.name,
                    extra_args=[
                        "--standalone",
                        f"--resource-path={artifacts_dir}",
                        "--wrap=none",
                    ],
                )
                return tmp.name
        finally:
            shutil.rmtree(artifacts_dir, ignore_errors=True)
