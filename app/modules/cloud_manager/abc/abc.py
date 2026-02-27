from typing import Union, Optional
from pathlib import Path
from abc import ABC, abstractmethod


class BaseCloudManager(ABC):
    
    def __init__(self, manager_url: str):
        super().__init__()
        self.manager_url = manager_url
        
    @abstractmethod
    async def upload_file(self, file_path: Union[Path, str], upload_url: str):
        pass
    
    @abstractmethod
    async def get_file_link(self, share_url: str):
        pass