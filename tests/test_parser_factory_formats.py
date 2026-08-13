import unittest
from pathlib import Path
from unittest.mock import patch

from modules.parser.v1.abc.factory import ParserFactory
from modules.parser.v1.file_parsers import (
    EMLParser,
    EPUBParser,
    ODPParser,
    ODSParser,
    ODTParser,
    TXTParser,
    XBRLParser,
    DocParser,
    PPTXParser,
    XLSXParser,
)
from modules.parser.v1.schemas import FileFormats, ParserParams
from modules.parser.v1.utils import SUPPORTED_EXTENSIONS, is_supported_extension


class SupportedExtensionsTest(unittest.TestCase):
    def test_is_supported_extension_accepts_every_known_format(self):
        for file_format in FileFormats:
            for extension in file_format.value:
                with self.subTest(extension=extension):
                    self.assertIn(extension.lower(), SUPPORTED_EXTENSIONS)
                    self.assertTrue(is_supported_extension(f"документ{extension}"))
                    self.assertTrue(
                        is_supported_extension(Path("/tmp") / f"файл{extension.upper()}")
                    )

    def test_is_supported_extension_rejects_unknown_and_extensionless(self):
        for file_name in ["archive.zip", "binary.exe", "README", "", ".", "no_suffix."]:
            with self.subTest(file_name=file_name):
                self.assertFalse(is_supported_extension(file_name))


class ParserFactoryFormatsTest(unittest.TestCase):
    def test_native_office_formats_are_not_reconverted(self):
        cases = [
            ("sample.docx", DocParser),
            ("sample.xlsx", XLSXParser),
            ("sample.pptx", PPTXParser),
        ]

        for filename, expected_parser in cases:
            with self.subTest(filename=filename):
                params = ParserParams(file_path=Path(filename))
                with patch("modules.parser.v1.abc.factory.convert_doc_to") as convert_doc_to:
                    parser = ParserFactory(params).get_parser()

                convert_doc_to.assert_not_called()
                self.assertIsInstance(parser, expected_parser)
                self.assertEqual(params.file_path, Path(filename))

    def test_open_document_formats_are_not_converted_through_office_formats(self):
        cases = [
            ("sample.odt", ODTParser),
            ("sample.ods", ODSParser),
            ("sample.odp", ODPParser),
        ]

        for filename, expected_parser in cases:
            with self.subTest(filename=filename):
                params = ParserParams(file_path=Path(filename))
                with patch("modules.parser.v1.abc.factory.convert_doc_to") as convert_doc_to:
                    parser = ParserFactory(params).get_parser()

                convert_doc_to.assert_not_called()
                self.assertIsInstance(parser, expected_parser)
                self.assertEqual(params.file_path, Path(filename))

    def test_docling_native_formats_are_routed_directly(self):
        cases = [
            ("sample.md", TXTParser),
            ("sample.qmd", TXTParser),
            ("sample.Rmd", TXTParser),
            ("sample.epub", EPUBParser),
            ("sample.eml", EMLParser),
            ("sample.xbrl", XBRLParser),
            ("sample.xml", XBRLParser),
        ]

        for filename, expected_parser in cases:
            with self.subTest(filename=filename):
                params = ParserParams(file_path=Path(filename))
                with patch("modules.parser.v1.abc.factory.convert_doc_to") as convert_doc_to:
                    parser = ParserFactory(params).get_parser()

                convert_doc_to.assert_not_called()
                self.assertIsInstance(parser, expected_parser)


if __name__ == "__main__":
    unittest.main()
