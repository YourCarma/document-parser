from typing import Union, Optional
from pathlib import Path
from abc import ABC, abstractmethod


class BaseWebhookManager(ABC):
    
    def __init__(self, manager_url: str):
        super().__init__()
        self.manager_url = manager_url
    
    
       
    @abstractmethod
    async def update_progress(self, ):
        pass
    
    @abstractmethod
    async def get_file_link(self, share_url: str):
        pass