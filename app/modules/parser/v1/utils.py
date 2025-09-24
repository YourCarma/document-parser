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
        
        temp_path = os.path.join(temp_dir, original_filename)
        
        content = await file.read()
        
        async with aiofiles.open(temp_path, 'wb') as f:
            await f.write(content)
        
        logger.success(f"File saved at: {temp_path}")
        return temp_path
    except Exception as e:
        logger.error(f"Error saving_file: {e}")
        await delete_file(temp_path)

async def delete_file(file_path: Path):
    try:
        logger.debug(f"Deleting \"{file_path}\" file")
        await aioremove(file_path)
        logger.success(f"File \"{file_path}\" succesfully deleted!")
    except Exception as e:
        logger.error(f"Error on deleting \"{file_path}\" file: {e}")


    
