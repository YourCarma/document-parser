from pathlib import Path
import signal
import tempfile
import os
from concurrent.futures import Executor
from concurrent.futures.process import BrokenProcessPool
from typing import Union, Optional
import asyncio
import subprocess

from loguru import logger
from fastapi import UploadFile
from starlette.background import BackgroundTask

from modules.parser.v1.exceptions import ConversionTimeoutError, ProcessPoolUnavailable
from modules.parser.v1.process_pool import ProcessPoolHolder
from modules.parser.v1.schemas import (
    ConvertationOutputs,
    FileFormats,
    ParserMods,
    ParserParams,
)
from settings import settings


UPLOAD_CHUNK_SIZE = 1024 * 1024

# Расширения, для которых ParserFactory умеет подобрать парсер.
SUPPORTED_EXTENSIONS: frozenset[str] = frozenset(
    ext.lower() for fmt in FileFormats for ext in fmt.value
)

# Сколько ждать мягкого завершения soffice перед SIGKILL.
_SOFFICE_KILL_GRACE_SECS: float = 5.0


def is_supported_extension(file_name: Union[str, Path]) -> bool:
    """Проверить, поддерживается ли расширение файла парсером.

    Никогда не бросает: файл без расширения — просто `False`.
    """
    suffix = Path(str(file_name)).suffix
    if not suffix:
        return False
    return suffix.lower() in SUPPORTED_EXTENSIONS


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

async def run_in_process(fn, app_executor, *args, semaphore=None, retries: int = 1):
    """Выполнить `fn` в пуле процессов, переживая гибель воркера.

    `app_executor` — штатно `ProcessPoolHolder`; голый `Executor` принимается
    для обратной совместимости, но пересобрать его нельзя.
    """
    loop = asyncio.get_running_loop()
    is_holder = isinstance(app_executor, ProcessPoolHolder)

    for attempt in range(retries + 1):
        if is_holder:
            executor: Executor = await app_executor.get()
        else:
            executor = app_executor

        try:
            # Семафор живёт внутри одной попытки: удерживать слот через
            # пересборку пула значит держать очередь на сломанном пуле.
            if semaphore is None:
                return await loop.run_in_executor(executor, fn, *args)
            async with semaphore:
                return await loop.run_in_executor(executor, fn, *args)
        except BrokenProcessPool as exc:
            if not is_holder or attempt >= retries:
                raise ProcessPoolUnavailable(
                    "Процесс парсинга был прерван. Повторите попытку."
                ) from exc
            await app_executor.rebuild(executor)
            logger.error(
                "run_in_process: parsing worker died, pool rebuilt "
                "attempt={} fn='{}'",
                attempt + 1,
                getattr(fn, "__name__", fn),
            )


def convert_doc_to(
    input_file_path: Union[Path, str],
    output_format: str,
    output_dir: Optional[Union[Path, str]] = None,
    timeout_secs: Optional[int] = None,
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

    if timeout_secs is None:
        timeout_secs = settings.SOFFICE_TIMEOUT_SECS

    # start_new_session=True: soffice плодит дочерние процессы, и убить нужно
    # всю группу — иначе зависший конвертер переживёт таймаут.
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout_secs)
    except subprocess.TimeoutExpired:
        _kill_soffice_process_group(proc)
        raise ConversionTimeoutError(
            f"Конвертация '{input_path.name}' превысила {timeout_secs} с"
        )

    if proc.returncode != 0:
        raise RuntimeError(
            f"Conversion failed.\n"
            f"stdout:\n{stdout}\n"
            f"stderr:\n{stderr}"
        )

    output_path = outdir / f"{input_path.stem}.{output_format}"

    if not output_path.exists():
        raise RuntimeError(
            f"LibreOffice finished without an error code, but output file was not created: {output_path}\n"
            f"stdout:\n{stdout}\n"
            f"stderr:\n{stderr}"
        )

    return output_path


def _kill_soffice_process_group(proc: subprocess.Popen) -> None:
    """Прибить зависший soffice: сначала мягко группу, потом жёстко."""
    try:
        pgid = os.getpgid(proc.pid)
    except ProcessLookupError:
        logger.debug("soffice: the process had already exited before the signal was sent")
        return

    if pgid != proc.pid:
        # setsid не успел отработать (гонка на старте) — группа чужая,
        # бить по ней нельзя, иначе заденем посторонние процессы.
        logger.debug(
            "soffice: pgid={} does not match pid={}, killing the process only",
            pgid,
            proc.pid,
        )
        proc.kill()
        proc.wait()
        return

    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        logger.debug("soffice: group {} had already exited", pgid)
        return

    try:
        proc.wait(timeout=_SOFFICE_KILL_GRACE_SECS)
        return
    except subprocess.TimeoutExpired:
        pass

    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        logger.debug("soffice: group {} exited between SIGTERM and SIGKILL", pgid)
        return
    proc.wait()
