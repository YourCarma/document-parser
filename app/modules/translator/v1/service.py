import asyncio

from docling.document_converter import DocumentConverter
from loguru import logger
from docling.datamodel.base_models import  InputFormat

from modules.translator.v1.abc.abc import AbstractTranslator
from modules.translator.v1.utils import post_request
from tempfile import NamedTemporaryFile
from modules.parser.v1.schemas import ParserMods
from modules.translator.v1.schemas import CustomTranslatorBody
from docling_core.types.doc import (
    ImageRef, PictureItem, TableItem, ImageRefMode, TextItem, DocItemLabel, TableData
)
from modules.translator.v1.utils import retry

from settings import settings


class CustomModelTranslator(AbstractTranslator):
    def __init__(self, source_text, source_language, target_language, include_image_in_output, max_concurrency: int = 5):
        super().__init__(source_text, source_language, target_language, include_image_in_output)
        self.document_converter = DocumentConverter()

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

    retry(3, TimeoutError)
    async def translate_element(self, text: str) -> str:
        translate_body = self.create_translator_service_body(text).model_dump()
        translated: dict = (await post_request(settings.TRANSALTOR_TRANSLATE_URL, translate_body)).get("text")
        logger.success(f"Translation completed: {translated}")
        return translated


    async def translate(self, mode: ParserMods):
        convertation = self.document_converter.convert_string(self.source_text, InputFormat.MD, "TEXT_TO_TRANSLATE")
        doc = convertation.document
        logger.debug("Translating elements...")
        
        text_item_translation_tasks = []
        text_elements = []

        cell_item_transaltion_tasks = []
        cell_items = []
        
        for element, _level in doc.iterate_items():
            if isinstance(element, TextItem):
                element.orig = element.text
                text_item_translation_tasks.append(self.translate_element_limited(element.text))
                text_elements.append(element)

            elif isinstance(element, TableItem):
                for cell in element.data.table_cells:
                    cell_item_transaltion_tasks.append(self.translate_element_limited(cell.text))
                    cell_items.append(cell)
        

        if text_item_translation_tasks:
            logger.debug("Translating text items")
            translated_texts = await asyncio.gather(*text_item_translation_tasks)
            for element, translated_text in zip(text_elements, translated_texts):
                element.text = translated_text

        if cell_item_transaltion_tasks:
            logger.debug("Translating cells in tables")
            translated_cells = await asyncio.gather(*cell_item_transaltion_tasks)
            for cell, translated_text in zip(cell_items, translated_cells):
                cell.text = translated_text


        match mode:
            case ParserMods.TO_FILE:
                logger.debug("Saving to .md file")
                with NamedTemporaryFile(suffix=".md", delete=False) as tmp_file:
                    doc.save_as_markdown(
                        filename=tmp_file.name,
                        artifacts_dir=settings.ARTIFACTS_PATH,
                        image_mode=self.image_mode
                    )
                    logger.success("File Saved!")
                    return tmp_file.name

            case ParserMods.TO_TEXT:
                markdown = doc.export_to_markdown(image_mode=self.image_mode)
                return markdown

            case ParserMods.TO_DOCLING:
                return doc

            case _:
                logger.error("Unknown parse mode!")
                raise ValueError

