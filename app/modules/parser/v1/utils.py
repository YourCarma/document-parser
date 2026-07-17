from pathlib import Path
import tempfile
import os
from typing import Union, Optional
import asyncio
import subprocess

from loguru import logger
from fastapi import UploadFile
from starlette.background import BackgroundTask

from modules.parser.v1.schemas import ConvertationOutputs, ParserMods, ParserParams


UPLOAD_CHUNK_SIZE = 1024 * 1024


async def save_file(file: UploadFile) -> Path:
    temp_path: Optional[Path] = None
    descriptor: int | None = None
    try:
        # Only the suffix is retained from the untrusted client filename. The
        # operating system creates the destination atomically, so concurrent
        # uploads with identical names cannot overwrite each other.
        file_suffix = Path(file.filename or "").suffix
        descriptor, raw_temp_path = tempfile.mkstemp(
            prefix="document_parser_",
            suffix=file_suffix,
            dir=tempfile.gettempdir(),
        )
        temp_path = Path(raw_temp_path)

        # Reuse the atomically-created descriptor. Writes remain bounded by the
        # chunk size, so even large uploads are never accumulated in memory.
        with os.fdopen(descriptor, "wb") as destination:
            descriptor = None
            while chunk := await file.read(UPLOAD_CHUNK_SIZE):
                destination.write(chunk)

        logger.success(f"File saved at: {temp_path}")
        return temp_path
    except Exception as e:
        logger.error(f"Error saving_file: {e}")
        if descriptor is not None:
            os.close(descriptor)
        await delete_file(temp_path)
        raise

async def delete_file(file_path: Union[Path, str, None]) -> None:
    if file_path is None:
        return

    try:
        logger.debug(f"Deleting \"{file_path}\" file")
        # unlink is a small metadata operation and safe to perform inline.
        Path(file_path).unlink()
        logger.success(f"File \"{file_path}\" succesfully deleted!")
    except FileNotFoundError:
        logger.debug(f"File \"{file_path}\" has already been deleted")
    except Exception as e:
        logger.error(f"Error on deleting \"{file_path}\" file: {e}")


def file_cleanup_task(file_path: Union[Path, str]) -> BackgroundTask:
    """Create cleanup which runs after a streaming response is sent."""
    return BackgroundTask(delete_file, file_path)


def parse_document(parser_params: ParserParams, mode: ParserMods):
    """Build and execute a parser in a worker process.

    The local import avoids a module cycle: the factory itself uses conversion
    helpers from this module. Keeping this function at module level also makes
    it picklable by ``ProcessPoolExecutor``.
    """
    from modules.parser.v1.abc.factory import ParserFactory

    original_path = Path(parser_params.file_path)
    parser = ParserFactory(parser_params).get_parser()
    converted_path = Path(parser_params.file_path)
    try:
        return parser.parse(mode)
    finally:
        if converted_path != original_path:
            converted_path.unlink(missing_ok=True)


def read_file_content(file_path: Path):
    try:
        with open(file_path, mode='r', encoding='utf-8') as file:
            contents = file.read()
            return contents
    except Exception as e:
        logger.error(f"Error on deleting \"{file_path}\" file: {e}")

async def run_in_process(fn, app_executor, *args, semaphore=None):
    loop = asyncio.get_running_loop()
    if semaphore is None:
        return await loop.run_in_executor(app_executor, fn, *args)
    async with semaphore:
        return await loop.run_in_executor(app_executor, fn, *args)

def convert_doc_to(
    input_file_path: Union[Path, str],
    output_format: str,
    output_dir: Optional[Union[Path, str]] = None,
) -> Path:
    input_path = Path(input_file_path).resolve()

    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    if output_dir is None:
        outdir = input_path.parent
    else:
        outdir = Path(output_dir).resolve()
        outdir.mkdir(parents=True, exist_ok=True)

    format_filters = {
        "docx": "MS Word 2007 XML",
        "xlsx": "Calc MS Excel 2007 XML",
        "pptx": "Impress MS PowerPoint 2007 XML",
    }

    filter_name = format_filters.get(output_format.lower())
    if not filter_name:
        raise ValueError(f"Unsupported output format: {output_format}")

    cmd = [
        "soffice",
        "--headless",
        "--nologo",
        "--nofirststartwizard",
        "--convert-to",
        f"{output_format}:{filter_name}",
        "--outdir",
        str(outdir),
        str(input_path),
    ]

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=False,
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"Conversion failed.\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )

    output_path = outdir / f"{input_path.stem}.{output_format}"

    if not output_path.exists():
        raise RuntimeError(
            f"LibreOffice finished without an error code, but output file was not created: {output_path}\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )

    return output_path
