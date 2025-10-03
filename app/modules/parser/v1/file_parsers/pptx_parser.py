from pathlib import Path

from docling.document_converter import DocumentConverter
from docling.datamodel.pipeline_options import PipelineOptions, PaginatedPipelineOptions
from docling.document_converter import DocumentConverter, PowerpointFormatOption
from docling.datamodel.base_models import  InputFormat
from loguru import logger
from docling_core.types.doc import (
    ImageRef, PictureItem, TableItem, ImageRefMode, TextItem, DocItemLabel, TableData
)

from modules.parser.v1.file_parsers.image_parser import ImageParser
from modules.parser.v1.abc.abc import ParserABC
from modules.parser.v1.exceptions import ServiceUnavailable, TimeoutError
from modules.parser.v1.schemas import ParserParams

class PPTXParser(ParserABC):
    def __init__(self, parser_params: ParserParams):
        super().__init__(parser_params)
        self.converter = DocumentConverter()
        self.pipeline_options = PaginatedPipelineOptions(artifacts_path=self.artifacts_path,
                                                         generate_page_images=True,
                                                         generate_picture_images=True)

    def set_converter_options(self):
        self.converter = DocumentConverter(format_options={
                InputFormat.PPTX: PowerpointFormatOption(pipeline_options=self.pipeline_options)
                })
        
    def parse(self):
        logger.debug(f"Parsing {self.source_file}...")
        self.set_converter_options()
        doc = self.converter.convert(self.source_file).document
        logger.success(f"Document converted!")
        logger.debug(f"Exctracting text from images...")
        for element, _level in doc.iterate_items():
            if isinstance(element, PictureItem) or isinstance(element, TableItem):
                logger.success(f"Image or Table detected")
                image = element.get_image(doc)
                parser = ImageParser(image)
                parsed_text = parser.parse_image_for_element(image)
                doc.insert_text(element, text=parsed_text, orig=parsed_text, label=DocItemLabel.TEXT)
        markdown = doc.export_to_markdown()
        doc.save_as_markdown('test.md', image_mode=ImageRefMode.EMBEDDED)
        logger.success("Document have been parsed!")
        return markdown