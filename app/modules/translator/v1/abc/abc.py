from abc import ABC, abstractmethod
from docling_core.types.doc import (
    ImageRefMode
)
from settings import settings

class AbstractTranslator(ABC):
    def __init__(self, source_text: str, source_language: str, target_language: str, include_image_in_output: bool):
        super().__init__()
        self.source_text = source_text
        self.source_language = source_language
        self.target_language = target_language
        self.artifacts_path=settings.ARTIFACTS_PATH
        self.include_image_in_output =  include_image_in_output
        self.image_mode = ImageRefMode.EMBEDDED if self.include_image_in_output else ImageRefMode.PLACEHOLDER
        self.page_break_placeholder = "\n\n---\n\n"

    @abstractmethod
    def translate(self, source_text):
        pass