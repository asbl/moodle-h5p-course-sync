from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Iterable
from zipfile import BadZipFile, ZipFile

if TYPE_CHECKING:
    from scripts.classes.models import PythonQuestionBlock


class H5PFileService:
    """Handles H5P sidecar and archive file operations."""

    def __init__(
        self,
        *,
        courses_dir: Path,
        ensure_directory: Callable[[Path], None],
        read_yaml: Callable[[Path], object],
        read_h5p_content_payload: Callable[[Path], dict[str, object]],
        write_h5p_content_files: Callable[[Path, dict[str, object]], None],
        write_json: Callable[[Path, dict], None],
    ) -> None:
        self._courses_dir = courses_dir
        self._ensure_directory = ensure_directory
        self._read_yaml = read_yaml
        self._read_h5p_content_payload = read_h5p_content_payload
        self._write_h5p_content_files = write_h5p_content_files
        self._write_json = write_json

    def build_h5p_sidecar_paths(self, question: PythonQuestionBlock) -> tuple[str, str]:
        base_dir = Path("h5p") / question.identifier
        return (base_dir / "h5p.json").as_posix(), (base_dir / "content.yml").as_posix()

    def build_source_package_sidecar_path(self, question: PythonQuestionBlock) -> str:
        return (Path("h5p") / question.identifier).as_posix()

    def write_h5p_sidecar_files(self, question: PythonQuestionBlock) -> tuple[str, str]:
        if question.course_dir is None or question.h5p_metadata is None or question.h5p_content is None:
            return question.h5p_metadata_path, question.h5p_content_path

        metadata_rel, content_rel = self.build_h5p_sidecar_paths(question)
        metadata_path = question.course_dir / metadata_rel
        content_path = question.course_dir / content_rel
        self._write_json(metadata_path, question.h5p_metadata)
        self._write_h5p_content_files(content_path.parent, question.h5p_content)
        return metadata_rel, content_rel

    def write_source_package_sidecar(self, question: PythonQuestionBlock, source_archive: Path) -> str:
        if question.course_dir is None:
            return question.source_package_path

        relative_path = self.build_source_package_sidecar_path(question)
        target_path = question.course_dir / relative_path

        if target_path.exists():
            if target_path.is_dir():
                shutil.rmtree(target_path)
            else:
                target_path.unlink()

        with ZipFile(source_archive) as archive:
            metadata_payload = json.loads(archive.read("h5p.json").decode("utf-8"))
            content_payload = json.loads(archive.read("content/content.json").decode("utf-8"))

        self.populate_imported_h5p_directory(source_archive, target_path, metadata_payload, content_payload)
        return relative_path

    def load_h5p_sidecar_file(self, course_dir: Path, relative_path: str, *, description: str) -> dict[str, object]:
        path = (course_dir / relative_path).resolve()
        if not path.exists() or not path.is_file():
            raise ValueError(f"{description} '{relative_path}' wurde nicht gefunden.")

        if path.suffix in {".yml", ".yaml"}:
            payload = self._read_yaml(path)
        else:
            payload = json.loads(path.read_text(encoding="utf-8"))

        if not isinstance(payload, dict):
            raise ValueError(f"{description} '{relative_path}' muss ein Objekt sein.")
        return payload

    def load_h5p_payload_from_path(self, source_path: Path) -> tuple[dict[str, object], dict[str, object]] | None:
        try:
            if source_path.is_dir():
                metadata_payload = json.loads((source_path / "h5p.json").read_text(encoding="utf-8"))
                content_payload = self._read_h5p_content_payload(source_path)
            else:
                with ZipFile(source_path) as archive:
                    metadata_payload = json.loads(archive.read("h5p.json").decode("utf-8"))
                    content_payload = json.loads(archive.read("content/content.json").decode("utf-8"))
        except (BadZipFile, KeyError, OSError, json.JSONDecodeError):
            return None

        if not isinstance(metadata_payload, dict) or not isinstance(content_payload, dict):
            return None
        return metadata_payload, content_payload

    def source_tree_mtime_ns(self, path: Path | None) -> int:
        if path is None or not path.exists():
            return 0
        if path.is_file():
            return path.stat().st_mtime_ns

        latest_mtime = path.stat().st_mtime_ns
        for child in path.rglob("*"):
            latest_mtime = max(latest_mtime, child.stat().st_mtime_ns)
        return latest_mtime

    def normalize_h5p_source_asset_path(self, relative_path: str, *, content_root_only: bool = False) -> str | None:
        normalized = relative_path.strip("/")
        if not normalized:
            return None
        if normalized in {"h5p.json", "content.json", "content.yml", "content.yaml", "content/content.json"}:
            return None
        if normalized.startswith("content/"):
            asset_path = normalized[len("content/"):]
            return asset_path or None
        if content_root_only:
            return None
        if "/" not in normalized and normalized.endswith((".json", ".yml", ".yaml")):
            return None
        return normalized

    def populate_imported_h5p_directory(
        self,
        source_path: Path,
        target_dir: Path,
        metadata_payload: dict[str, object],
        content_payload: dict[str, object],
    ) -> None:
        if target_dir.exists():
            shutil.rmtree(target_dir)
        self._ensure_directory(target_dir)

        if source_path.is_dir():
            content_root_only = not any(
                (source_path / candidate).exists()
                for candidate in ("content.json", "content.yml", "content.yaml")
            )
            excluded_roots = {
                child.name
                for child in source_path.iterdir()
                if child.is_dir() and (child / "library.json").exists()
            }
            source_root = source_path.resolve()
            for source_file in sorted(source_path.rglob("*")):
                if not source_file.is_file():
                    continue
                relative_path = source_file.resolve().relative_to(source_root).as_posix()
                first_segment = relative_path.split("/", 1)[0]
                if first_segment in excluded_roots:
                    continue
                destination_relative = self.normalize_h5p_source_asset_path(
                    relative_path,
                    content_root_only=content_root_only,
                )
                if destination_relative is None:
                    continue
                destination = target_dir / destination_relative
                self._ensure_directory(destination.parent)
                shutil.copyfile(source_file, destination)
        else:
            target_root = target_dir.resolve()
            with ZipFile(source_path) as archive:
                for member in archive.infolist():
                    if member.is_dir():
                        continue
                    destination_relative = self.normalize_h5p_source_asset_path(
                        member.filename,
                        content_root_only=True,
                    )
                    if destination_relative is None:
                        continue
                    destination = (target_dir / destination_relative).resolve()
                    if not str(destination).startswith(str(target_root)):
                        continue
                    self._ensure_directory(destination.parent)
                    destination.write_bytes(archive.read(member.filename))

        self._write_json(target_dir / "h5p.json", metadata_payload)
        self._write_h5p_content_files(target_dir, content_payload)

    def write_h5p_archive_from_directory(
        self,
        archive: ZipFile,
        source_dir: Path,
        *,
        shared_libraries: Iterable[Path] = (),
        shared_libraries_root: Path | None = None,
    ) -> None:
        content_payload: dict[str, object] | None = None
        try:
            content_payload = self._read_h5p_content_payload(source_dir)
        except (FileNotFoundError, ValueError):
            content_payload = None

        if content_payload is not None:
            archive.writestr("content/content.json", json.dumps(content_payload, ensure_ascii=False, indent=2) + "\n")

        for file_path in sorted(source_dir.rglob("*")):
            if not file_path.is_file():
                continue

            relative_path = file_path.relative_to(source_dir).as_posix()
            if relative_path == "h5p.json":
                archive_name = "h5p.json"
            elif relative_path in {"content.json", "content.yml", "content.yaml"}:
                continue
            else:
                archive_name = f"content/{relative_path}"
            archive.write(file_path, archive_name)

        library_root = shared_libraries_root or (self._courses_dir.parent / "libraries")
        for library_dir in shared_libraries:
            for file_path in sorted(library_dir.rglob("*")):
                if file_path.is_file():
                    archive.write(file_path, file_path.relative_to(library_root).as_posix())
