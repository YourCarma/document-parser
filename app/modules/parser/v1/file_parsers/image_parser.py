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
from docling.backend.image_backend import ImageDocumentBackend
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
                                                   do_picture_classification=True,
                                                   generate_page_images=True,
                                                   do_picture_description=False
                                                   )
        self.artifacts_path = settings.ARTIFACTS_PATH
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
                        max_tokens: int = 32000,
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
            Convert to Markdown.
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
                                   backend=ImageDocumentBackend,
                                    pipeline_cls=VlmPipeline
                                )
                            }
                        )
        
    def parse(self, mode: ParserMods = ParserMods.TO_TEXT):
        logger.debug("Parsing Image...")
        self._set_converter_options()
        try:
            self.source_file = self.convert_image_to_bytes_io(self.source_file)
            doc = self.converter.convert(self.source_file).document
            logger.success("Document have been parsed!")
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
                case _:
                    logger.error("Unknown parse mode!")
                    raise ValueError
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
            logger.warning(f"Image not found: {e}")
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