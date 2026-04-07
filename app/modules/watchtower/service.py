import aiohttp
from loguru import logger

from settings import settings


class WatchtowerService:
    """Клиент для загрузки файлов и получения share-ссылок в watchtower."""

    def __init__(self, base_url: str):
        self.base_url = base_url

    async def create_folder(self, bucket: str, prefix: str):
        """Создать placeholder-папку в bucket, если backend этого требует."""
        async with aiohttp.ClientSession() as session:
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

    async def upload_file(self, bucket: str, local_path: str, filename: str) -> str:
        """Загрузить локальный файл в bucket и вернуть object key."""
        async with aiohttp.ClientSession() as session:
            with open(local_path, "rb") as f:
                form = aiohttp.FormData()
                form.add_field(
                    "files",
                    f,
                    filename=filename,
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
                            f"bucket='{bucket}' file='{filename}': {body}"
                        )
                    logger.info(
                        "Watchtower: файл загружен bucket='{}' filename='{}'",
                        bucket,
                        filename,
                    )
        return filename

    async def get_sharelink(
        self,
        bucket: str,
        file_path: str,
        expired_secs: int = 3600 * 24 * 7,
    ) -> str:
        """Получить pre-signed share-ссылку для файла в bucket."""
        async with aiohttp.ClientSession() as session:
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
                url = self._apply_shared_host(url)
                logger.info(
                    "Watchtower: получена share-ссылка bucket='{}' file_path='{}'",
                    bucket,
                    file_path,
                )
                return url

    @staticmethod
    def _apply_shared_host(url: str) -> str:
        """Заменить внутренний host на публичный shared host, если он задан."""
        if not url or not settings.WATCHTOWER_SHARED_HOST:
            return url
        host = settings.WATCHTOWER_SHARED_HOST.rstrip("/")
        path = url.lstrip("/")
        return f"{host}/{path}"
