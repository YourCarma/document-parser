from pathlib import Path

from loguru import logger

from modules.parser.v1.schemas import FileFormats, ParserParams
from modules.parser.v1.exceptions import ContentNotSupportedError, ServiceUnavailable, TimeoutError
from modules.parser.v1.file_parsers import (
    EMLParser,
    EPUBParser,
    ImageParser,
    ODPParser,
    ODSParser,
    ODTParser,
    PPTXParser,
    DocParser,
    XLSXParser,
    PDFParser,
    HTMLParser,
    TXTParser,
    PDFVLMParser,
    XBRLParser,
)
from modules.parser.v1.utils import convert_doc_to


class ParserFactory():
    def __init__(self, parser_params: ParserParams):
        self.parser_params = parser_params

        self.IMAGE_FORMATS = FileFormats.IMAGE.value
        self.XLSX_FORMATS = FileFormats.XLSX.value
        self.DOC_FORMATS = FileFormats.DOC.value
        self.ODT_FORMATS = FileFormats.ODT.value
        self.ODS_FORMATS = FileFormats.ODS.value
        self.ODP_FORMATS = FileFormats.ODP.value
        self.PDF_FORMATS = FileFormats.PDF.value
        self.PPTX_FORMATS = FileFormats.PPTX.value
        self.HTML_FORMATS = FileFormats.HTML.value
        self.TXT_FORMATS = FileFormats.TXT.value
        self.EPUB_FORMATS = FileFormats.EPUB.value
        self.EMAIL_FORMATS = FileFormats.EMAIL.value
        self.XBRL_FORMATS = FileFormats.XBRL.value

    def get_parser(self):
        if not isinstance(self.parser_params.file_path, Path):
            self.parser_params.file_path = Path(self.parser_params.file_path)
        source_file_format = self.parser_params.file_path.suffix.lower()
        logger.success(f"Current file format: {source_file_format}")
        logger.debug("Creating Parser...")
        match source_file_format:
            case file_format if file_format in self.IMAGE_FORMATS:
                logger.debug("Image Parser Created!")
                return ImageParser(self.parser_params.file_path)
            
            case file_format if file_format in self.XLSX_FORMATS:
                logger.debug("XLSX Parser Created!")
                return XLSXParser(self.parser_params)

            case file_format if file_format in self.ODS_FORMATS:
                logger.debug("ODS Parser Created!")
                return ODSParser(self.parser_params)
            
            case file_format if file_format in self.DOC_FORMATS:
                if file_format != ".docx":
                    self.parser_params.file_path = convert_doc_to(
                        self.parser_params.file_path,
                        "docx",
                    )
                logger.debug("Doc Parser Created!")
                return DocParser(self.parser_params)

            case file_format if file_format in self.ODT_FORMATS:
                logger.debug("ODT Parser Created!")
                return ODTParser(self.parser_params)
            
            case file_format if file_format in self.PPTX_FORMATS:
                logger.debug("PPTX Parser Created!")
                return PPTXParser(self.parser_params)

            case file_format if file_format in self.ODP_FORMATS:
                logger.debug("ODP Parser Created!")
                return ODPParser(self.parser_params)
            
            case file_format if file_format in self.PDF_FORMATS and not self.parser_params.full_vlm_pdf_parse:
                logger.debug("PDF Parser Created!")
                return PDFParser(self.parser_params)
            
            case file_format if file_format in self.PDF_FORMATS and self.parser_params.full_vlm_pdf_parse:
                logger.debug("PDF VLM Parser Created!")
                return PDFVLMParser(self.parser_params.file_path)
            
            case file_format if file_format in self.HTML_FORMATS:
                logger.debug("HTML Parser Created!")
                return HTMLParser(self.parser_params)
            
            case file_format if file_format in self.TXT_FORMATS:
                logger.debug("TXT Parser Created!")
                return TXTParser(self.parser_params)

            case file_format if file_format in self.EPUB_FORMATS:
                logger.debug("EPUB Parser Created!")
                return EPUBParser(self.parser_params)

            case file_format if file_format in self.EMAIL_FORMATS:
                logger.debug("EML Parser Created!")
                return EMLParser(self.parser_params)

            case file_format if file_format in self.XBRL_FORMATS:
                logger.debug("XBRL Parser Created!")
                return XBRLParser(self.parser_params)
            
            case _:
                raise ContentNotSupportedError(f"Формат \"{source_file_format}\" не поддерживается!")
