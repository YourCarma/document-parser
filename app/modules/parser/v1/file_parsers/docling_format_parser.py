from pathlib import Path
from tempfile import NamedTemporaryFile
import shutil
import tempfile

import pypandoc
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import ConvertPipelineOptions
from docling.document_converter import (
    DocumentConverter,
    EmailFormatOption,
    EpubFormatOption,
    MarkdownFormatOption,
    OdpFormatOption,
    OdsFormatOption,
    OdtFormatOption,
    XBRLFormatOption,
)
from docling_core.types.doc import DocItemLabel, PictureItem, TableItem, TextItem
from loguru import logger

from modules.parser.v1.abc.abc import ParserABC
from modules.parser.v1.file_parsers.image_parser import ImageParser
from modules.parser.v1.schemas import ParserMods, ParserParams


class DoclingFormatParser(ParserABC):
    input_format: InputFormat
    format_option_cls: type

    def __init__(self, parser_params: ParserParams):
        super().__init__(parser_params)
        self.converter = DocumentConverter()
        self.pipeline_options = ConvertPipelineOptions(artifacts_path=self.artifacts_path)

    def set_converter_options(self):
        self.converter = DocumentConverter(
            allowed_formats=[self.input_format],
            format_options={
                self.input_format: self.format_option_cls(
                    pipeline_options=self.pipeline_options
                )
            },
        )

    def parse(self, mode: ParserMods):
        logger.debug(f"Parsing {self.source_file}...")
        self.set_converter_options()
        doc = self.converter.convert(self.source_file).document
        logger.success("Document converted!")

        for element, _level in doc.iterate_items():
            if isinstance(element, TextItem):
                element.orig = element.text
                element.text = self.normalize_unicode(element.text)
                element.text = self.clean_text(text=element.text)
                element.text = self.to_utf8(element.text)
            elif isinstance(element, TableItem):
                for cell in element.data.table_cells:
                    cell.text = self.clean_text(text=cell.text)
                    cell.text = self.to_utf8(cell.text)

        if self.parser_params.parse_images:
            logger.debug("Exctracting text from images...")
            for element, _level in doc.iterate_items():
                if isinstance(element, PictureItem) or isinstance(element, TableItem):
                    logger.success("Image or Table detected")
                    image = element.get_image(doc)
                    parser = ImageParser(image)
                    parsed_text = parser.parse_image_for_element(image)
                    doc.insert_text(
                        element,
                        text=parsed_text,
                        orig=parsed_text,
                        label=DocItemLabel.TEXT,
                    )

        match mode:
            case ParserMods.TO_FILE:
                logger.debug("Saving to .md file")
                with NamedTemporaryFile(suffix=".md", delete=False) as tmp_file:
                    doc.save_as_markdown(
                        filename=tmp_file.name,
                        artifacts_dir=self.artifacts_path,
                        image_mode=self.image_mode,
                        page_break_placeholder=self.page_break_placeholder,
                    )
                    logger.success("File Saved!")
                    return tmp_file.name
            case ParserMods.TO_TEXT:
                return doc.export_to_markdown(
                    image_mode=self.image_mode,
                    page_break_placeholder=self.page_break_placeholder,
                )
            case ParserMods.TO_WORD:
                artifacts_dir = Path(tempfile.mkdtemp(prefix="artifacts_"))
                doc_with_refs = doc._make_copy_with_refmode(
                    reference_path=artifacts_dir,
                    artifacts_dir=artifacts_dir,
                    image_mode=self.image_mode,
                    page_no=None,
                )
                markdown = doc_with_refs.export_to_markdown(
                    image_mode=self.image_mode,
                    page_break_placeholder=self.page_break_placeholder,
                )
                with NamedTemporaryFile(suffix=".docx", delete=False) as tmp_file:
                    pypandoc.convert_text(
                        markdown,
                        "docx",
                        format="md",
                        outputfile=tmp_file.name,
                        extra_args=[
                            "--standalone",
                            f"--resource-path={artifacts_dir}",
                        ],
                    )
                    shutil.rmtree(artifacts_dir, ignore_errors=True)
                    return tmp_file.name
            case ParserMods.TO_DOCLING:
                return doc
            case _:
                logger.error("Unknown parse mode!")
                raise ValueError


class ODTParser(DoclingFormatParser):
    input_format = InputFormat.ODT
    format_option_cls = OdtFormatOption


class ODSParser(DoclingFormatParser):
    input_format = InputFormat.ODS
    format_option_cls = OdsFormatOption


class ODPParser(DoclingFormatParser):
    input_format = InputFormat.ODP
    format_option_cls = OdpFormatOption


class TXTParser(DoclingFormatParser):
    input_format = InputFormat.MD
    format_option_cls = MarkdownFormatOption


class EPUBParser(DoclingFormatParser):
    input_format = InputFormat.EPUB
    format_option_cls = EpubFormatOption


class EMLParser(DoclingFormatParser):
    input_format = InputFormat.EMAIL
    format_option_cls = EmailFormatOption


class XBRLParser(DoclingFormatParser):
    input_format = InputFormat.XML_XBRL
    format_option_cls = XBRLFormatOption
