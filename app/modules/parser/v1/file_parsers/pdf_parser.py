from pathlib import Path

from docling.document_converter import DocumentConverter
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.datamodel.base_models import  InputFormat
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling.backend.pypdfium2_backend import PyPdfiumDocumentBackend
from loguru import logger
from docling_core.types.doc import (
    ImageRef, PictureItem, TableItem, ImageRefMode, TextItem, DocItemLabel, TableData
)

from modules.parser.v1.file_parsers.image_parser import ImageParser
from modules.parser.v1.abc.abc import ParserABC
from modules.parser.v1.schemas import ParserParams


class PDFParser(ParserABC):
    def __init__(self, parser_params: ParserParams):
        super().__init__(parser_params)
        self.converter = DocumentConverter()
        self.pipeline_options = PdfPipelineOptions(artifacts_path=self.artifacts_path, 
                                                   generate_parsed_pages=True, 
                                                   generate_picture_images=True,
                                                   generate_page_images=True,
                                                   do_code_enrichment=True,
                                                   do_ocr=False
                                                   )

    def set_converter_options(self):
        self.converter = DocumentConverter(format_options={
                InputFormat.PDF: PdfFormatOption(pipeline_options=self.pipeline_options, backend=PyPdfiumDocumentBackend)
                })
        
    def parse(self):
        logger.debug(f"Parsing {self.source_file}...")
        self.set_converter_options()
        doc = self.converter.convert(self.source_file).document
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
        doc.save_as_markdown('test.md', image_mode=ImageRefMode.EMBEDDED)
        logger.success("Document have been parsed!")
        return markdown
