from pathlib import Path

import aiohttp
from loguru import logger

from settings import settings


class WatchtowerService:
    def __init__(self, base_url: str):
        self.base_url = base_url

    async def create_folder(self, bucket: str, prefix: str):
        """Create folder placeholder in bucket. Ignores 4xx (folder may already exist)."""
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
                logger.debug(f"Folder ensured: {bucket}/{prefix} [{resp.status}]")

    async def upload_file(self, bucket: str, local_path: str, filename: str) -> str:
        """Upload local file to bucket. Returns filename."""
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
                    logger.info(f"Uploaded '{filename}' → bucket '{bucket}'")
        return filename

    async def get_sharelink(
        self,
        bucket: str,
        file_path: str,
        expired_secs: int = 3600 * 24 * 7,
    ) -> str:
        """Get pre-signed share URL for a file. Returns URL string."""
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
                logger.info(f"Sharelink for '{file_path}': {url}")
                return url

    @staticmethod
    def _apply_shared_host(url: str) -> str:
        """Prepend WATCHTOWER_SHARED_HOST to the sharelink path."""
        if not url or not settings.WATCHTOWER_SHARED_HOST:
            return url
        host = settings.WATCHTOWER_SHARED_HOST.rstrip("/")
        path = url.lstrip("/")
        return f"{host}/{path}"
