from pathlib import Path
from tempfile import NamedTemporaryFile

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
from modules.parser.v1.schemas import ParserParams, ParserMods

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
        
    def parse(self, mode: ParserMods):
        logger.debug(f"Parsing {self.source_file}...")
        self.set_converter_options()
        doc = self.converter.convert(self.source_file).document
        logger.success(f"Document converted!")
        logger.debug(f"Exctracting text from images...")
        logger.debug("Cleaning text")
        for element, _level in doc.iterate_items():
            if isinstance(element, TextItem):
                element.orig = element.text
                element.text = self.clean_text(text=element.text)

            elif isinstance(element, TableItem):
                for cell in element.data.table_cells:
                    cell.text = self.clean_text(text=cell.text)

        if self.parser_params.parse_images:
            for element, _level in doc.iterate_items():
                if isinstance(element, PictureItem) or isinstance(element, TableItem):
                    logger.success(f"Image or Table detected")
                    image = element.get_image(doc)
                    parser = ImageParser(image)
                    parsed_text = parser.parse_image_for_element(image)
                    doc.insert_text(element, text=parsed_text, orig=parsed_text, label=DocItemLabel.TEXT)

        match mode:
                case ParserMods.TO_FILE:
                    logger.debug("Saving to .md file")
                    with NamedTemporaryFile(suffix=".md", delete=False) as tmp_file:
                        doc.save_as_markdown(filename=tmp_file.name,artifacts_dir=self.artifacts_path, image_mode=self.image_mode)
                        logger.success("File Saved!")
                        return tmp_file.name
                case ParserMods.TO_TEXT:
                    markdown = doc.export_to_markdown(image_mode=self.image_mode)
                    return markdown
                case _:
                    logger.error("Unknown parse mode!")
                    raise ValueError