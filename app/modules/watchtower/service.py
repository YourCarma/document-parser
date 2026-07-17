from urllib.parse import quote, urlsplit, urlunsplit

import aiohttp
from loguru import logger

from settings import settings


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
        prefix = self.encode_path(prefix)
        async def request(session: aiohttp.ClientSession):
            async with session.post(
                f"{self.base_url}/api/v1/cloud/{bucket}/folder",
                json={"prefix": prefix},
            ) as resp:
                body = await resp.text()
                if resp.status >= 500:
                    raise Exception(
                        f"Watchtower create_folder [{resp.status}] "
                        f"bucket='{bucket}' prefix='{prefix}': {body}"
                    )
                logger.debug(
                    "Watchtower: папка подготовлена bucket='{}' prefix='{}' status={}",
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
        encoded_filename = self.encode_path(filename)
        encoded_prefix = self.encode_path(prefix)
        async def request(session: aiohttp.ClientSession):
            with open(local_path, "rb") as f:
                form = aiohttp.FormData()
                if encoded_prefix:
                    form.add_field("prefix", encoded_prefix)
                form.add_field(
                    "files",
                    f,
                    filename=encoded_filename,
                    content_type="application/octet-stream",
                )
                async with session.put(
                    f"{self.base_url}/api/v1/cloud/{bucket}/file/upload",
                    data=form,
                ) as resp:
                    body = await resp.text()
                    if resp.status not in (200, 201):
                        raise Exception(
                            f"Watchtower upload_file [{resp.status}] "
                            f"bucket='{bucket}' file='{encoded_filename}': {body}"
                        )
                    logger.info(
                        "Watchtower: файл загружен bucket='{}' prefix='{}' filename='{}'",
                        bucket,
                        encoded_prefix,
                        encoded_filename,
                    )
        await self._with_session(request)
        if encoded_prefix:
            return f"{encoded_prefix}/{encoded_filename}"
        return encoded_filename

    async def get_sharelink(
        self,
        bucket: str,
        file_path: str,
        expired_secs: int = 3600 * 24 * 7,
    ) -> str:
        """Получить pre-signed share-ссылку для файла в bucket."""
        async def request(session: aiohttp.ClientSession):
            async with session.post(
                f"{self.base_url}/api/v1/cloud/{bucket}/file/share",
                json={"file_path": file_path, "expired_secs": expired_secs},
            ) as resp:
                body = await resp.text()
                if resp.status not in (200, 201):
                    raise Exception(
                        f"Watchtower get_sharelink [{resp.status}] "
                        f"bucket='{bucket}' file='{file_path}': {body}"
                )
                data = await resp.json()
                url = data.get("message", "")
                url = self._apply_shared_prefix(url)
                logger.info(
                    "Watchtower: получена share-ссылка bucket='{}' file_path='{}'",
                    bucket,
                    file_path,
                )
                return url
        return await self._with_session(request)

    @staticmethod
    def encode_path(path: str) -> str:
        """URL-encode object path segment-by-segment, preserving folder separators."""
        return "/".join(
            quote(segment, safe="")
            for segment in str(path).strip("/").split("/")
            if segment
        )

    @staticmethod
    def _apply_shared_prefix(url: str) -> str:
        """Вернуть относительный frontend path или старую host-based ссылку."""
        shared_prefix = settings.WATCHTOWER_SHARED_PREFIX.strip("/")
        if url and shared_prefix:
            parsed = urlsplit(url)
            path = parsed.path if parsed.scheme or parsed.netloc else urlsplit(url).path
            path = f"/{shared_prefix}/{path.lstrip('/')}"
            return urlunsplit(("", "", path, parsed.query, parsed.fragment))

        if not url or not settings.WATCHTOWER_SHARED_HOST:
            return url
        host = settings.WATCHTOWER_SHARED_HOST.rstrip("/")
        path = url.lstrip("/")
        return f"{host}/{path}"
