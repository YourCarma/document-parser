from typing import Optional

import aiohttp
from loguru import logger

from modules.resource_manager.schemas import ResourceSchema


class ResourceManagerService:
    """Клиент resource_manager для поиска пользовательского bucket."""

    def __init__(
        self,
        base_url: str,
        session: aiohttp.ClientSession | None = None,
    ):
        self.base_url = base_url
        self.session = session

    async def get_user_bucket(self, user_id: str) -> Optional[str]:
        """Вернуть bucket-id персонального Document-ресурса."""
        if self.session is not None:
            return await self._get_user_bucket(self.session, user_id)
        async with aiohttp.ClientSession() as session:
            return await self._get_user_bucket(session, user_id)

    async def _get_user_bucket(
        self,
        session: aiohttp.ClientSession,
        user_id: str,
    ) -> Optional[str]:
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
                payload = await resp.json()
                if not isinstance(payload, list):
                    raise ValueError(
                        "ResourceManager вернул некорректный список ресурсов"
                    )

                resources = [ResourceSchema.model_validate(item) for item in payload]
                personal_resources = [
                    resource
                    for resource in resources
                    if resource.resource_type == "Document"
                    and resource.resource_owner == "User"
                ]

                if len(personal_resources) > 1:
                    resource_ids = [resource.id for resource in personal_resources]
                    raise ValueError(
                        "ResourceManager вернул несколько персональных "
                        f"Document-ресурсов user_id='{user_id}': {resource_ids}"
                    )

                if personal_resources:
                    bucket = personal_resources[0].id
                    logger.info(
                        "ResourceManager: найден персональный bucket "
                        "user_id='{}' bucket='{}' resource_name='{}'",
                        user_id,
                        bucket,
                        personal_resources[0].name,
                    )
                    return bucket

                logger.warning(
                    "ResourceManager: персональный Document bucket "
                    "не найден user_id='{}' resources={}",
                    user_id,
                    len(resources),
                )
                return None
