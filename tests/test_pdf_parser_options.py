import unittest
from pathlib import Path
from unittest.mock import patch

from docling.datamodel.pipeline_options import ConvertPipelineOptions, ThreadedPdfPipelineOptions
from docling.datamodel.pipeline_options_vlm_model import ResponseFormat

from modules.parser.v1.exceptions import TimeoutError
from modules.parser.v1.file_parsers.html_parser import HTMLParser
from modules.parser.v1.file_parsers.image_parser import ImageParser
from modules.parser.v1.file_parsers.pdf_parser import PDFParser
from modules.parser.v1.file_parsers.pdf_parser_vlm import PDFVLMParser
from modules.parser.v1.schemas import ParserParams


class PDFParserOptionsTest(unittest.TestCase):
    def test_pdf_parser_uses_threaded_pipeline_options_without_ocr(self):
        parser = PDFParser(ParserParams(file_path=Path("sample.pdf")))

        self.assertIsInstance(parser.pipeline_options, ThreadedPdfPipelineOptions)
        self.assertFalse(parser.pipeline_options.do_ocr)


class PDFVLMParserOptionsTest(unittest.TestCase):
    def test_vlm_parser_uses_deterministic_markdown_page_conversion(self):
        parser = PDFVLMParser(Path("sample.pdf"))

        parser.set_converter_options()

        vlm_options = parser.pipeline_options.vlm_options
        self.assertEqual(vlm_options.response_format, ResponseFormat.MARKDOWN)
        self.assertEqual(vlm_options.temperature, 0.0)
        self.assertEqual(vlm_options.scale, 2.0)
        self.assertTrue(parser.pipeline_options.generate_page_images)
        self.assertNotIn("Жду", vlm_options.prompt)


class ImageParserOptionsTest(unittest.TestCase):
    def test_image_parser_uses_deterministic_markdown_conversion(self):
        parser = ImageParser(Path("sample.png"))

        parser._set_converter_options()

        vlm_options = parser.pipeline_options.vlm_options
        self.assertEqual(vlm_options.response_format, ResponseFormat.MARKDOWN)
        self.assertEqual(vlm_options.temperature, 0.0)
        self.assertEqual(vlm_options.scale, 2.0)

    def test_image_parser_returns_timeout_fallback_for_embedded_elements(self):
        parser = ImageParser(Path("sample.png"))

        with patch.object(ImageParser, "parse", side_effect=TimeoutError()):
            parsed_text = parser.parse_image_for_element(object())

        self.assertIn("Время ожидания", parsed_text)


class HTMLParserOptionsTest(unittest.TestCase):
    def test_html_parser_uses_convert_pipeline_options(self):
        parser = HTMLParser(ParserParams(file_path=Path("sample.html")))

        self.assertIs(type(parser.pipeline_options), ConvertPipelineOptions)


if __name__ == "__main__":
    unittest.main()
