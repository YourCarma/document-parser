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

            current_stage = "парсинг документа"
            logger.debug(
                "TranslatorV2: этап='{}' task_id='{}' filename='{}'",
                current_stage,
                task_id,
                original_filename,
            )
            parser = ParserFactory(parser_params).get_parser()
            docling_doc: DoclingDocument = await run_in_process(
                parser.parse, executor, ParserMods.TO_DOCLING
            )
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
                max_concurrency=settings.TRANSALTOR_MAX_CONCURRENCY,
            )
            translated_path = await self._translate_with_progress(
                translator, docling_doc, task_key, response_data
            )

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

            await self._update(
                task_key, response_data, 100, TaskStatus.READY, "Готово"
            )
            logger.success("TranslatorV2: задача успешно завершена task_id='{}'", task_id)

        except Exception as exc:
            logger.error(
                "TranslatorV2: задача завершилась ошибкой task_id='{}' user_id='{}' stage='{}' error='{}'",
                task_id,
                user_id,
                current_stage,
                exc,
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

    async def _update(
        self,
        key: str,
        response_data: TranslatorResponseData,
        progress: float,
        status: TaskStatus,
        text_status: str,
    ):
        response_data.text_status = text_status
        logger.info(
            "TranslatorV2: обновление статуса key='{}' progress={} status='{}' text_status='{}'",
            key,
            progress,
            status,
            text_status,
        )
        await self.webhook.update_progress(key, progress, status)
        await self.webhook.update_response_data(key, response_data.model_dump())
        logger.debug("TranslatorV2: статус обновлён key='{}'", key)

    async def _translate_with_progress(
        self,
        translator: CustomModelTranslator,
        docling_doc: DoclingDocument,
        task_key: str,
        response_data: TranslatorResponseData,
    ) -> str:
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
            return await self._export_to_word(translator, docling_doc)

        completed = [0]
        update_every = max(1, total // 20)
        progress_tasks: list[asyncio.Task] = []

        async def translate_tracked(text: str) -> str:
            result = await translator.translate_element_limited(text)
            completed[0] += 1
            n = completed[0]
            if n % update_every == 0 or n == total:
                progress = 15 + 78 * (n / total)  # 15 → 93
                status_text = f"Перевожу... {n}/{total} элементов"
                logger.debug(
                    "TranslatorV2: прогресс перевода key='{}' progress={:.1f} translated={}/{}",
                    task_key,
                    progress,
                    n,
                    total,
                )
                snapshot = {**response_data.model_dump(), "text_status": status_text}
                async def _send(p=progress, s=snapshot):
                    await self.webhook.update_progress(task_key, p, TaskStatus.PROCESSING)
                    await self.webhook.update_response_data(task_key, s)

                progress_tasks.append(asyncio.create_task(_send()))
            return result

        if text_elements:
            results = await asyncio.gather(
                *[translate_tracked(el.text) for el in text_elements]
            )
            for el, translated in zip(text_elements, results):
                el.text = translated.replace("`", "*")

        if cell_items:
            results = await asyncio.gather(
                *[translate_tracked(cell.text) for cell in cell_items]
            )
            for cell, translated in zip(cell_items, results):
                cell.text = translated.replace("`", "*")

        if progress_tasks:
            logger.debug(
                "TranslatorV2: ожидание отложенных обновлений key='{}' pending={}",
                task_key,
                len(progress_tasks),
            )
            await asyncio.gather(*progress_tasks, return_exceptions=True)
            logger.debug("TranslatorV2: все отложенные обновления завершены key='{}'", task_key)

        return await self._export_to_word(translator, docling_doc)

    @staticmethod
    async def _export_to_word(
        translator: CustomModelTranslator,
        docling_doc: DoclingDocument,
    ) -> str:
        """Экспортировать переведённый `DoclingDocument` во временный `.docx`."""
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
