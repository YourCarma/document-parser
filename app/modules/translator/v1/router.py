from datetime import timezone, datetime
from typing import List, Dict
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from loguru import logger
from fastapi.responses import FileResponse

from settings import settings
from modules.translator.v1.schemas import TranslatorRequest, TranslatorTextResponse
from modules.parser.v1.abc.factory import ParserFactory
from modules.parser.v1.utils import save_file, delete_file, run_in_process
from modules.translator.v1.service import CustomModelTranslator
from modules.parser.v1.schemas import ParserMods, ParserParams

router = APIRouter(prefix="/api/v1/parser")

@router.post(path="/translator/text",
             name="Translating to text",
             summary="Translating files to MD text",
             description=f"""   
    """,
             tags=['Translator'])
async def translate_file_to_text(request_fastapi: Request, translator_data: TranslatorRequest = Depends()) -> TranslatorTextResponse:
    try:
        source_language = translator_data.source_language
        target_language = translator_data.target_language
        logger.debug(f"Source language: {source_language}")
        logger.debug(f"Target language: {target_language}")
        file = translator_data.file
        file_path = await save_file(file)
        parser_params = ParserParams(
            file_path=file_path,
            parse_images=translator_data.parse_images,
            include_image_in_output=translator_data.include_image_in_output)
        
        parser = ParserFactory(parser_params).get_parser()
        text = await run_in_process(parser.parse, request_fastapi.app.state.executor, ParserMods.TO_TEXT)
        translator = CustomModelTranslator(text, source_language, target_language, translator_data.include_image_in_output)
        transalted = await translator.translate(ParserMods.TO_TEXT)
        instance = TranslatorTextResponse(parsed_text=transalted)
        return instance
    except Exception as e:
        logger.error(f"Error in request: {e}")
        raise e
    finally:
        await delete_file(file_path)

@router.post(path="/translator/file",
             name="Transalting file",
             summary="Translating to MD file",
             description=f"""   
    """,
             tags=['Translator'])
async def translate_file_to_text(request_fastapi: Request, translator_data: TranslatorRequest = Depends()) -> TranslatorTextResponse:
    try:
        source_language = translator_data.source_language
        target_language = translator_data.target_language
        logger.debug(f"Source language: {source_language}")
        logger.debug(f"Target language: {target_language}")
        file = translator_data.file
        file_path = await save_file(file)
        parser_params = ParserParams(
            file_path=file_path,
            parse_images=translator_data.parse_images,
            include_image_in_output=translator_data.include_image_in_output)
        
        parser = ParserFactory(parser_params).get_parser()
        text = await run_in_process(parser.parse, request_fastapi.app.state.executor, ParserMods.TO_TEXT)
        translator = CustomModelTranslator(text, source_language, target_language, translator_data.include_image_in_output)
        transalted_path = await translator.translate(ParserMods.TO_FILE)
        return FileResponse(path=transalted_path, filename=str(Path(file_path).stem + ".md"))
        return instance
    except Exception as e:
        logger.error(f"Error in request: {e}")
        raise e
    finally:
        await delete_file(file_path)


