from __future__ import annotations

import json
import shutil
import subprocess
import fnmatch
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Iterable
from zipfile import BadZipFile, ZipFile

if TYPE_CHECKING:
    from scripts.classes.models import PythonQuestionBlock


class H5PFileService:
    """Handles H5P sidecar and archive file operations."""

    _IGNORED_ARCHIVE_PARTS = {"__pycache__", "node_modules", "editable-images"}
    _IGNORED_LIBRARY_FILENAMES = {
        "LICENSE",
        "README",
        "README.md",
        "package.json",
        "package-lock.json",
        "webpack.config.js",
        "eslint.config.js",
        "eslint.config.mjs",
        "vitest.config.mjs",
    }
    _EDITABLE_IMAGE_DIR = "editable-images"

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
        base_dir = Path("h5p")
        if question.h5p_subdir:
            base_dir /= question.h5p_subdir
        base_dir /= question.identifier
        return (base_dir / "h5p.json").as_posix(), (base_dir / "settings.yml").as_posix()

    def build_source_package_sidecar_path(self, question: PythonQuestionBlock) -> str:
        base_dir = Path("h5p")
        if question.h5p_subdir:
            base_dir /= question.h5p_subdir
        if question.raw_package:
            return (base_dir / f"{question.identifier}.h5p").as_posix()
        return (base_dir / question.identifier).as_posix()

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

        if question.raw_package:
            self._ensure_directory(target_path.parent)
            shutil.copy2(source_archive, target_path)
            return relative_path

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
        if normalized in {
            "h5p.json",
            "content.json",
            "content.yml",
            "content.yaml",
            "content.mdx",
            "settings.yml",
            "settings.yaml",
            "content/content.json",
        }:
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
                for candidate in ("content.json", "content.yml", "content.yaml", "content.mdx")
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
        self.render_editable_images(source_dir)
        written_entries: set[str] = set()
        content_payload: dict[str, object] | None = None
        try:
            content_payload = self._read_h5p_content_payload(source_dir)
        except (FileNotFoundError, ValueError):
            content_payload = None

        if content_payload is not None:
            archive_name = "content/content.json"
            archive.writestr(archive_name, json.dumps(content_payload, ensure_ascii=False, indent=2) + "\n")
            written_entries.add(archive_name)

        for file_path in sorted(source_dir.rglob("*")):
            if not file_path.is_file():
                continue

            relative_path = file_path.relative_to(source_dir).as_posix()
            if self._should_skip_archive_path(relative_path):
                continue
            if relative_path == "h5p.json":
                archive_name = "h5p.json"
            elif relative_path in {"content.json", "content.yml", "content.yaml", "content.mdx", "settings.yml", "settings.yaml"}:
                continue
            else:
                archive_name = f"content/{relative_path}"
            if archive_name in written_entries:
                continue
            archive.write(file_path, archive_name)
            written_entries.add(archive_name)

        library_root = shared_libraries_root or (self._courses_dir.parent / "libraries")
        for library_dir in shared_libraries:
            for file_path in sorted(library_dir.rglob("*")):
                if file_path.is_file():
                    relative_archive_path = file_path.relative_to(library_root).as_posix()
                    if self._should_skip_archive_path(relative_archive_path, archive_root=library_root):
                        continue
                    if relative_archive_path in written_entries:
                        continue
                    archive.write(file_path, relative_archive_path)
                    written_entries.add(relative_archive_path)

    def _should_skip_archive_path(self, relative_path: str, archive_root: Path | None = None) -> bool:
        parts = Path(relative_path).parts
        for part in parts:
            if part in self._IGNORED_ARCHIVE_PARTS:
                return True
            if part.startswith("."):
                return True
        if archive_root is not None and self._should_skip_library_archive_path(relative_path, archive_root):
            return True
        return False

    def _should_skip_library_archive_path(self, relative_path: str, library_root: Path) -> bool:
        parts = Path(relative_path).parts
        if len(parts) < 2:
            return False

        library_dir = library_root / parts[0]
        library_relative_path = Path(*parts[1:]).as_posix()
        if Path(library_relative_path).name in self._IGNORED_LIBRARY_FILENAMES:
            return True

        ignore_file = library_dir / ".h5pignore"
        if not ignore_file.exists():
            return False

        for raw_line in ignore_file.read_text(encoding="utf-8").splitlines():
            pattern = raw_line.strip().lstrip("./")
            if not pattern or pattern.startswith("#"):
                continue
            if self._matches_h5pignore_pattern(library_relative_path, pattern):
                return True
        return False

    def _matches_h5pignore_pattern(self, relative_path: str, pattern: str) -> bool:
        basename = Path(relative_path).name
        if fnmatch.fnmatch(relative_path, pattern) or fnmatch.fnmatch(basename, pattern):
            return True
        return relative_path.startswith(pattern.rstrip("/") + "/")

    def render_editable_images(self, source_dir: Path) -> list[Path]:
        editable_dir = source_dir / self._EDITABLE_IMAGE_DIR
        if not editable_dir.is_dir():
            return []

        rendered: list[Path] = []
        for source_path in sorted(editable_dir.glob("*.svg")):
            target_path = source_dir / "images" / f"{source_path.stem}.png"
            if target_path.exists() and target_path.stat().st_mtime_ns >= source_path.stat().st_mtime_ns:
                continue
            self._ensure_directory(target_path.parent)
            self._render_svg_to_png(source_path, target_path)
            rendered.append(target_path)
        return rendered

    def _render_svg_to_png(self, source_path: Path, target_path: Path) -> None:
        for candidate in ("magick", "convert"):
            executable = shutil.which(candidate)
            if executable:
                subprocess.run([executable, str(source_path), str(target_path)], check=True)
                return
        raise RuntimeError(
            "Editierbare H5P-Bilder koennen nicht gerendert werden: "
            "ImageMagick fehlt. Installiere 'magick' oder 'convert'."
        )
