import asyncio
from pathlib import Path
import shutil
import tempfile

import pypandoc

from loguru import logger

from modules.translator.v1.abc.abc import AbstractTranslator
from modules.translator.v1.utils import post_request
from tempfile import NamedTemporaryFile
from modules.parser.v1.schemas import ParserMods
from modules.translator.v1.schemas import CustomTranslatorBody
from docling_core.types.doc import (
    TableItem,  TextItem, DoclingDocument )
from modules.translator.v1.utils import retry

from settings import settings
from modules.translator.v1.exceptions import LanguageNotSupported


class CustomModelTranslator(AbstractTranslator):
    def __init__(self, source: str | Path, source_language, target_language, include_image_in_output, max_concurrency: int = 10):
        super().__init__(source, source_language, target_language, include_image_in_output)
        
        self.sem = asyncio.Semaphore(max_concurrency)
        
  
    def create_translator_service_body(self, text: str) -> CustomTranslatorBody:
        return CustomTranslatorBody(
            text=text,
            source_language=self.source_language,
            target_language=self.target_language
        )

    async def translate_element_limited(self, text: str) -> str:
        async with self.sem:
            return await self.translate_element(text)

    
    async def detect_language(self, text: str) -> str:
        language_detect = {
            "text": text
        }
        detected: dict = (await post_request(settings.DETECT_LANGUAGE_URL, language_detect)).get("detected_language")
        return detected
    
    retry(3, TimeoutError)
    async def translate_element(self, text: str) -> str:
        translate_body = self.create_translator_service_body(text).model_dump()
        translated: dict = (await post_request(settings.TRANSALTOR_TRANSLATE_URL, translate_body)).get("text")
        logger.success(f"Translation completed: {translated}")
        return translated


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
        
        logger.debug("Translating elements...")
        text_item_translation_tasks = []
        text_elements = []

        cell_item_transaltion_tasks = []
        cell_items = []
        
        for element, _level in docling_data.iterate_items():
            if isinstance(element, TextItem):
                element.orig = element.text
                logger.debug(element.orig)
                text_item_translation_tasks.append(self.translate_element_limited(element.text))
                text_elements.append(element)

            elif isinstance(element, TableItem):
                for cell in element.data.table_cells:
                    cell_item_transaltion_tasks.append(self.translate_element_limited(cell.text))
                    cell_items.append(cell)
        

        if text_item_translation_tasks:
            logger.debug("Translating text items")
            translated_texts: list[str] = await asyncio.gather(*text_item_translation_tasks)
            for element, translated_text in zip(text_elements, translated_texts):
                translated_text = translated_text.replace('`', "*")
                element.text = translated_text

        if cell_item_transaltion_tasks:
            logger.debug("Translating cells in tables")
            translated_cells = await asyncio.gather(*cell_item_transaltion_tasks)
            for cell, translated_text in zip(cell_items, translated_cells):
                translated_text = translated_text.replace('`', "*")
                cell.text = translated_text


        match mode:
            case ParserMods.TO_FILE:
                logger.debug("Saving to .md file")
                with NamedTemporaryFile(suffix=".md", delete=False) as tmp_file:
                    docling_data.save_as_markdown(
                        filename=tmp_file.name,
                        artifacts_dir=settings.ARTIFACTS_PATH,
                        image_mode=self.image_mode,
                        page_break_placeholder="---"
                    )
                    logger.success("File Saved!")
                    return tmp_file.name

            case ParserMods.TO_TEXT:
                markdown = docling_data.export_to_markdown(image_mode=self.image_mode, page_break_placeholder=self.page_break_placeholder)
                return markdown
            
            case ParserMods.TO_WORD:
                artifacts_dir = Path(tempfile.mkdtemp(prefix="artifacts_"))
                doc_with_refs = docling_data._make_copy_with_refmode(
                                        reference_path=artifacts_dir,
                                        artifacts_dir=artifacts_dir,
                                        image_mode=self.image_mode,
                                        page_no=None
                                    )
                markdown = doc_with_refs.export_to_markdown(image_mode=self.image_mode, 
                                                  page_break_placeholder=self.page_break_placeholder)
                with NamedTemporaryFile(suffix=".docx", delete=False) as tmp_file:
                    pypandoc.convert_text(markdown, 
                            "docx", 
                            format="md", 
                            outputfile=tmp_file.name,
                            extra_args=[
                                '--standalone',
                                f'--resource-path={artifacts_dir}',
                                '--wrap=none'    
                            ])
                    shutil.rmtree(artifacts_dir, ignore_errors=True)
                    return tmp_file.name

            case _:
                logger.error("Unknown parse mode!")
                raise ValueError
