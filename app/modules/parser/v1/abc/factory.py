from pathlib import Path

from loguru import logger

from modules.parser.v1.schemas import FileFormats, ParserParams
from modules.parser.v1.exceptions import ContentNotSupportedError, ServiceUnavailable, TimeoutError
from modules.parser.v1.file_parsers import ImageParser, PPTXParser, DocParser, XLSXParser,PDFParser, HTMLParser, TXTParser, PDFVLMParser
from modules.parser.v1.utils import convert_doc_to


class ParserFactory():
    def __init__(self, parser_params: ParserParams):
        self.parser_params = parser_params

        self.IMAGE_FORMATS = FileFormats.IMAGE.value
        self.XLSX_FORMATS = FileFormats.XLSX.value
        self.DOC_FORMATS = FileFormats.DOC.value
        self.PDF_FORMATS = FileFormats.PDF.value
        self.PPTX_FORMATS = FileFormats.PPTX.value
        self.HTML_FORMATS = FileFormats.HTML.value
        self.TXT_FORMATS = FileFormats.TXT.value

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
                self.parser_params.file_path = convert_doc_to(self.parser_params.file_path, "xlsx")
                logger.debug("XLSX Parser Created!")
                return XLSXParser(self.parser_params)
            
            case file_format if file_format in self.DOC_FORMATS:
                self.parser_params.file_path = convert_doc_to(self.parser_params.file_path, "docx")
                logger.debug("Doc Parser Created!")
                return DocParser(self.parser_params)
            
            case file_format if file_format in self.PPTX_FORMATS:
                self.parser_params.file_path = convert_doc_to(self.parser_params.file_path, "pptx")
                logger.debug("PPTX Parser Created!")
                return PPTXParser(self.parser_params)
            
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
            
            case _:
                raise ContentNotSupportedError(f"Формат \"{source_file_format}\" не поддерживается!")
        