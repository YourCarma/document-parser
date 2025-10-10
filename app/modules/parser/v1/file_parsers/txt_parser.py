import asyncio
from pathlib import Path

from loguru import logger

from modules.parser.v1.abc.abc import ParserABC
from modules.parser.v1.schemas import ParserParams
from modules.parser.v1.utils import read_file_content


class TXTParser(ParserABC):
    def __init__(self, parser_params: ParserParams):
        super().__init__(parser_params)

    
    def parse(self):
        logger.debug(f"Parsing {self.source_file}...")
        file_content = read_file_content(self.parser_params.file_path)
        return file_content