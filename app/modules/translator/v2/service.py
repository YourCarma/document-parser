import asyncio
import shutil
import tempfile
from pathlib import Path
from tempfile import NamedTemporaryFile

import pypandoc
from docling_core.types.doc import DoclingDocument, TableItem, TextItem
from loguru import logger

from modules.parser.v1.abc.factory import ParserFactory
from modules.parser.v1.schemas import ParserMods, ParserParams
from modules.parser.v1.utils import delete_file, run_in_process
from modules.resource_manager.service import ResourceManagerService
from modules.translator.v1.exceptions import LanguageNotSupported
from modules.translator.v1.service import CustomModelTranslator
from modules.translator.v2.schemas import TranslatorResponseData
from modules.watchtower.service import WatchtowerService
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


def _stage_to_user_message(stage: str) -> str:
    return _STAGE_MESSAGES.get(stage, f"Ошибка на этапе «{stage}»")


class TranslatorV2Service:
    def __init__(
        self,
        webhook: WebhookManagerService,
        watchtower: WatchtowerService,
        resource_manager: ResourceManagerService,
    ):
        self.webhook = webhook
        self.watchtower = watchtower
        self.resource_manager = resource_manager

    # ------------------------------------------------------------------
    # Public entry point — runs as a BackgroundTask
    # ------------------------------------------------------------------

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
    ):
        translated_path: str | None = None
        response_data = TranslatorResponseData(
            original_language=source_language,
            target_language=target_language,
            text_status="Получение ресурсов пользователя...",
        )

        current_stage = "инициализация"
        try:
            # 1. Resolve user bucket
            current_stage = "получение бакета пользователя"
            bucket = await self.resource_manager.get_user_bucket(user_id)
            if not bucket:
                raise Exception(
                    f"Resource Manager не вернул бакет для пользователя '{user_id}'. "
                    "Убедитесь, что у пользователя есть ресурс типа Document."
                )

            # 3. Upload original file
            current_stage = "загрузка оригинального файла"
            await self._update(
                task_key, response_data, 5, TaskStatus.PROCESSING,
                "Загружаю оригинальный файл...",
            )
            object_key = await self.watchtower.upload_file(
                bucket, file_path, original_filename
            )
            original_link = await self.watchtower.get_sharelink(bucket, object_key)
            response_data.original_file = original_link
            await self._update(
                task_key, response_data, 10, TaskStatus.PROCESSING,
                "Оригинал загружен. Парсинг документа...",
            )

            # 4. Parse document (CPU-bound → process pool)
            current_stage = "парсинг документа"
            parser = ParserFactory(parser_params).get_parser()
            docling_doc: DoclingDocument = await run_in_process(
                parser.parse, executor, ParserMods.TO_DOCLING
            )
            await self._update(
                task_key, response_data, 15, TaskStatus.PROCESSING,
                "Начинаю перевод...",
            )

            # 5. Translate with per-element progress reporting
            current_stage = "перевод документа"
            translator = CustomModelTranslator(
                source=Path(file_path),
                source_language=source_language,
                target_language=target_language,
                include_image_in_output=False,
                max_concurrency=settings.TRANSALTOR_MAX_CONCURRENCY,
            )
            translated_path = await self._translate_with_progress(
                translator, docling_doc, task_key, response_data
            )

            # 6. Upload translated file
            current_stage = "загрузка переведённого файла"
            await self._update(
                task_key, response_data, 95, TaskStatus.PROCESSING,
                "Загружаю переведённый файл...",
            )
            stem = Path(original_filename).stem
            translated_filename = f"{stem}_(переведённый).docx"
            translated_key = await self.watchtower.upload_file(
                bucket, translated_path, translated_filename
            )
            translated_link = await self.watchtower.get_sharelink(bucket, translated_key)
            response_data.translated_file = translated_link

            # 7. Mark READY
            await self._update(
                task_key, response_data, 100, TaskStatus.READY, "Готово"
            )
            logger.success(f"Task {task_id} completed")

        except Exception as exc:
            logger.error(
                f"Task {task_id} failed at stage '{current_stage}': {exc}"
            )
            response_data.error = str(exc)
            await self._update(
                task_key, response_data, 0, TaskStatus.ERROR,
                _stage_to_user_message(current_stage),
            )
        finally:
            await delete_file(file_path)
            if translated_path:
                try:
                    await delete_file(translated_path)
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _update(
        self,
        key: str,
        response_data: TranslatorResponseData,
        progress: float,
        status: TaskStatus,
        text_status: str,
    ):
        """Update progress then response_data sequentially to avoid read-modify-write race."""
        response_data.text_status = text_status
        logger.info(f"[{key}] _update → progress={progress} status={status} | {text_status}")
        await self.webhook.update_progress(key, progress, status)
        await self.webhook.update_response_data(key, response_data.model_dump())
        logger.debug(f"[{key}] _update done")

    async def _translate_with_progress(
        self,
        translator: CustomModelTranslator,
        docling_doc: DoclingDocument,
        task_key: str,
        response_data: TranslatorResponseData,
    ) -> str:
        """
        Translate all text/table elements in docling_doc.
        Fires fire-and-forget webhook updates every ~5% of elements.
        Returns path to a temporary .docx file.
        """
        # --- Language auto-detection ---
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
                logger.info(f"Detected language: {detected}")

        # --- Collect elements ---
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
            return await self._export_to_word(translator, docling_doc)

        # --- Progress state ---
        completed = [0]
        update_every = max(1, total // 20)  # report roughly every 5%
        progress_tasks: list[asyncio.Task] = []

        async def translate_tracked(text: str) -> str:
            result = await translator.translate_element_limited(text)
            completed[0] += 1
            n = completed[0]
            if n % update_every == 0 or n == total:
                progress = 15 + 78 * (n / total)  # 15 → 93
                status_text = f"Перевожу... {n}/{total} элементов"
                logger.debug(f"[{task_key}] progress={progress:.1f} ({n}/{total})")
                snapshot = {**response_data.model_dump(), "text_status": status_text}
                async def _send(p=progress, s=snapshot):
                    await self.webhook.update_progress(task_key, p, TaskStatus.PROCESSING)
                    await self.webhook.update_response_data(task_key, s)

                progress_tasks.append(asyncio.create_task(_send()))
            return result

        # --- Translate text items ---
        if text_elements:
            results = await asyncio.gather(
                *[translate_tracked(el.text) for el in text_elements]
            )
            for el, translated in zip(text_elements, results):
                el.text = translated.replace("`", "*")

        # --- Translate table cells ---
        if cell_items:
            results = await asyncio.gather(
                *[translate_tracked(cell.text) for cell in cell_items]
            )
            for cell, translated in zip(cell_items, results):
                cell.text = translated.replace("`", "*")

        # Дожидаемся всех progress-обновлений прежде чем вернуть управление,
        # иначе они могут выполниться уже после финального READY
        if progress_tasks:
            logger.debug(f"[{task_key}] Awaiting {len(progress_tasks)} pending progress tasks...")
            await asyncio.gather(*progress_tasks, return_exceptions=True)
            logger.debug(f"[{task_key}] All progress tasks done")

        return await self._export_to_word(translator, docling_doc)

    @staticmethod
    async def _export_to_word(
        translator: CustomModelTranslator,
        docling_doc: DoclingDocument,
    ) -> str:
        """Export translated DoclingDocument to a temporary .docx file."""
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
