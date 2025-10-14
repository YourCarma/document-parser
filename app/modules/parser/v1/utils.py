from pathlib import Path
import tempfile
import os
import asyncio
import aiofiles
from aiofiles.os import remove as aioremove

from loguru import logger
from fastapi import UploadFile


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