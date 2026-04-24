from pathlib import Path
import tempfile
import os
from typing import Union, Optional
import asyncio
import aiofiles
from aiofiles.os import remove as aioremove
import subprocess

from loguru import logger
from fastapi import UploadFile

from modules.parser.v1.schemas import ConvertationOutputs


async def save_file(file: UploadFile) -> Path:
    temp_path = None
    try:
        temp_dir = tempfile.gettempdir()
        original_filename = file.filename
        
        file_stem = Path(original_filename).stem
        file_suffix = Path(original_filename).suffix
        
        temp_path = os.path.join(temp_dir, original_filename)
        
        counter = 1
        while os.path.exists(temp_path):
            new_filename = f"{file_stem}_{counter}{file_suffix}"
            temp_path = os.path.join(temp_dir, new_filename)
            counter += 1
        
        content = await file.read()
        
        async with aiofiles.open(temp_path, 'wb') as f:
            await f.write(content)

        logger.success(f"File saved at: {temp_path}")
        return Path(temp_path)
    except Exception as e:
        logger.error(f"Error saving_file: {e}")
        await delete_file(temp_path)
        raise

async def delete_file(file_path: Path):
    try:
        logger.debug(f"Deleting \"{file_path}\" file")
        await aioremove(file_path)
        logger.success(f"File \"{file_path}\" succesfully deleted!")
    except Exception as e:
        logger.error(f"Error on deleting \"{file_path}\" file: {e}")

def read_file_content(file_path: Path):
    try:
        with open(file_path, mode='r', encoding='utf-8') as file:
            contents = file.read()
            return contents
    except Exception as e:
        logger.error(f"Error on deleting \"{file_path}\" file: {e}")

async def run_in_process(fn, app_executor, *args):
    loop = asyncio.get_event_loop()
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