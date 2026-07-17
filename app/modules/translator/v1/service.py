import asyncio
from collections.abc import Sequence
from pathlib import Path
import shutil
import tempfile
from typing import Any

import aiohttp
import pypandoc
from loguru import logger

from modules.translator.v1.abc.abc import AbstractTranslator
from tempfile import NamedTemporaryFile

from modules.translator.v1.utils import RetryableUpstreamError, post_request, retry
from modules.parser.v1.schemas import ParserMods
from modules.translator.v1.schemas import CustomTranslatorBody
from docling_core.types.doc import (
    TableItem,  TextItem, DoclingDocument )

from settings import settings
from modules.translator.v1.exceptions import LanguageNotSupported


class CustomModelTranslator(AbstractTranslator):
    def __init__(
        self,
        source: str | Path,
        source_language,
        target_language,
        include_image_in_output,
        max_concurrency: int = 10,
        shared_semaphore: asyncio.Semaphore | None = None,
        http_session: aiohttp.ClientSession | None = None,
    ):
        super().__init__(source, source_language, target_language, include_image_in_output)
        
        self.max_concurrency = max(1, int(max_concurrency))
        self.sem = shared_semaphore or asyncio.Semaphore(self.max_concurrency)
        self.http_session = http_session
        
  
    def create_translator_service_body(self, text: str) -> CustomTranslatorBody:
        return CustomTranslatorBody(
            text=text,
            source_language=self.source_language,
            target_language=self.target_language
        )

    async def translate_element_limited(self, text: str) -> str:
        async with self.sem:
            return await self.translate_element(text)

    
    @retry(3, RetryableUpstreamError)
    async def detect_language(self, text: str) -> str | None:
        language_detect = {
            "text": text
        }
        request = post_request(
            settings.DETECT_LANGUAGE_URL,
            language_detect,
            **({"session": self.http_session} if self.http_session is not None else {}),
        )
        detected = (await request).get("detected_language")
        return detected if isinstance(detected, str) else None
    
    @retry(3, RetryableUpstreamError)
    async def translate_element(self, text: str) -> str:
        translate_body = self.create_translator_service_body(text).model_dump()
        request = post_request(
            settings.TRANSLATOR_TRANSLATE_URL,
            translate_body,
            **({"session": self.http_session} if self.http_session is not None else {}),
        )
        translated = (await request).get("text")
        if not isinstance(translated, str):
            raise ValueError("Сервис перевода вернул некорректный ответ")
        return translated

    async def _translate_in_batches(self, texts: Sequence[str]) -> list[str]:
        """Translate a bounded number of elements at once and preserve order."""
        translated: list[str] = []
        for offset in range(0, len(texts), self.max_concurrency):
            batch = texts[offset:offset + self.max_concurrency]
            translated.extend(await asyncio.gather(*(
                self.translate_element_limited(text) for text in batch
            )))
        return translated

    def _export_docling(self, mode: ParserMods, docling_data: DoclingDocument) -> str:
        """Perform the synchronous Docling/Pandoc export outside the event loop."""
        match mode:
            case ParserMods.TO_FILE:
                logger.debug("Saving to .md file")
                with NamedTemporaryFile(suffix=".md", delete=False) as tmp_file:
                    output_path = tmp_file.name
                try:
                    docling_data.save_as_markdown(
                        filename=output_path,
                        artifacts_dir=settings.ARTIFACTS_PATH,
                        image_mode=self.image_mode,
                        page_break_placeholder="---",
                    )
                    logger.success("File Saved!")
                    return output_path
                except Exception:
                    Path(output_path).unlink(missing_ok=True)
                    raise

            case ParserMods.TO_TEXT:
                return docling_data.export_to_markdown(
                    image_mode=self.image_mode,
                    page_break_placeholder=self.page_break_placeholder,
                )

            case ParserMods.TO_WORD:
                logger.debug("Saving to Word")
                artifacts_dir = Path(tempfile.mkdtemp(prefix="artifacts_"))
                output_path: str | None = None
                try:
                    doc_with_refs = docling_data._make_copy_with_refmode(
                        reference_path=artifacts_dir,
                        artifacts_dir=artifacts_dir,
                        image_mode=self.image_mode,
                        page_no=None,
                    )
                    markdown = doc_with_refs.export_to_markdown(
                        image_mode=self.image_mode,
                        page_break_placeholder=self.page_break_placeholder,
                    )
                    with NamedTemporaryFile(suffix=".docx", delete=False) as tmp_file:
                        output_path = tmp_file.name
                    pypandoc.convert_text(
                        markdown,
                        "docx",
                        format="md",
                        outputfile=output_path,
                        extra_args=[
                            "--standalone",
                            f"--resource-path={artifacts_dir}",
                            "--wrap=none",
                        ],
                    )
                    return output_path
                except Exception:
                    if output_path is not None:
                        Path(output_path).unlink(missing_ok=True)
                    raise
                finally:
                    shutil.rmtree(artifacts_dir, ignore_errors=True)

            case _:
                logger.error("Unknown parse mode!")
                raise ValueError(f"Unknown parse mode: {mode}")


    async def translate_docling(self, mode: ParserMods, docling_data: DoclingDocument):
        if self.source_language == "auto":
            logger.debug("Detecting language from first 3 paragraphs...")
            sample_texts = []
            for element, _level in docling_data.iterate_items():
                if isinstance(element, TextItem) and element.text.strip():
                    sample_texts.append(element.text)
                    if len(sample_texts) >= 3:
                        break
        
            if sample_texts:
                combined_sample = "\n".join(sample_texts)
                detected = await self.detect_language(combined_sample)
                if not detected:
                    raise LanguageNotSupported(detail="Language Detector: Язык не определен!")
                self.source_language = detected
                logger.info(f"Detected language: {self.source_language}")
        
        
        text_elements: list[TextItem] = []
        text_values: list[str] = []

        cell_items: list[Any] = []
        cell_values: list[str] = []
        
        logger.debug("Translating elements...")
        for element, _level in docling_data.iterate_items():
            if isinstance(element, TextItem):
                element.orig = element.text
            
                text_elements.append(element)
                text_values.append(element.text)

            elif isinstance(element, TableItem):
                for cell in element.data.table_cells:
                    cell_items.append(cell)
                    cell_values.append(cell.text)
        

        if text_values:
            logger.debug("Translating text items")
            translated_texts = await self._translate_in_batches(text_values)
            for element, translated_text in zip(text_elements, translated_texts):
                translated_text = translated_text.replace('`', "*")
                element.text = translated_text

        if cell_values:
            logger.debug("Translating cells in tables")
            translated_cells = await self._translate_in_batches(cell_values)
            for cell, translated_text in zip(cell_items, translated_cells):
                translated_text = translated_text.replace('`', "*")
                cell.text = translated_text


        return await asyncio.to_thread(self._export_docling, mode, docling_data)
