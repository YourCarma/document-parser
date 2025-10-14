from typing import Optional, Union
from pathlib import Path
from PIL import Image
from io import BytesIO
import requests
from uuid import uuid4
from tempfile import NamedTemporaryFile

from docling.pipeline.vlm_pipeline import VlmPipeline
from docling.document_converter import DocumentConverter
from docling.datamodel.pipeline_options import VlmPipelineOptions
from docling.datamodel.pipeline_options_vlm_model import ApiVlmOptions, ResponseFormat
from docling.datamodel.base_models import DocumentStream, InputFormat
from docling.document_converter import DocumentConverter, ImageFormatOption
from docling.backend.pypdfium2_backend import PyPdfiumDocumentBackend
from loguru import logger

from modules.parser.v1.abc.abc import ParserABC
from settings import settings
from modules.parser.v1.schemas import DocLingAPIVLMOptionsParams, ParserMods
from modules.parser.v1.exceptions import ServiceUnavailable, TimeoutError


class ImageParser(ParserABC):
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
                                                   generate_page_images=True,
                                                   do_picture_description=False
                                                   )
        self.converter = DocumentConverter()

    def convert_image_to_bytes_io(self, image: Union[Path, str, Image.Image]):
        logger.debug(image)
        logger.info("Need convert?")
        if isinstance(image, (Path, str)):
            logger.info("Convert not needed")
            return image
        logger.info("Convert to DocumentStream...")
        imgByteArr = BytesIO()
        image.save(imgByteArr, format='png')
        imgByteArr = imgByteArr.getvalue()
        stream = DocumentStream(name=f"{uuid4()}.png", stream=BytesIO(imgByteArr))
        logger.success("DocumentStreamObject created!")
        return stream

    def _openai_compatible_vlm_options(
                        self,
                        prompt: str,
                        format: ResponseFormat = ResponseFormat.MARKDOWN,
                        temperature: float = 0.7,
                        max_tokens: int = 20000,
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
                    scale=2,
                    temperature=temperature,
                    response_format=format,
                )
        return options

    def _get_prompt(self):
        prompt = """
            You are an expert at converting documents, especially academic and technical materials, into perfectly formatted markdown.
        Your primary goal is to capture 100% of the textual and structural content from the provided image.   
        CRITICAL INSTRUCTIONS: 
        TEXT DETECTION FIRST:
        Before any processing, determine whether the image contains any human-readable text (letters, digits, symbols that form words, equations, labels, etc.).
        → If the image contains no text whatsoever 
        (e.g., it's a logo, icon, diagram without labels, pure illustration, or decorative graphic), output an empty string — nothing else. 
        COMPLETE TEXT EXTRACTION (ONLY IF TEXT IS PRESENT):
        If text is detected, extract every word, number, and symbol. Pay special attention to headers, footers, side notes, captions, and text in complex layouts.
        Reconstruct logical reading order (typically top-to-bottom, left-to-right).
        Your absolute priority is to preserve all information. Formatting is secondary to completeness. 
        Hierarchy & Text:
        Use #, ##, ### for titles and headings.
        Preserve paragraphs and line breaks.
        Use **bold**, *italic*, and `code` for inline elements. 
        Lists:
        Convert bullet points into - items.
        Convert numbered lists into 1., 2. items. 
        Tables (VERY IMPORTANT):
        Identify all tables. Recreate them using Markdown pipe syntax.
        Alignment: :--- (left), :---: (center), ---: (right).
        Separate header with |---| line.
        For multiline cells, use <br/> if needed. 
        Mathematical Formulas (VERY IMPORTANT):
        Inline: $...$
        Block: $$...$$ on separate lines.
        If LaTeX is uncertain, describe plainly: (Formula: ...). 
        Code Blocks:
        Use triple backticks with language: python, javascript, etc. 
        Accuracy and Handling Uncertainty:
        Never invent or omit. Mark unclear parts as [?] or [illegible].
        Preserve original order and structure meticulously. 
        NO IMAGE DESCRIPTIONS:
        Never describe visual elements (colors, layout, shapes) unless they contain text.
        If there’s no text — output nothing. 
        Output Requirements:   
        If no text is present: return an empty string.  
        If text is present: output only raw FLAT markdown, ready for a .md file.  
        Never add introductions, explanations, or comments before/after the content
                 """
        return prompt
    
    def _set_converter_options(self):
        logger.debug("Setting VLM Options...")
        prompt = self._get_prompt()
        self.pipeline_options.vlm_options = self._openai_compatible_vlm_options(
                                                prompt=prompt, format=ResponseFormat.MARKDOWN
                                            )
        self.converter = DocumentConverter(
                            format_options={
                                InputFormat.IMAGE: ImageFormatOption(
                                    pipeline_options=self.pipeline_options,
                                    backend=PyPdfiumDocumentBackend,
                                    pipeline_cls=VlmPipeline
                                )
                            }
                        )
        
    def parse(self, mode: ParserMods):
        logger.debug("Parsing Image...")
        self._set_converter_options()
        try:
            self.source_file = self.convert_image_to_bytes_io(self.source_file)
            doc = self.converter.convert(self.source_file).document
            markdown = doc.export_to_markdown(image_mode=self.image_mode)
            logger.success("Document have been parsed!")
            if mode == ParserMods.TO_FILE.value:
                logger.debug("Saving to .md file")
                with NamedTemporaryFile(suffix=".md", delete=False) as tmp_file:
                    doc.save_as_markdown(filename=tmp_file.name,artifacts_dir=self.artifacts_path, image_mode=self.image_mode)
                    logger.success("File Saved!")
                    return tmp_file.name
            else: 
                return markdown
        except requests.exceptions.ConnectionError as e:
            logger.error(f"VLM is not available: {e}")
            raise ServiceUnavailable("VLM", settings.VLM_BASE_URL)
        except requests.exceptions.HTTPError as e:
            logger.error(f"VLM is not available: {e}")
            raise ServiceUnavailable("VLM", settings.VLM_BASE_URL)
        except requests.exceptions.ReadTimeout as e:
            logger.error(f"Timeout error!")
            raise TimeoutError()
        except ValueError as e:
            logger.warning(f"Can't append child (Docling Error): {e}")
            return ""
        except AttributeError as e:
            logger.warning("Image not found...")
            return ""
        except Exception as e:
            logger.error(f"Error converting document: {e}")
            raise e

    def parse_image_for_element(self, image: Image):
        try:
            parsed_text = ImageParser(image).parse()
        except TimeoutError as e:
            logger.warning("Timeout error on image parsing...")
        except Exception as e:
            logger.error(f"Error while parsing element: {e}")
            parsed_text = "*При парсинге изображения возникла задержка сети*"
        return parsed_text