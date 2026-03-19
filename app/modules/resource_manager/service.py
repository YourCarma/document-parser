from typing import Optional

import aiohttp
from loguru import logger


class ResourceManagerService:
    def __init__(self, base_url: str):
        self.base_url = base_url

    async def get_user_bucket(self, user_id: str) -> Optional[str]:
        """
        Fetch user's Document resources and return the external_id of the first
        one that has it — this is the bucket name in watchtower.
        """
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
                        logger.debug(
                            f"Bucket for user '{user_id}': '{external_id}'"
                        )
                        return external_id
                logger.warning(
                    f"ResourceManager: у пользователя '{user_id}' нет ресурсов "
                    f"типа Document с заполненным external_id. "
                    f"Получено ресурсов: {len(resources)}"
                )
                return None
