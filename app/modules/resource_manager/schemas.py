from typing import Optional
from datetime import datetime

from pydantic import BaseModel


class ResourceSchema(BaseModel):
    """Ресурс пользователя, полученный из resource_manager."""

    id: str
    name: str
    created_at: datetime
    is_public: bool
    resource_type: str
    resource_owner: str
    external_id: Optional[str] = None
