from abc import ABC, abstractmethod
from pathlib import Path
from PIL.Image import Image
import chardet
import re

import chardet
from loguru import logger
from docling.document_converter import DocumentConverter
from docling_core.types.doc import (
    ImageRefMode
)

from settings import settings
from modules.parser.v1.schemas import ParserParams, ParserMods



class ParserABC(ABC):
    def __init__(
        self,
        parser_params: ParserParams
    ):
        self.parser_params = parser_params
        self.source_file = parser_params.file_path
        self.image_mode = ImageRefMode.EMBEDDED if self.parser_params.include_image_in_output else ImageRefMode.PLACEHOLDER
        self.artifacts_path=settings.ARTIFACTS_PATH
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
        
    def to_utf8(self, text: str):
        if isinstance(text, bytes):
            try:
                return text.decode('utf-8')
            except UnicodeDecodeError:
                encoding = chardet.detect(text)['encoding']
                try:
                    return text.decode(encoding or 'utf-8')
                except:
                    return text.decode('utf-8', errors='replace')
        
  
        if isinstance(text, str):
            try:
                fixed = text.encode('latin-1').decode('cp1251')
                return fixed
            except (UnicodeEncodeError, UnicodeDecodeError):
                return text
        
        return str(text)

    def clean_text(self, text: str):
            
        def replace_uni(match):
            try:
                return chr(int(match.group(1), 16))
            except ValueError:
                return match.group(0)
        
        text = re.sub(r'/uni([0-9A-Fa-f]{4})', replace_uni, text)
        invisible_spaces = [
            '\u00A0',  # неразрывный пробел
            '\u1680',  # Ogham space mark
            '\u2000',  # en quad
            '\u2001',  # em quad
            '\u2002',  # en space
            '\u2003',  # em space
            '\u2004',  # three-per-em space
            '\u2005',  # four-per-em space
            '\u2006',  # six-per-em space
            '\u2007',  # figure space
            '\u2008',  # punctuation space
            '\u2009',  # thin space
            '\u200A',  # hair space
            '\u202F',  # narrow no-break space
            '\u205F',  # medium mathematical space
            '\u3000',  # IDEOGRAPHIC SPACE
            '\u00AD',  # мягкий перенос (часто мешает)
            '\u2060',  # word joiner
            '\uFEFF',  # BOM / zero width no-break space
            '\u200B',  # zero width space
            '\u200C',  # zero width non-joiner
            '\u200D',  # zero width joiner
            '\u0009'
        ]
        
        text = text.replace('�', '.')
        for sp in invisible_spaces:
            text = text.replace(sp, ' ')
        return text

    @abstractmethod
    def parse(self, mode: ParserMods = ParserMods.TO_TEXT.value):
        pass