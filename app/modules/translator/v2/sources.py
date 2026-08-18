"""Источник исходного файла для конвейера перевода.

HTTP-вход отдаёт уже сохранённый локальный файл, очередь — object key в
бакете. Конвейер про разницу не знает.
"""

import shutil
import tempfile
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from modules.watchtower.service import WatchtowerService


@dataclass(frozen=True, slots=True)
class SourceFile:
    local_path: str
    original_filename: str
    remote_key: str | None = None
    """Object key, если файл УЖЕ лежит в бакете. Тогда конвейер не заливает
    оригинал повторно, а только берёт на него share-ссылку."""


class SourceFileProviderABC(ABC):
    """Поставщик исходного файла задачи."""

    @abstractmethod
    async def acquire(self, bucket: str) -> SourceFile:
        """Подготовить локальный файл. Расширение обязано сохраниться."""

    async def release(self) -> None:
        """Убрать за собой. По умолчанию — ничего не делает."""
        return None


class LocalUploadSource(SourceFileProviderABC):
    """HTTP-вход: файл уже сохранён `save_file()`."""

    def __init__(self, local_path: str | Path, original_filename: str) -> None:
        self._local_path = str(local_path)
        self._original_filename = original_filename

    async def acquire(self, bucket: str) -> SourceFile:
        return SourceFile(
            local_path=self._local_path,
            original_filename=self._original_filename,
            remote_key=None,
        )


class WatchtowerSource(SourceFileProviderABC):
    """Вход из очереди: файл скачивается из бакета по object key."""

    def __init__(
        self,
        watchtower: WatchtowerService,
        file_path: str,
        dest_dir: str | Path | None = None,
    ) -> None:
        self._watchtower = watchtower
        self._file_path = file_path
        self._dir = str(dest_dir) if dest_dir is not None else None
        # Чужой каталог удалять нельзя — чистим только то, что создали сами.
        self._owns_dir = dest_dir is None

    async def acquire(self, bucket: str) -> SourceFile:
        if self._dir is None:
            self._dir = tempfile.mkdtemp(prefix="dp_source_")
        # Ошибки хранилища идут наружу как есть: их классифицирует консюмер.
        local = await self._watchtower.download_file(bucket, self._file_path, self._dir)
        return SourceFile(
            local_path=local,
            original_filename=Path(self._file_path).name,
            remote_key=self._file_path,
        )

    async def release(self) -> None:
        if not self._owns_dir or not self._dir:
            return
        # Синхронно: release() обязан отработать и под CancelledError.
        shutil.rmtree(self._dir, ignore_errors=True)
        logger.debug("WatchtowerSource: временный каталог удалён '{}'", self._dir)
