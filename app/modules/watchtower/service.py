import asyncio
from pathlib import Path
from typing import Union
from urllib.parse import quote

import aiohttp
from loguru import logger

from modules.watchtower.exceptions import (
    FileNotFoundInStorage,
    FileTooLargeError,
    WatchtowerError,
    WatchtowerUnavailable,
)
from settings import settings


DOWNLOAD_CHUNK_SIZE: int = 1024 * 1024


class WatchtowerService:
    """Клиент для загрузки файлов и получения share-ссылок в watchtower."""

    def __init__(
        self,
        base_url: str,
        session: aiohttp.ClientSession | None = None,
    ):
        self.base_url = base_url
        self.session = session

    async def _with_session(self, operation):
        if self.session is not None:
            return await operation(self.session)
        async with aiohttp.ClientSession() as session:
            return await operation(session)

    async def create_folder(self, bucket: str, prefix: str):
        """Создать placeholder-папку в bucket, если backend этого требует."""
        bucket_segment = quote(str(bucket), safe="")
        prefix = str(prefix).strip("/")
        async def request(session: aiohttp.ClientSession):
            async with session.post(
                f"{self.base_url}/api/v1/cloud/{bucket_segment}/folder",
                json={"prefix": prefix},
            ) as resp:
                body = await resp.text()
                if resp.status >= 500:
                    raise Exception(
                        f"Watchtower create_folder [{resp.status}] "
                        f"bucket='{bucket}' prefix='{prefix}': {body}"
                    )
                logger.debug(
                    "Watchtower: folder prepared bucket='{}' prefix='{}' status={}",
                    bucket,
                    prefix,
                    resp.status,
                )
        await self._with_session(request)

    async def upload_file(
        self,
        bucket: str,
        local_path: str,
        filename: str,
        prefix: str = "",
    ) -> str:
        """Загрузить локальный файл в bucket и вернуть object key."""
        bucket_segment = quote(str(bucket), safe="")
        # Multipart fields carry Unicode strings. Pre-encoding them would make
        # `%D0...` part of the actual object name and Watchtower would encode
        # every `%` again as `%25` when producing a share URL.
        safe_filename = Path(str(filename).replace("\\", "/")).name
        normalized_prefix = str(prefix).strip("/")
        async def request(session: aiohttp.ClientSession):
            with open(local_path, "rb") as f:
                # Watchtower stores multipart `filename` literally. aiohttp's
                # default quote_fields=True turns Cyrillic into `%D0...`, which
                # then becomes the visible object name instead of URL syntax.
                form = aiohttp.FormData(quote_fields=False)
                if normalized_prefix:
                    form.add_field("prefix", normalized_prefix)
                form.add_field(
                    "files",
                    f,
                    filename=safe_filename,
                    content_type="application/octet-stream",
                )
                async with session.put(
                    f"{self.base_url}/api/v1/cloud/{bucket_segment}/file/upload",
                    data=form,
                ) as resp:
                    body = await resp.text()
                    if resp.status not in (200, 201):
                        raise Exception(
                            f"Watchtower upload_file [{resp.status}] "
                            f"bucket='{bucket}' file='{safe_filename}': {body}"
                        )
                    logger.info(
                        "Watchtower: file uploaded bucket='{}' prefix='{}' filename='{}'",
                        bucket,
                        normalized_prefix,
                        safe_filename,
                    )
        await self._with_session(request)
        if normalized_prefix:
            return f"{normalized_prefix}/{safe_filename}"
        return safe_filename

    async def download_file(
        self,
        bucket: str,
        file_path: str,
        dest_dir: Union[str, Path],
        max_size_mb: int | None = None,
    ) -> str:
        """Скачать файл из бакета в `dest_dir` потоком и вернуть путь к нему."""
        limit_mb = (
            settings.MAX_DOWNLOAD_FILE_SIZE_MB if max_size_mb is None else max_size_mb
        )
        limit_bytes = limit_mb * 1024 * 1024
        bucket_segment = quote(str(bucket), safe="")

        # Суффикс имени определяет парсер, поэтому имя не перекодируем и не
        # нормализуем — только отбрасываем путь.
        name = Path(str(file_path).replace("\\", "/")).name
        if not name:
            raise WatchtowerError(
                f"Watchtower download_file: пустое имя файла в '{file_path}'"
            )

        dest_root = Path(dest_dir)
        dest_root.mkdir(parents=True, exist_ok=True)
        dest = dest_root / name

        async def request(session: aiohttp.ClientSession):
            try:
                async with session.post(
                    f"{self.base_url}/api/v1/cloud/{bucket_segment}/file/download",
                    json={"file_name": file_path},
                ) as resp:
                    if resp.status == 404:
                        raise FileNotFoundInStorage(
                            f"Watchtower download_file [404] "
                            f"bucket='{bucket}' file='{file_path}'"
                        )
                    if resp.status >= 500:
                        raise WatchtowerUnavailable(
                            f"Watchtower download_file [{resp.status}] "
                            f"bucket='{bucket}' file='{file_path}'"
                        )
                    if resp.status != 200:
                        raise WatchtowerError(
                            f"Watchtower download_file [{resp.status}] "
                            f"bucket='{bucket}' file='{file_path}'"
                        )

                    # Быстрая отсечка по заголовку: не начинаем качать заведомо
                    # слишком большой файл. Реальная защита — счётчик ниже.
                    declared = resp.headers.get("Content-Length")
                    if declared is not None:
                        try:
                            declared_size = int(declared)
                        except (TypeError, ValueError):
                            declared_size = None
                        if declared_size is not None and declared_size > limit_bytes:
                            raise FileTooLargeError(
                                f"Файл '{name}' занимает {declared_size} байт "
                                f"при лимите {limit_mb} МБ"
                            )

                    downloaded = 0
                    try:
                        with open(dest, "wb") as destination:
                            async for chunk in resp.content.iter_chunked(
                                DOWNLOAD_CHUNK_SIZE
                            ):
                                downloaded += len(chunk)
                                if downloaded > limit_bytes:
                                    raise FileTooLargeError(
                                        f"Файл '{name}' превысил лимит "
                                        f"{limit_mb} МБ при скачивании"
                                    )
                                destination.write(chunk)
                    except BaseException:
                        dest.unlink(missing_ok=True)
                        raise
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                raise WatchtowerUnavailable(
                    f"Watchtower download_file недоступен "
                    f"bucket='{bucket}' file='{file_path}': {exc}"
                ) from exc

            logger.info(
                "Watchtower: file downloaded bucket='{}' file='{}' dest='{}' bytes={}",
                bucket,
                file_path,
                dest,
                downloaded,
            )
            return str(dest)

        return await self._with_session(request)
