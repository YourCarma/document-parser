from typing import Optional

import aiohttp
from loguru import logger


class ResourceManagerService:
    """Клиент resource_manager для поиска пользовательского bucket."""

    def __init__(self, base_url: str):
        self.base_url = base_url

    async def get_user_bucket(self, user_id: str) -> Optional[str]:
        """Вернуть `external_id` первого ресурса типа `Document` для пользователя."""
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{self.base_url}/api/v1/resource/",
                headers={"x-user-id": user_id},
                params={"resource_kind": "Document"},
            ) as resp:
                body = await resp.text()
                if resp.status != 200:
                    raise Exception(
                        f"ResourceManager get_user_bucket [{resp.status}] "
                        f"user_id='{user_id}': {body}"
                    )
                resources: list[dict] = await resp.json()
                for resource in resources:
                    external_id = resource.get("external_id")
                    if external_id:
                        logger.info(
                            "ResourceManager: найден bucket user_id='{}' bucket='{}'",
                            user_id,
                            external_id,
                        )
                        return external_id
                logger.warning(
                    "ResourceManager: bucket не найден user_id='{}' resources={}",
                    user_id,
                    len(resources),
                )
                return None
