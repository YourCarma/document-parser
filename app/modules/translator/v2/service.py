import asyncio
import shutil
import tempfile
from pathlib import Path
from tempfile import NamedTemporaryFile

import aiohttp
import pypandoc
from docling_core.types.doc import DoclingDocument, TableItem, TextItem
from fastapi import HTTPException
from loguru import logger

from modules.parser.v1.schemas import ParserMods, ParserParams
from modules.parser.v1.utils import delete_file, parse_document, run_in_process
from modules.resource_manager.service import ResourceManagerService
from modules.translator.v1.exceptions import LanguageNotSupported
from modules.translator.v1.service import CustomModelTranslator
from modules.translator.v1.utils import RetryableUpstreamError
from modules.translator.v2.exceptions import TaskTimeout
from modules.translator.v2.schemas import TranslationOutcome, TranslatorResponseData
from modules.watchtower.service import WatchtowerService
from modules.webhook_manager.cancellation import (
    CancellationTokenABC,
    NullCancellationToken,
    TaskCancelled,
)
from modules.webhook_manager.schemas import TaskStatus
from modules.webhook_manager.service import WebhookManagerService
from settings import settings


_STAGE_MESSAGES: dict[str, str] = {
    "инициализация": "Ошибка при инициализации задачи",
    "получение бакета пользователя": "Ошибка при получении ресурсов пользователя",
    "загрузка оригинального файла": "Ошибка при загрузке оригинального файла в хранилище",
    "парсинг документа": "Ошибка при обработке документа",
    "перевод документа": "Ошибка в сервисе переводчика",
    "загрузка переведённого файла": "Ошибка при загрузке переведённого файла в хранилище",
}
_TRANSLATION_TIMEOUT_FALLBACK_SUFFIX = " (ошибка запроса, переведите вручную)"
_UNTRANSLATED_TEXT_STATUS = "Готово. Не переведено элементов: {count}"
_CANCELLED_TEXT_STATUS = "Задача отменена"
_TIMEOUT_TEXT_STATUS = "Превышено время обработки"


def _stage_to_user_message(stage: str) -> str:
    return _STAGE_MESSAGES.get(stage, f"Ошибка на этапе «{stage}»")


class TranslatorV2Service:

    def __init__(
        self,
        webhook: WebhookManagerService,
        watchtower: WatchtowerService,
        resource_manager: ResourceManagerService,
        translation_semaphore: asyncio.Semaphore | None = None,
        parser_semaphore: asyncio.Semaphore | None = None,
    ):
        self.webhook = webhook
        self.watchtower = watchtower
        self.resource_manager = resource_manager
        self.translation_semaphore = translation_semaphore
        self.parser_semaphore = parser_semaphore
        # Последний успешно опубликованный прогресс: при отмене публикуем его,
        # чтобы не обнулять шкалу в UI. Отсюда правило «один сервис — одна задача».
        self._last_progress: float = 0.0

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
    ) -> TaskStatus:
        """Выполнить фоновую задачу перевода и вернуть терминальный статус.

        Исключений не бросает — кроме `asyncio.CancelledError`, который обязан
        пройти наружу, чтобы отмена корутины работала штатно.
        """
        cancellation = cancellation or NullCancellationToken(task_key)
        final_status = TaskStatus.ERROR
        translated_path: str | None = None
        response_data = TranslatorResponseData(
            original_language=source_language,
            target_language=target_language,
            text_status="Получение ресурсов пользователя...",
        )

        current_stage = "инициализация"
        try:
            async with asyncio.timeout(settings.TASK_TIMEOUT_SECS):
                await cancellation.raise_if_cancelled("до старта")

                current_stage = "получение бакета пользователя"
                bucket = await self.resource_manager.get_user_bucket(user_id)
                if not bucket:
                    raise Exception(
                        f"Resource Manager не вернул бакет для пользователя '{user_id}'. "
                        "Убедитесь, что у пользователя есть ресурс типа Document."
                    )
                logger.info(
                    "TranslatorV2: старт задачи task_id='{}' user_id='{}' bucket='{}' source='{}' target='{}'",
                    task_id,
                    user_id,
                    bucket,
                    source_language,
                    target_language,
                )
                await cancellation.raise_if_cancelled("получение бакета пользователя")

                current_stage = "загрузка оригинального файла"
                await self._update(
                    task_key, response_data, 5, TaskStatus.PROCESSING,
                    "Загружаю оригинальный файл...",
                )
                object_key = await self.watchtower.upload_file(
                    bucket,
                    file_path,
                    original_filename,
                )
                original_link = await self.watchtower.get_sharelink(bucket, object_key)
                response_data.original_file = original_link
                await self._update(
                    task_key, response_data, 10, TaskStatus.PROCESSING,
                    "Оригинал загружен. Парсинг документа...",
                )
                await cancellation.raise_if_cancelled("загрузка оригинального файла")

                current_stage = "парсинг документа"
                logger.debug(
                    "TranslatorV2: этап='{}' task_id='{}' filename='{}'",
                    current_stage,
                    task_id,
                    original_filename,
                )
                try:
                    async with asyncio.timeout(settings.PARSE_TIMEOUT_SECS):
                        docling_doc: DoclingDocument = await run_in_process(
                            parse_document,
                            executor,
                            parser_params,
                            ParserMods.TO_DOCLING,
                            semaphore=self.parser_semaphore,
                        )
                except TimeoutError as exc:
                    # Отменяется только ожидание: воркер парсинга останется
                    # занят до конца конвертации, слот семафора вернётся раньше.
                    logger.error(
                        "TranslatorV2: парсинг превысил PARSE_TIMEOUT_SECS "
                        "task_id='{}' timeout={}",
                        task_id,
                        settings.PARSE_TIMEOUT_SECS,
                    )
                    raise TaskTimeout(
                        "Парсинг документа превысил PARSE_TIMEOUT_SECS"
                    ) from exc
                await cancellation.raise_if_cancelled("парсинг документа")
                await self._update(
                    task_key, response_data, 15, TaskStatus.PROCESSING,
                    "Начинаю перевод...",
                )

                current_stage = "перевод документа"
                translator = CustomModelTranslator(
                    source=Path(file_path),
                    source_language=source_language,
                    target_language=target_language,
                    include_image_in_output=False,
                    max_concurrency=settings.TRANSLATOR_MAX_CONCURRENCY,
                    shared_semaphore=self.translation_semaphore,
                    http_session=self.webhook.session,
                )
                outcome = await self._translate_with_progress(
                    translator, docling_doc, task_key, response_data, cancellation
                )
                translated_path = outcome.file_path
                await cancellation.raise_if_cancelled("перевод документа")

                current_stage = "загрузка переведённого файла"
                await self._update(
                    task_key, response_data, 95, TaskStatus.PROCESSING,
                    "Загружаю переведённый файл...",
                )
                stem = Path(original_filename).stem
                translated_filename = f"{stem}_(переведённый).docx"
                translated_key = await self.watchtower.upload_file(
                    bucket,
                    translated_path,
                    translated_filename,
                )
                translated_link = await self.watchtower.get_sharelink(
                    bucket, translated_key
                )
                response_data.translated_file = translated_link

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
                    "TranslatorV2: задача успешно завершена task_id='{}'", task_id
                )
                final_status = TaskStatus.READY

        except TaskCancelled as exc:
            logger.info(
                "TranslatorV2: задача отменена task_id='{}' user_id='{}' stage='{}'",
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
        except asyncio.CancelledError:
            # Корутину гасят снаружи (SIGTERM, закрытие цикла): публиковать
            # статус уже некому и нечем, но временные файлы обязаны уйти.
            logger.warning(
                "TranslatorV2: корутина задачи отменена извне task_id='{}' stage='{}'",
                task_id,
                current_stage,
            )
            raise
        except (TimeoutError, TaskTimeout) as exc:
            logger.error(
                "TranslatorV2: задача превысила лимит времени "
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
        except Exception as exc:
            logger.error(
                "TranslatorV2: задача завершилась ошибкой task_id='{}' user_id='{}' stage='{}' error='{}'",
                task_id,
                user_id,
                current_stage,
                exc,
            )
            public_error = _stage_to_user_message(current_stage)
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
            await self._cleanup_files(file_path, translated_path)

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
        response_data.text_status = text_status
        logger.info(
            "TranslatorV2: обновление статуса key='{}' progress={} status='{}' text_status='{}'",
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
                "TranslatorV2: не удалось опубликовать промежуточный статус "
                "key='{}' progress={}: {}",
                key,
                progress,
                exc,
            )
            return
        self._last_progress = progress
        logger.debug("TranslatorV2: статус обновлён key='{}'", key)

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
            "TranslatorV2: терминальный статус key='{}' progress={} status='{}' text_status='{}'",
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
                "TranslatorV2: не удалось опубликовать терминальный статус "
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
                    "TranslatorV2: не удалось удалить временный файл '{}': {}",
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
                    "TranslatorV2: язык определён автоматически key='{}' language='{}'",
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
            try:
                return await translator.translate_element_limited(text)
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
                logger.warning(
                    "TranslatorV2: элемент не переведён key='{}' error='{}'",
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
                        "TranslatorV2: прогресс перевода key='{}' progress={:.1f} translated={}/{}",
                        task_key,
                        progress,
                        n,
                        total,
                    )
                    snapshot = {**response_data.model_dump(), "text_status": status_text}

                    async def _send(p=progress, s=snapshot):
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
                                "TranslatorV2: не удалось обновить "
                                "прогресс key='{}': {}",
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
            await cancellation.raise_if_cancelled("перевод документа")
            batch = items[start : start + batch_size]
            results = await asyncio.gather(
                *(translate(get_text(item)) for item in batch)
            )
            for item, translated in zip(batch, results):
                set_text(item, translated)

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
