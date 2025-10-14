from abc import ABC, abstractmethod
from pathlib import Path
from PIL.Image import Image
import re

import chardet

from modules.parser.v1.schemas import ParserParams
from docling.document_converter import DocumentConverter
from loguru import logger
from docling_core.types.doc import (
    ImageRefMode
)


class ParserABC(ABC):
    def __init__(
        self,
        parser_params: ParserParams
    ):
        self.parser_params = parser_params
        self.source_file = parser_params.file_path
        self.image_mode = ImageRefMode.EMBEDDED if self.parser_params.include_image_in_output else ImageRefMode.PLACEHOLDER
        self.artifacts_path=Path(__file__).parent.parent.parent.parent.parent.parent.joinpath("ml")
        self.converter = DocumentConverter()

    def parse_with_docling(self, file_path: Path) -> str:
        try:
            doc = self.converter.convert(file_path).document
            logger.debug("Standard Docling conversion completed")
            for item in doc.iterate_items(traverse_pictures=True):
                logger.warning(item)
            markdown = doc.export_to_markdown()
            logger.debug(markdown)
            return markdown
        except Exception as e:
            logger.error(f"Error converting document with Docling: {e}")
            raise e
        
    
    def clean_markdown_text(self, text: str):
        return text

    @abstractmethod
    def parse(self):
        pass
