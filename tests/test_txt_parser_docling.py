import unittest
from pathlib import Path

from docling.datamodel.base_models import InputFormat
from docling.document_converter import MarkdownFormatOption

from modules.parser.v1.file_parsers.txt_parser import TXTParser
from modules.parser.v1.schemas import ParserParams


class TXTParserDoclingTest(unittest.TestCase):
    def test_txt_parser_uses_docling_markdown_format(self):
        parser = TXTParser(ParserParams(file_path=Path("sample.txt")))

        parser.set_converter_options()

        self.assertEqual(list(parser.converter.format_to_options.keys()), [InputFormat.MD])
        self.assertIsInstance(
            parser.converter.format_to_options[InputFormat.MD],
            MarkdownFormatOption,
        )


if __name__ == "__main__":
    unittest.main()
