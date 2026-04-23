from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from .models import SyncMetadata


class SyncMetadataStore:
    """Persistence helper for course sync metadata files."""

    def __init__(
        self,
        *,
        sync_metadata_file: str,
        ensure_directory: Callable[[Path], None],
    ) -> None:
        self._sync_metadata_file = sync_metadata_file
        self._ensure_directory = ensure_directory

    def path(self, course_dir: Path) -> Path:
        return course_dir / self._sync_metadata_file

    def load(self, course_dir: Path) -> SyncMetadata | None:
        metadata_path = self.path(course_dir)
        if not metadata_path.exists():
            return None

        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"Sync-Metadaten in {metadata_path} sind kein JSON-Objekt.")
        return SyncMetadata.from_dict(payload)

    def save(self, course_dir: Path, metadata: SyncMetadata) -> Path:
        self._ensure_directory(course_dir)
        metadata_path = self.path(course_dir)
        metadata_path.write_text(
            json.dumps(metadata.to_dict(), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return metadata_path
