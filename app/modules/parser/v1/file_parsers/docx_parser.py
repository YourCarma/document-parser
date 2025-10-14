from pathlib import Path
from tempfile import NamedTemporaryFile

from docling.document_converter import DocumentConverter
from docling.datamodel.pipeline_options import PipelineOptions, PaginatedPipelineOptions
from docling.datamodel.base_models import  InputFormat
from docling.document_converter import DocumentConverter, WordFormatOption
from loguru import logger
from docling_core.types.doc import (
    ImageRef, PictureItem, TableItem, ImageRefMode, TextItem, DocItemLabel, TableData
)

from modules.parser.v1.schemas import ParserParams, ParserMods
from modules.parser.v1.file_parsers.image_parser import ImageParser
from modules.parser.v1.abc.abc import ParserABC


class DocParser(ParserABC):
    def __init__(self, parser_params: ParserParams):
        super().__init__(parser_params)
        self.converter = DocumentConverter()
        self.pipeline_options = PaginatedPipelineOptions(artifacts_path=self.artifacts_path)

    def set_converter_options(self):
        self.converter = DocumentConverter(format_options={
                InputFormat.DOCX: WordFormatOption(pipeline_options=self.pipeline_options)
                })
        
    def parse(self, mode: ParserMods):
        logger.debug(f"Parsing {self.parser_params.file_path}...")
        self.set_converter_options()
        doc = self.converter.convert(self.parser_params.file_path).document
        logger.success(f"Document converted!")
        logger.debug(f"Exctracting text from images...")
        if self.parser_params.parse_images:
            for element, _level in doc.iterate_items():
                if isinstance(element, PictureItem) or isinstance(element, TableItem):
                    logger.success(f"Image or Table detected")
                    image = element.get_image(doc)
                    parser = ImageParser(image)
                    parsed_text = parser.parse_image_for_element(image)
                    doc.insert_text(element, text=parsed_text, orig=parsed_text, label=DocItemLabel.TEXT)
        
        markdown = doc.export_to_markdown(image_mode=self.image_mode)
        clean_text = self.clean_markdown_text(markdown)
        logger.success("Document have been parsed!")
        if mode == ParserMods.TO_FILE.value:
            logger.debug("Saving to .md file")
            with NamedTemporaryFile(suffix=".md", delete=False) as tmp_file:
                doc.save_as_markdown(filename=tmp_file.name,artifacts_dir=self.artifacts_path, image_mode=self.image_mode)
                logger.success("File Saved!")
                return tmp_file.name
        else: 
            return clean_text