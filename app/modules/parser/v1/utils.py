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

def convert_doc_to(input_file_path: Union[Path| str], output_format: ConvertationOutputs, output_dir: Optional[Union[Path| str]] = None):
    logger.debug(f"Covertation to {input_file_path} в {output_format}")
    if not output_dir:
        output_dir = os.path.dirname(input_file_path)

    cmd = [
         'libreoffice',
    '--headless',
    '--nologo',
    '--nofirststartwizard',
    '--convert-to', f'{output_format}:writer8',
    '--outdir', output_dir,
    input_file_path
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
        base_name = os.path.splitext(os.path.basename(input_file_path))[0]
        output_path = os.path.join(output_dir, f"{base_name}.{output_format}")
        logger.success(f"Document convertion sucsessful!. File path: {output_path}")
        return output_path
    except subprocess.CalledProcessError as e:
        logger.warning(f"Error converting dpcument: {e.stderr.decode()}. Returning original file")
        return input_file_path