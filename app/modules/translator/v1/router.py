from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse
from loguru import logger

from modules.parser.v1.schemas import FileFormats, ParserMods, ParserParams
from modules.parser.v1.utils import (
    delete_file,
    file_cleanup_task,
    parse_document,
    run_in_process,
    save_file,
)
from modules.translator.v1.schemas import TranslatorRequest, TranslatorTextResponse
from modules.translator.v1.service import CustomModelTranslator
from settings import settings

router = APIRouter(prefix="/api/v1/parser")


@router.post(
    path="/translator/text",
    name="Синхронный перевод документа в Markdown-текст",
    summary="Перевести документ и вернуть Markdown-текст",
    description=f"""
## Назначение
Парсит документ, переводит текст и возвращает результат в одном HTTP-ответе.

### Поддерживаемые MIME-типы
```python
{settings.ALLOWED_MIME_TYPES}
```

### Поддерживаемые форматы
* **DOC**: `{FileFormats.DOC.value}`
* **PDF**: `{FileFormats.PDF.value}`
* **XLSX**: `{FileFormats.XLSX.value}`
* **IMAGES**: `{FileFormats.IMAGE.value}`
* **HTML**: `{FileFormats.HTML.value}`
* **PPTX**: `{FileFormats.PPTX.value}`
* **TXT**: `{FileFormats.TXT.value}`

### Важные параметры
1. `source_language` — исходный язык документа или `auto`.
2. `target_language` — целевой язык в формате `iso639-1`.
3. `parse_images`, `include_image_in_output`, `full_vlm_pdf_parse` — параметры парсинга перед переводом.

### Возвращаемый объект
```python
{TranslatorTextResponse.model_fields}
```
""",
    tags=["Translator V1"],
)
async def translate_file_to_text(
    request_fastapi: Request,
    translator_data: TranslatorRequest = Depends(),
) -> TranslatorTextResponse:
    file_path: Path | None = None
    try:
        source_language = translator_data.source_language
        target_language = translator_data.target_language
        logger.debug(
            "TranslatorV1: incoming request source='{}' target='{}'",
            source_language,
            target_language,
        )
        file = translator_data.file
        file_path = await save_file(file)
        parser_params = ParserParams(
            file_path=file_path,
            parse_images=translator_data.parse_images,
            include_image_in_output=translator_data.include_image_in_output,
            full_vlm_pdf_parse=translator_data.full_vlm_pdf_parse,
        )

        parsed = await run_in_process(
            parse_document,
            request_fastapi.app.state.executor,
            parser_params,
            ParserMods.TO_DOCLING,
            semaphore=request_fastapi.app.state.parser_semaphore,
        )
        translator = CustomModelTranslator(
            parsed,
            source_language,
            target_language,
            translator_data.include_image_in_output,
            max_concurrency=settings.TRANSLATOR_MAX_CONCURRENCY,
            shared_semaphore=getattr(
                request_fastapi.app.state,
                "translation_semaphore",
                None,
            ),
            http_session=getattr(request_fastapi.app.state, "http_session", None),
        )
        translated = await translator.translate_docling(ParserMods.TO_TEXT, parsed)
        return TranslatorTextResponse(parsed_text=translated)
    except Exception as e:
        logger.error(f"Synchronous translation into text failed: {e}")
        raise
    finally:
        await delete_file(file_path)


@router.post(
    path="/translator/file",
    name="Синхронный перевод документа в Markdown-файл",
    summary="Перевести документ и вернуть `.md`-файл",
    description=f"""
## Назначение
Парсит документ, переводит текст и возвращает результат в виде `.md`-файла.

### Поддерживаемые MIME-типы
```python
{settings.ALLOWED_MIME_TYPES}
```

### Поддерживаемые форматы
* **DOC**: `{FileFormats.DOC.value}`
* **PDF**: `{FileFormats.PDF.value}`
* **XLSX**: `{FileFormats.XLSX.value}`
* **IMAGES**: `{FileFormats.IMAGE.value}`
* **HTML**: `{FileFormats.HTML.value}`
* **PPTX**: `{FileFormats.PPTX.value}`
* **TXT**: `{FileFormats.TXT.value}`

### Важные параметры
1. `source_language` — исходный язык документа или `auto`.
2. `target_language` — целевой язык в формате `iso639-1`.
3. `parse_images`, `include_image_in_output`, `full_vlm_pdf_parse` — параметры парсинга перед переводом.

### Возвращаемый объект
```python
{FileResponse}
```
""",
    tags=["Translator V1"],
)
async def translate_file_to_file(
    request_fastapi: Request,
    translator_data: TranslatorRequest = Depends(),
):
    file_path: Path | None = None
    translated_path: str | None = None
    try:
        source_language = translator_data.source_language
        target_language = translator_data.target_language
        logger.debug(
            "TranslatorV1: incoming request source='{}' target='{}'",
            source_language,
            target_language,
        )
        file = translator_data.file
        download_stem = Path(file.filename or "document").stem
        file_path = await save_file(file)
        parser_params = ParserParams(
            file_path=file_path,
            parse_images=translator_data.parse_images,
            include_image_in_output=translator_data.include_image_in_output,
            full_vlm_pdf_parse=translator_data.full_vlm_pdf_parse,
        )

        parsed = await run_in_process(
            parse_document,
            request_fastapi.app.state.executor,
            parser_params,
            ParserMods.TO_DOCLING,
            semaphore=request_fastapi.app.state.parser_semaphore,
        )
        translator = CustomModelTranslator(
            parsed,
            source_language,
            target_language,
            translator_data.include_image_in_output,
            max_concurrency=settings.TRANSLATOR_MAX_CONCURRENCY,
            shared_semaphore=getattr(
                request_fastapi.app.state,
                "translation_semaphore",
                None,
            ),
            http_session=getattr(request_fastapi.app.state, "http_session", None),
        )
        translated_path = await translator.translate_docling(ParserMods.TO_FILE, parsed)
        return FileResponse(
            path=translated_path,
            filename=f"{download_stem}(переведенный).md",
            background=file_cleanup_task(translated_path),
        )
    except Exception as e:
        logger.error(f"Synchronous translation into .md failed: {e}")
        if translated_path is not None:
            await delete_file(translated_path)
        raise
    finally:
        await delete_file(file_path)


@router.post(
    path="/translator/file/word",
    name="Синхронный перевод документа в Word-файл",
    summary="Перевести документ и вернуть `.docx`-файл",
    description=f"""
## Назначение
Парсит документ, переводит текст и возвращает результат в виде `.docx`-файла.

### Поддерживаемые MIME-типы
```python
{settings.ALLOWED_MIME_TYPES}
```

### Поддерживаемые форматы
* **DOC**: `{FileFormats.DOC.value}`
* **PDF**: `{FileFormats.PDF.value}`
* **XLSX**: `{FileFormats.XLSX.value}`
* **IMAGES**: `{FileFormats.IMAGE.value}`
* **HTML**: `{FileFormats.HTML.value}`
* **PPTX**: `{FileFormats.PPTX.value}`
* **TXT**: `{FileFormats.TXT.value}`

### Важные параметры
1. `source_language` — исходный язык документа или `auto`.
2. `target_language` — целевой язык в формате `iso639-1`.
3. `parse_images`, `include_image_in_output`, `full_vlm_pdf_parse` — параметры парсинга перед переводом.

### Возвращаемый объект
```python
{FileResponse}
```
""",
    tags=["Translator V1"],
)
async def translate_file_to_word(
    request_fastapi: Request,
    translator_data: TranslatorRequest = Depends(),
):
    file_path: Path | None = None
    translated_path: str | None = None
    try:
        source_language = translator_data.source_language
        target_language = translator_data.target_language
        logger.debug(
            "TranslatorV1: incoming request source='{}' target='{}'",
            source_language,
            target_language,
        )
        file = translator_data.file
        download_stem = Path(file.filename or "document").stem
        file_path = await save_file(file)
        parser_params = ParserParams(
            file_path=file_path,
            parse_images=translator_data.parse_images,
            include_image_in_output=translator_data.include_image_in_output,
            full_vlm_pdf_parse=translator_data.full_vlm_pdf_parse,
        )

        parsed = await run_in_process(
            parse_document,
            request_fastapi.app.state.executor,
            parser_params,
            ParserMods.TO_DOCLING,
            semaphore=request_fastapi.app.state.parser_semaphore,
        )
        translator = CustomModelTranslator(
            parsed,
            source_language,
            target_language,
            translator_data.include_image_in_output,
            max_concurrency=settings.TRANSLATOR_MAX_CONCURRENCY,
            shared_semaphore=getattr(
                request_fastapi.app.state,
                "translation_semaphore",
                None,
            ),
            http_session=getattr(request_fastapi.app.state, "http_session", None),
        )
        translated_path = await translator.translate_docling(ParserMods.TO_WORD, parsed)
        return FileResponse(
            path=translated_path,
            filename=f"{download_stem}(переведенный).docx",
            background=file_cleanup_task(translated_path),
        )
    except Exception as e:
        logger.error(f"Synchronous translation into .docx failed: {e}")
        if translated_path is not None:
            await delete_file(translated_path)
        raise
    finally:
        await delete_file(file_path)
