from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from threading import RLock
from typing import Callable, Iterable
from zipfile import ZIP_DEFLATED, ZipFile

from .models import PythonQuestionBlock


class H5PPackageBuilder:
    """Build and materialize local H5P packages for a question.

    The builder uses three strategies internally:
    - imported/source package merge
    - raw package download
    - scratch package assembly
    """

    def __init__(
        self,
        *,
        workspace_lock: RLock,
        python_question_machine_name: str,
        ensure_directory: Callable[[Path], None],
        source_tree_mtime_ns: Callable[[Path | None], int],
        download_file: Callable[[str, Path], None],
        populate_imported_h5p_directory: Callable[[Path, Path, dict[str, object], dict[str, object]], None],
        collect_required_library_dirs_from_metadata: Callable[[dict[str, object]], list[Path]],
        collect_required_library_dirs: Callable[..., list[Path]],
        write_h5p_archive_from_directory: Callable[..., None],
        write_h5p_content_files: Callable[[Path, dict[str, object]], None],
        ensure_h5p_runtime_libraries: Callable[[], None],
        build_h5p_content: Callable[[PythonQuestionBlock], dict],
        read_json: Callable[[Path], dict],
        find_library_dir: Callable[..., Path],
    ) -> None:
        self._workspace_lock = workspace_lock
        self._python_question_machine_name = python_question_machine_name
        self._ensure_directory = ensure_directory
        self._source_tree_mtime_ns = source_tree_mtime_ns
        self._download_file = download_file
        self._populate_imported_h5p_directory = populate_imported_h5p_directory
        self._collect_required_library_dirs_from_metadata = collect_required_library_dirs_from_metadata
        self._collect_required_library_dirs = collect_required_library_dirs
        self._write_h5p_archive_from_directory = write_h5p_archive_from_directory
        self._write_h5p_content_files = write_h5p_content_files
        self._ensure_h5p_runtime_libraries = ensure_h5p_runtime_libraries
        self._build_h5p_content = build_h5p_content
        self._read_json = read_json
        self._find_library_dir = find_library_dir

    def build_h5p_metadata(self, question: PythonQuestionBlock) -> dict:
        library_metadata = self._read_json(self._find_library_dir(question.main_library) / "library.json")
        preloaded_dependencies = [
            {
                "machineName": library_metadata["machineName"],
                "majorVersion": library_metadata["majorVersion"],
                "minorVersion": library_metadata["minorVersion"],
            }
        ]
        preloaded_dependencies.extend(
            {
                "machineName": dependency["machineName"],
                "majorVersion": dependency["majorVersion"],
                "minorVersion": dependency["minorVersion"],
            }
            for dependency in library_metadata.get("preloadedDependencies", [])
        )
        return {
            "title": question.title,
            "language": "de",
            "defaultLanguage": "de",
            "mainLibrary": question.main_library,
            "embedTypes": ["div"],
            "license": "U",
            "preloadedDependencies": preloaded_dependencies,
            "majorVersion": library_metadata["majorVersion"],
            "minorVersion": library_metadata["minorVersion"],
        }

    def build_h5p_content(self, question: PythonQuestionBlock) -> dict:
        return self._build_h5p_content(question)

    def sync_shared_h5p_libraries(self, question: PythonQuestionBlock, required_libraries: Iterable[Path]) -> list[Path]:
        self._ensure_directory(question.shared_libraries_dir)
        shared_libraries: list[Path] = []
        for library_dir in required_libraries:
            destination = question.shared_libraries_dir / library_dir.name
            if not destination.exists():
                shutil.copytree(library_dir, destination)
            shared_libraries.append(destination)
        return shared_libraries

    def write_h5p_package(self, question: PythonQuestionBlock) -> Path:
        with self._workspace_lock:
            self._ensure_directory(question.package_path.parent)

            source_archive_path = (question.course_dir / question.source_package_path) if question.source_package_path else None
            source_mtime_ns = self._source_tree_mtime_ns(source_archive_path)
            index_mtime_ns = (question.course_dir / "index.mdx").stat().st_mtime_ns
            freshness_reference = max(index_mtime_ns, source_mtime_ns)

            if (question.package_url or source_archive_path is not None) and question.h5p_metadata is not None and question.h5p_content is not None:
                if question.package_path.exists() and question.package_path.stat().st_mtime_ns >= freshness_reference:
                    return question.package_path

                self._ensure_h5p_runtime_libraries()
                metadata_payload = json.loads(json.dumps(question.h5p_metadata, ensure_ascii=False))
                content_payload = json.loads(json.dumps(question.h5p_content, ensure_ascii=False))
                metadata_payload["title"] = question.title
                metadata_payload["mainLibrary"] = question.main_library
                if question.main_library == self._python_question_machine_name:
                    metadata_payload = self._normalize_imported_python_question_metadata(question, metadata_payload)
                if "pythonRunner" in content_payload or question.main_library == self._python_question_machine_name:
                    content_payload["pythonRunner"] = question.runner

                with tempfile.TemporaryDirectory() as temp_dir:
                    source_path = Path(temp_dir) / "source"
                    if source_archive_path is not None and source_archive_path.exists():
                        if source_archive_path.is_dir():
                            shutil.copytree(source_archive_path, source_path)
                        else:
                            shutil.copyfile(source_archive_path, source_path)
                    else:
                        source_path = Path(temp_dir) / "original.h5p"
                        self._download_file(question.package_url, source_path)

                    self._populate_imported_h5p_directory(source_path, question.exploded_dir, metadata_payload, content_payload)
                    shared_libraries = self.sync_shared_h5p_libraries(
                        question,
                        self._collect_required_library_dirs_from_metadata(metadata_payload),
                    )

                    with ZipFile(question.package_path, "w", compression=ZIP_DEFLATED) as target_archive:
                        self._write_h5p_archive_from_directory(
                            target_archive,
                            question.exploded_dir,
                            shared_libraries=shared_libraries,
                            shared_libraries_root=question.shared_libraries_dir,
                        )

                return question.package_path

            if question.package_url and (question.raw_package or question.main_library != self._python_question_machine_name):
                if question.package_path.exists() and question.package_path.stat().st_mtime_ns >= freshness_reference:
                    return question.package_path
                self._download_file(question.package_url, question.package_path)
                return question.package_path

            self._ensure_h5p_runtime_libraries()
            if question.exploded_dir.exists():
                shutil.rmtree(question.exploded_dir)
            self._ensure_directory(question.exploded_dir)

            h5p_json = self.build_h5p_metadata(question)
            content_json = self._build_h5p_content(question)
            required_libraries = self._collect_required_library_dirs(question.main_library)
            shared_libraries = self.sync_shared_h5p_libraries(question, required_libraries)

            self._ensure_directory(question.exploded_dir)
            (question.exploded_dir / "h5p.json").write_text(json.dumps(h5p_json, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            self._write_h5p_content_files(question.exploded_dir, content_json)

            with ZipFile(question.package_path, "w", compression=ZIP_DEFLATED) as archive:
                self._write_h5p_archive_from_directory(
                    archive,
                    question.exploded_dir,
                    shared_libraries=shared_libraries,
                    shared_libraries_root=question.shared_libraries_dir,
                )

            return question.package_path

    def _normalize_imported_python_question_metadata(
        self,
        question: PythonQuestionBlock,
        metadata_payload: dict[str, object],
    ) -> dict[str, object]:
        """Fill required H5P metadata fields when imported sidecar metadata is incomplete."""
        baseline = self.build_h5p_metadata(question)

        dependencies = metadata_payload.get("preloadedDependencies")
        if not isinstance(dependencies, list) or not dependencies:
            metadata_payload["preloadedDependencies"] = baseline["preloadedDependencies"]

        if not isinstance(metadata_payload.get("majorVersion"), int):
            metadata_payload["majorVersion"] = baseline["majorVersion"]
        if not isinstance(metadata_payload.get("minorVersion"), int):
            metadata_payload["minorVersion"] = baseline["minorVersion"]
        if not isinstance(metadata_payload.get("embedTypes"), list):
            metadata_payload["embedTypes"] = baseline["embedTypes"]

        metadata_payload.setdefault("language", baseline["language"])
        metadata_payload.setdefault("defaultLanguage", baseline["defaultLanguage"])
        metadata_payload.setdefault("license", baseline["license"])
        return metadata_payload
