from typing import Optional, Union
from pathlib import Path
import requests
from abc import ABC, abstractmethod
from PIL import Image

from loguru import logger
from docling.document_converter import DocumentConverter
from docling.pipeline.vlm_pipeline import VlmPipeline
from docling_core.types.doc import (
    ImageRef
)
from docling.document_converter import (
    DocumentConverter,
    PdfFormatOption,
    ImageFormatOption,
    PowerpointFormatOption,
)
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.datamodel.pipeline_options_vlm_model import ApiVlmOptions, ResponseFormat
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import (
    VlmPipelineOptions,
)

from settings import settings
from modules.parser.v1.schemas import DocLingAPIVLMOptionsParams, FileFormats
from modules.parser.v1.exceptions import ContentNotSupportedError


class Parser(ABC):
    def __init__(
        self,
        source_file: Path
    ):
        self.source_file = source_file
        self.artifacts_path=Path(__file__).parent.parent.parent.parent.parent.joinpath("ml")
        self.converter = DocumentConverter()

    def parse_with_docling(self, file_path: Path) -> str:
        try:
            doc = self.converter.convert(file_path).document
            logger.debug("Standard Docling conversion completed")
            for item in doc.iterate_items(traverse_pictures=True):
                logger.warning(item)
            markdown = doc.export_to_markdown()
            logger.debug(markdown)
            return markdown
        except Exception as e:
            logger.error(f"Error converting document with Docling: {e}")
            raise e
        
    @abstractmethod
    def parse(self):
        pass

class PDFParser(Parser):
    def __init__(self, source_file: Path):
        super().__init__(source_file)
        self.converter = DocumentConverter()
        self.pipeline_options = PdfPipelineOptions(artifacts_path=self.artifacts_path, 
                                                   generate_parsed_pages=True, 
                                                   do_ocr=False)

    def set_converter_options(self):
        self.converter = DocumentConverter(format_options={
                InputFormat.PDF: PdfFormatOption(pipeline_options=self.pipeline_options)
                })
    def parse(self):
        self.set_converter_options()
        doc = self.converter.convert(self.source_file).document
        for item in doc.iterate_items():
            logger.warning(item)
        markdown = doc.export_to_markdown()
        return markdown

class ImageParser(Parser):
    def __init__(self, source_file: Union[Path| bytes], 
                vlm_base_url: str = settings.VLM_BASE_URL, 
                vlm_model_name: str = settings.VLM_MODEL_NAME, 
                vlm_api_key: Optional[str] = settings.VLM_API_KEY):
        
        self.source_file = source_file
        self.vlm_base_url = vlm_base_url
        self.vlm_model_name = vlm_model_name
        self.vlm_api_key = vlm_api_key
        self.pipeline_options = VlmPipelineOptions(enable_remote_services=True,
                                                   do_picture_classification=True,
                                                   generate_page_images=True)
        
    def openai_compatible_vlm_options(
                        self,
                        prompt: str,
                        format: ResponseFormat = ResponseFormat.MARKDOWN,
                        temperature: float = 0.7,
                        max_tokens: int = 8000,
                        skip_special_tokens=False,
                    ):
        headers = dict(Authorization=f"Bearer {self.vlm_api_key}")
        api_vlm_params = DocLingAPIVLMOptionsParams(
                            model=self.vlm_model_name,
                            max_tokens=max_tokens,
                            skip_special_tokens=skip_special_tokens,
                        ).model_dump()

        options = ApiVlmOptions(
                    url=f"http://{self.vlm_base_url}/v1/chat/completions",
                    params=api_vlm_params,
                    headers=headers,
                    prompt=prompt,
                    timeout=25,
                    scale=1.5,
                    temperature=temperature,
                    response_format=format,
                )
        return options

    def get_prompt(self):
        prompt = """
                You are an expert at converting documents, especially academic and technical materials, into perfectly formatted markdown. 
                Analyze the given image and convert it into markdown while preserving all structural, formatting, and semantic elements.
                **CRITICAL INSTRUCTIONS:**

                **1. Hierarchy & Text:**
                - Use `#`, `##`, `###` for titles and headings.
                - Preserve paragraphs and line breaks.
                - Use `**bold**`, `*italic*`, and `` `code` `` for inline elements.

                **2. Lists:**
                - Convert bullet points into `-` items.
                - Convert numbered lists into `1.`, `2.` items.

                **3. Tables (VERY IMPORTANT):**
                - **Identify all tables.** Recreate them using Markdown pipe syntax.
                - **Alignment:** Use `:---` for left, `:---:` for center, `---:` for right alignment.
                - **Header separation:** Ensure the header row is separated with `|---` lines.
                - **Complex tables:** If a table has multiline cells or complex formatting, 
                    do your best to represent it clearly, using `<br/>` for line breaks within cells if necessary.

                **4. Mathematical Formulas (VERY IMPORTANT):**
                - **Inline formulas:** Convert them using `$` delimiters (e.g., `$E = mc^2$`).
                - **Block equations:** Convert them using `$$` delimiters on separate lines.
                - **If you cannot convert to LaTeX accurately:** Describe the formula in plain text within parentheses, e.g., `(Formula: Integral from a to b of f(x) dx)`.

                **5. Code Blocks:**
                - Place code in triple backticks with language specifier: ````python`, ````javascript`, etc.

                **6. Accuracy:**
                - **Do not invent or omit information.** If something is unclear, represent it as best you can and add a comment like `[?]`.
                - Preserve the original order and structure meticulously.

                **Output Requirements:**
                Provide **only the raw markdown text**, ready to be saved as a `.md` file. 
                Do not add any introductory text like "Here is the markdown:" or explanatory comments before/after the content.
                 """
        return prompt
    
    def set_converter_options(self):
        logger.debug("Setting VLM Options...")
        prompt = self.get_prompt()
        self.pipeline_options.vlm_options = self.openai_compatible_vlm_options(
                                                prompt=prompt, format=ResponseFormat.MARKDOWN
                                            )
        self.converter = DocumentConverter(
                            format_options={
                                InputFormat.IMAGE: ImageFormatOption(
                                    pipeline_options=self.pipeline_options,
                                    pipeline_cls=VlmPipeline,
                                )
                            }
                        )
        
    def parse(self):
        self.set_converter_options()
        try:
            doc = self.converter.convert(self.source_file).document
            image_pil = Image.open(self.source_file)
            image = ImageRef.from_pil(image_pil, 300)
            doc.add_heading(text="Оригинал")
            doc.add_picture(image=image)
            markdown = doc.export_to_markdown()
            markdown
            logger.debug(markdown)
            return markdown
        except requests.exceptions.ConnectionError:
            logger.error(f"VLM is not available: {e}")
            raise 
        except Exception as e:
            logger.error(f"Error converting document: {e}")
            raise e
        
class PPTXParser(Parser):
    def __init__(self, source_file: Path):
        super().__init__(source_file)

    def parse(self):
        pass

class XLSXParser(Parser):
    def __init__(self, source_file: Path):
        super().__init__(source_file)

    def parse(self):
        pass

class HTMLParser(Parser):
    def __init__(self, source_file: Path):
        super().__init__(source_file)

    def parse(self):
        pass

class DocParser(Parser):
    def __init__(self, source_file: Path):
        super().__init__(source_file)

    def parse(self):
        pass


class ParserFactory():
    def __init__(self, source_file: Path):
        self.source_file = source_file

        self.IMAGE_FORMATS = FileFormats.IMAGE.value
        self.XLSX_FORMATS = FileFormats.XLSX.value
        self.DOC_FORMATS = FileFormats.DOC.value
        self.PDF_FORMATS = FileFormats.PDF.value
        self.PPTX_FORMATS = FileFormats.PPTX.value

    def get_parser(self):
        if not isinstance(self.source_file, Path):
            self.source_file = Path(self.source_file)
        source_file_format = self.source_file.suffix
        logger.success(f"Current file format: {source_file_format}")
        logger.debug("Creating Parser...")
        match source_file_format:
            case file_format if file_format in self.IMAGE_FORMATS:
                logger.debug("Image Parser Created!")
                return ImageParser(self.source_file)
            case file_format if file_format in self.XLSX_FORMATS:
                logger.debug("XLSX Parser Created!")
                return XLSXParser(self.source_file)
            case file_format if file_format in self.DOC_FORMATS:
                logger.debug("Doc Parser Created!")
                return DocParser(self.source_file)
            case file_format if file_format in self.PPTX_FORMATS:
                logger.debug("PPTX Parser Created!")
                return PPTXParser(self.source_file)
            case file_format if file_format in self.PDF_FORMATS:
                logger.debug("PDF Parser Created!")
                return PDFParser(self.source_file)
            case _:
                raise ContentNotSupportedError(f"Формат \"{source_file_format}\" не поддерживается!")
        


