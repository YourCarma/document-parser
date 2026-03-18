from pathlib import Path
from tempfile import NamedTemporaryFile
import tempfile
import atexit
import shutil

import pypandoc
from docling.document_converter import DocumentConverter
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.datamodel.base_models import  InputFormat
from docling.pipeline.threaded_standard_pdf_pipeline import ThreadedStandardPdfPipeline
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling.backend.docling_parse_backend import DoclingParseDocumentBackend


from docling.backend.docling_parse_backend import DoclingParseDocumentBackend
from loguru import logger
from docling_core.types.doc import (
    ImageRef, PictureItem, TableItem, ImageRefMode, TextItem, DocItemLabel, TableData
)

from modules.parser.v1.file_parsers.image_parser import ImageParser
from modules.parser.v1.abc.abc import ParserABC
from modules.parser.v1.schemas import ParserParams, ParserMods


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
                InputFormat.PDF: PdfFormatOption(pipeline_options=self.pipeline_options,pipeline_cls=ThreadedStandardPdfPipeline, 
                                                 backend=DoclingParseDocumentBackend)
                })
        
    def parse(self, mode: ParserMods):
        logger.debug(f"Using device: {self.pipeline_options.accelerator_options.device}")
        logger.debug(f"Parsing {self.source_file}...")
        self.set_converter_options()
        doc = self.converter.convert(self.source_file).document
        logger.success(f"Document converted!")
        logger.debug("Cleaning documents")
        for element, _level in doc.iterate_items():
            if isinstance(element, TextItem):
                element.orig = element.text
                element.text = self.clean_text(text=element.text)
                element.text = self.to_utf8(element.text)

            elif isinstance(element, TableItem):
                for cell in element.data.table_cells:
                    cell.text = self.clean_text(text=cell.text)
                    cell.text = self.to_utf8(cell.text)
        logger.debug(f"Exctracting text from images...")
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
                with NamedTemporaryFile(suffix=".md", delete=False, mode="w", encoding="utf-8", delete_on_close=False) as tmp_file:
                    
                    markdown_content = doc.export_to_markdown(
                        image_mode=self.image_mode,
                        page_break_placeholder=self.page_break_placeholder
                    )
                    tmp_file.write(markdown_content)
                    # doc.save_as_markdown(filename=tmp_file.name,
                    #                      artifacts_dir=self.artifacts_path, 
                    #                      image_mode=self.image_mode, 
                    #                      page_break_placeholder=self.page_break_placeholder)

                    logger.success("File Saved!")
                    return tmp_file.name
            case ParserMods.TO_TEXT:
              
                markdown = doc.export_to_markdown(image_mode=self.image_mode, 
                                                  page_break_placeholder=self.page_break_placeholder)
                return markdown
            case ParserMods.TO_DOCLING:
                return doc
            case ParserMods.TO_WORD:
                artifacts_dir = Path(tempfile.mkdtemp(prefix="artifacts_"))
                doc_with_refs = doc._make_copy_with_refmode(
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
                                f'--resource-path={artifacts_dir}'    
                            ])
                    shutil.rmtree(artifacts_dir, ignore_errors=True)
                    return tmp_file.name
            case _:
                logger.error("Unknown parse mode!")
                raise ValueError

        
