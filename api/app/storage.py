"""Storage adapter for images — Local filesystem / Google Drive / future providers.

Frontend always uses ClassHub URLs. The adapter decides where bytes live.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Optional


@dataclass
class StoredImage:
    """Result of saving an image via a storage adapter."""
    provider: str          # "local" | "google_drive" | ...
    storage_key: str       # provider-specific key (file path, Drive fileId, ...)
    mime_type: str
    size: int


@dataclass
class StorageContext:
    owner_id: str
    tenant_id: str | None = None


@dataclass
class ImageFile:
    image_id: str
    data: bytes
    mime_type: str
    ext: str


@dataclass
class RetrievedImage:
    """Result of opening an image — stream + metadata."""
    stream: IO[bytes]
    mime_type: str
    size: int


class ImageStorage(ABC):
    """Abstract interface every storage provider must implement."""

    provider: str = ""

    @abstractmethod
    async def save(self, context: StorageContext, image: ImageFile) -> StoredImage:
        ...

    @abstractmethod
    async def open(self, context: StorageContext, storage_key: str) -> RetrievedImage:
        ...

    @abstractmethod
    async def delete(self, context: StorageContext, storage_key: str) -> None:
        ...

    @abstractmethod
    async def exists(self, context: StorageContext, storage_key: str) -> bool:
        ...


class LocalImageStorage(ImageStorage):
    """Default adapter — stores files on local filesystem."""

    provider = "local"

    def __init__(self, base_dir: str):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _full_path(self, storage_key: str) -> Path:
        # Prevent path traversal — storage_key is relative under base_dir
        full = (self.base_dir / storage_key).resolve()
        if not str(full).startswith(str(self.base_dir.resolve())):
            raise ValueError("path traversal detected")
        return full

    async def save(self, context: StorageContext, image: ImageFile) -> StoredImage:
        # Organise: {owner_id}/{image_id}{ext}
        owner_id = context.owner_id
        subdir = self.base_dir / owner_id if owner_id else self.base_dir
        subdir.mkdir(parents=True, exist_ok=True)
        storage_key = f"{owner_id}/{image.image_id}{image.ext}" if owner_id else f"{image.image_id}{image.ext}"
        path = self._full_path(storage_key)
        path.write_bytes(image.data)
        return StoredImage(
            provider=self.provider,
            storage_key=storage_key,
            mime_type=image.mime_type,
            size=len(image.data),
        )

    async def open(self, context: StorageContext, storage_key: str) -> RetrievedImage:
        path = self._full_path(storage_key)
        if not path.exists():
            raise FileNotFoundError(f"image not found: {storage_key}")
        return RetrievedImage(
            stream=open(path, "rb"),
            mime_type="",  # filled by caller from DB
            size=path.stat().st_size,
        )

    async def delete(self, context: StorageContext, storage_key: str) -> None:
        path = self._full_path(storage_key)
        if path.exists():
            path.unlink()

    async def exists(self, context: StorageContext, storage_key: str) -> bool:
        return self._full_path(storage_key).exists()


class StorageRegistry:
    """Picks the right adapter based on provider name."""

    def __init__(self):
        self._adapters: dict[str, ImageStorage] = {}
        self._default_provider = "local"

    def register(self, adapter: ImageStorage):
        self._adapters[adapter.provider] = adapter

    def get(self, provider: str) -> ImageStorage:
        adapter = self._adapters.get(provider)
        if not adapter:
            raise ValueError(f"unknown storage provider: {provider}")
        return adapter

    @property
    def default_provider(self) -> str:
        return self._default_provider

    @default_provider.setter
    def default_provider(self, provider: str) -> None:
        self._default_provider = provider or "local"


# Singleton
_registry: Optional[StorageRegistry] = None


def get_storage_registry() -> StorageRegistry:
    global _registry
    if _registry is None:
        from app.config import get_settings
        from app.db import get_db
        from app.google_drive import GoogleDriveImageStorage, create_google_credential_provider
        s = get_settings()
        _registry = StorageRegistry()
        _registry.default_provider = s.image_storage_provider
        # Always register local
        _registry.register(LocalImageStorage(s.upload_dir))
        provider = create_google_credential_provider(s, get_db())
        _registry.register(GoogleDriveImageStorage(provider, provider.drive))
    return _registry
