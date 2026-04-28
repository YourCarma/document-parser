from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse
from loguru import logger

from modules.parser.v1.abc.factory import ParserFactory
from modules.parser.v1.schemas import (
    FileFormats,
    ParserMods,
    ParserParams,
    ParserRequest,
    ParserTextResponse,
)
from modules.parser.v1.utils import delete_file, run_in_process, save_file
from settings import settings

router = APIRouter(prefix="/api/v1/parser")


@router.post(
    path="/parse/text",
    name="Парсинг документа в Markdown-текст",
    summary="Распознать документ и вернуть Markdown-текст",
    description=f"""
## Назначение
Синхронно распознаёт документ и возвращает содержимое в виде Markdown-строки.

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
1. `parse_images` — распознавать встроенные изображения через VLM.
2. `include_image_in_output` — включать изображения в итоговый Markdown.
3. `full_vlm_pdf_parse` — отправлять PDF целиком в VLM вместо стандартного пайплайна.

### Возвращаемый объект
```python
{ParserTextResponse.model_fields}
```
""",
    tags=["Parser V1"],
)
async def parse_to_text(
    request_fastapi: Request,
    parser_data: ParserRequest = Depends(),
) -> ParserTextResponse:
    try:
        file = parser_data.file
        file_path = await save_file(file)
        parser_params = ParserParams(
            file_path=file_path,
            parse_images=parser_data.parse_images,
            include_image_in_output=parser_data.include_image_in_output,
            full_vlm_pdf_parse=parser_data.full_vlm_pdf_parse,
        )
        parser = ParserFactory(parser_params).get_parser()
        text = await run_in_process(
            parser.parse,
            request_fastapi.app.state.executor,
            ParserMods.TO_TEXT,
        )
        return ParserTextResponse(parsed_text=text)
    except Exception as e:
        logger.error(f"Ошибка парсинга документа в текст: {e}")
        raise e
    finally:
        await delete_file(file_path)


@router.post(
    path="/parse/file",
    name="Парсинг документа в Markdown-файл",
    summary="Распознать документ и вернуть `.md`-файл",
    description=f"""
## Назначение
Синхронно распознаёт документ и возвращает результат в виде `.md`-файла.

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
1. `parse_images` — распознавать встроенные изображения через VLM.
2. `include_image_in_output` — включать изображения в итоговый Markdown.
3. `full_vlm_pdf_parse` — отправлять PDF целиком в VLM вместо стандартного пайплайна.

### Возвращаемый объект
```python
{FileResponse}
```
""",
    tags=["Parser V1"],
)
async def parse_to_file(
    request_fastapi: Request,
    parser_data: ParserRequest = Depends(),
) -> FileResponse:
    try:
        file = parser_data.file
        file_path = await save_file(file)
        parser_params = ParserParams(
            file_path=file_path,
            parse_images=parser_data.parse_images,
            include_image_in_output=parser_data.include_image_in_output,
            full_vlm_pdf_parse=parser_data.full_vlm_pdf_parse,
        )
        file = await run_in_process(
            ParserFactory(parser_params).get_parser().parse,
            request_fastapi.app.state.executor,
            ParserMods.TO_FILE,
        )
        return FileResponse(path=file, filename=str(Path(file_path).stem + ".md"))
    except Exception as e:
        logger.error(f"Ошибка парсинга документа в .md: {e}")
        raise e
    finally:
        await delete_file(file_path)


@router.post(
    path="/parse/file/word",
    name="Парсинг документа в Word-файл",
    summary="Распознать документ и вернуть `.docx`-файл",
    description=f"""
## Назначение
Синхронно распознаёт документ и возвращает результат в виде `.docx`-файла.

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
1. `parse_images` — распознавать встроенные изображения через VLM.
2. `include_image_in_output` — включать изображения в итоговый Markdown.
3. `full_vlm_pdf_parse` — отправлять PDF целиком в VLM вместо стандартного пайплайна.

### Возвращаемый объект
```python
{FileResponse}
```
""",
    tags=["Parser V1"],
)
async def parse_to_word_file(
    request_fastapi: Request,
    parser_data: ParserRequest = Depends(),
) -> FileResponse:
    try:
        file = parser_data.file
        file_path = await save_file(file)
        parser_params = ParserParams(
            file_path=file_path,
            parse_images=parser_data.parse_images,
            include_image_in_output=parser_data.include_image_in_output,
            full_vlm_pdf_parse=parser_data.full_vlm_pdf_parse,
        )
        file = await run_in_process(
            ParserFactory(parser_params).get_parser().parse,
            request_fastapi.app.state.executor,
            ParserMods.TO_WORD,
        )
        return FileResponse(path=file, filename=str(Path(file_path).stem + ".docx"))
    except Exception as e:
        logger.error(f"Ошибка парсинга документа в .docx: {e}")
        raise e
    finally:
        await delete_file(file_path)
