from pydantic import BaseModel


class FolderForm(BaseModel):
    prefix: str


class ShareFileForm(BaseModel):
    file_path: str
    expired_secs: int = 3600 * 24 * 7  # 7 days


class BucketSchema(BaseModel):
    id: str
    path: str
    created_at: str
