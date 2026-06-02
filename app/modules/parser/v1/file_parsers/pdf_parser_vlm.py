from pathlib import Path
from typing import Optional, Union
from tempfile import NamedTemporaryFile

import pypandoc
from loguru import logger
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import (
    VlmPipelineOptions
)
from docling.datamodel.pipeline_options_vlm_model import (
    ApiVlmOptions, ResponseFormat
)
from docling.document_converter import (
    DocumentConverter, PdfFormatOption
)
from docling.pipeline.vlm_pipeline import VlmPipeline

from modules.parser.v1.abc.abc import ParserABC
from modules.parser.v1.schemas import ParserMods
from settings import settings
from modules.parser.v1.schemas import DocLingAPIVLMOptionsParams


class PDFVLMParser(ParserABC):
    def __init__(self, source_file: Union[Path| bytes], 
                vlm_base_url: str = settings.VLM_BASE_URL, 
                vlm_model_name: str = settings.VLM_MODEL_NAME, 
                vlm_api_key: Optional[str] = settings.VLM_API_KEY):
        
        self.source_file = source_file
        self.vlm_base_url = vlm_base_url
        self.vlm_model_name = vlm_model_name
        self.vlm_api_key = vlm_api_key
        self.pipeline_options = VlmPipelineOptions(enable_remote_services=True,
                                                   do_picture_classification=False,
                                                   artifacts_path=settings.ARTIFACTS_PATH,
                                                   generate_page_images=True,
                                                   do_picture_description=False
                                                   )
        self.artifacts_path = settings.ARTIFACTS_PATH
        self.converter = DocumentConverter()

    def _openai_compatible_vlm_options(
                        self,
                        prompt: str,
                        format: ResponseFormat = ResponseFormat.MARKDOWN,
                        temperature: float = 0.0,
                        max_tokens: int = 16000,
                        skip_special_tokens=False,
                    ):
        headers = dict(Authorization=f"Bearer {self.vlm_api_key}")
        api_vlm_params = DocLingAPIVLMOptionsParams(
                            model=self.vlm_model_name,
                            max_tokens=max_tokens
                        ).model_dump()

        options = ApiVlmOptions(
                    url=f"{self.vlm_base_url}/v1/chat/completions",
                    params=api_vlm_params,
                    headers=headers,
                    prompt=prompt,
                    timeout=settings.VLM_TIMEOUT_SECS,
                    scale=2.0,
                    temperature=temperature,
                    response_format=format,
                )
        return options
    
    def set_converter_options(self):
        prompt = self._get_prompt()
        self.pipeline_options.vlm_options = self._openai_compatible_vlm_options(
                                                prompt=prompt, format=ResponseFormat.MARKDOWN, max_tokens=settings.VLM_MAX_TOKENS
                                            )
        self.converter = DocumentConverter(format_options={
                InputFormat.PDF: PdfFormatOption(
                    pipeline_options=self.pipeline_options,                           
                    pipeline_cls=VlmPipeline)
                })
    
    def _get_prompt(self):
        prompt = """
Convert this PDF page to clean Markdown.

Rules:
1. Preserve the visible document structure: headings, paragraphs, lists, tables, code blocks, quotes, and links.
2. Preserve all visible text. Do not summarize, explain, translate, or add content.
3. Use Markdown syntax only for structure. Avoid decorative inline formatting inside normal paragraphs.
4. Represent tables as simple Markdown tables when possible.
5. Return only the final Markdown content, without preambles or comments.
"""
        return prompt
    
    def parse(self, mode: ParserMods):
        logger.debug(f"Parsing {self.source_file}...")
        self.set_converter_options()
        doc = self.converter.convert(self.source_file, raises_on_error=True).document
        markdown = doc.export_to_markdown()

        if not markdown.strip():
            logger.error("VLM failed silently: returned empty markdown")
        logger.success(f"Document converted!")
        logger.debug("Cleaning documents")

        match mode:
            case ParserMods.TO_FILE:
                logger.debug("Saving to .md file")
                with NamedTemporaryFile(suffix=".md", delete=False) as tmp_file:
                    doc.save_as_markdown(filename=tmp_file.name,artifacts_dir=self.artifacts_path)
                    logger.success("File Saved!")
                    return tmp_file.name
            case ParserMods.TO_TEXT:
                markdown = doc.export_to_markdown()
                return markdown
            case ParserMods.TO_DOCLING:
                return doc
            case ParserMods.TO_WORD:
                markdown = doc.export_to_markdown()
                with NamedTemporaryFile(suffix=".docx", delete=False) as tmp_file:
                    pypandoc.convert_text(markdown, "docx", "md", outputfile=tmp_file.name)
                    return tmp_file.name
            case _:
                logger.error("Unknown parse mode!")
                raise ValueError

        
