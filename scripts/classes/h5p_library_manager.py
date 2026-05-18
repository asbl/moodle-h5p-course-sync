from __future__ import annotations

import json
import shutil
import subprocess
import threading
from http import HTTPStatus
from pathlib import Path
from typing import Callable
from urllib.error import HTTPError
from zipfile import BadZipFile, ZipFile


class H5PLibraryManager:
    """Manages local H5P runtime libraries and their dependencies."""

    def __init__(
        self,
        *,
        workspace_lock: threading.RLock,
        runtime_dir: Path,
        runtime_content_dir: Path,
        runtime_libraries_dir: Path,
        runtime_downloads_dir: Path,
        shared_libraries_dir: Path,
        courses_dir: Path,
        release_repo: str,
        release_tag: str,
        asset_prefixes: dict[str, str],
        custom_short_names: dict[str, str],
        ensure_directory: Callable[[Path], None],
        read_json: Callable[[Path], dict],
        read_json_or_default: Callable[[Path, dict], dict],
        write_json: Callable[[Path, dict], None],
        fetch_json: Callable[[str], dict],
        download_file: Callable[[str, Path], None],
        run_cli_command: Callable[[list[str], Path], subprocess.CompletedProcess[str]] | None = None,
        resolve_cli_command: Callable[[], list[str]] | None = None,
    ) -> None:
        self._workspace_lock = workspace_lock
        self._runtime_dir = runtime_dir
        self._runtime_content_dir = runtime_content_dir
        self._runtime_libraries_dir = runtime_libraries_dir
        self._runtime_downloads_dir = runtime_downloads_dir
        self._shared_libraries_dir = shared_libraries_dir
        self._courses_dir = courses_dir
        self._release_repo = release_repo
        self._release_tag = release_tag
        self._asset_prefixes = asset_prefixes
        self._custom_short_names = custom_short_names
        self._ensure_directory = ensure_directory
        self._read_json = read_json
        self._read_json_or_default = read_json_or_default
        self._write_json = write_json
        self._fetch_json = fetch_json
        self._download_file = download_file
        self._run_cli_command = run_cli_command
        self._resolve_cli_command = resolve_cli_command

    def find_downloaded_asset(self, asset_prefix: str) -> Path | None:
        matches = sorted(self._runtime_downloads_dir.glob(f"{asset_prefix}*.h5p"))
        if not matches:
            return None
        return matches[-1]

    def release_metadata_cache_path(self) -> Path:
        return self._runtime_downloads_dir / f"release-{self._release_tag}.json"

    def load_release_assets(self) -> dict[str, str]:
        cache_path = self.release_metadata_cache_path()
        cached_release = self._read_json_or_default(cache_path, {})
        if cached_release:
            return {asset["name"]: asset["browser_download_url"] for asset in cached_release.get("assets", [])}

        try:
            release = self._fetch_json(
                f"https://api.github.com/repos/{self._release_repo}/releases/tags/{self._release_tag}"
            )
        except HTTPError as error:
            if error.code == HTTPStatus.FORBIDDEN:
                raise RuntimeError(
                    "GitHub API Rate-Limit erreicht und keine lokale Release-Metadatenkopie gefunden. "
                    "Falls die Libraries schon einmal geladen wurden, reicht ein vorhandener Inhalt in .h5p-runtime/downloads/."
                ) from error
            raise

        self._write_json(cache_path, release)
        return {asset["name"]: asset["browser_download_url"] for asset in release.get("assets", [])}

    def load_github_release(self, tag: str | None = None) -> dict:
        endpoint = f"releases/tags/{tag}" if tag else "releases/latest"
        try:
            return self._fetch_json(f"https://api.github.com/repos/{self._release_repo}/{endpoint}")
        except HTTPError as error:
            if error.code == HTTPStatus.FORBIDDEN:
                raise RuntimeError("GitHub API Rate-Limit erreicht. Bitte spaeter erneut versuchen.") from error
            raise

    def _asset_prefix_for_latest_release(self, machine_name: str) -> str:
        configured_prefix = self._asset_prefixes.get(machine_name, "")
        if configured_prefix:
            return configured_prefix.split("_", 1)[0].rsplit("-", 1)[0] + "-"
        return f"{machine_name}-"

    def _find_release_asset_name(self, assets: dict[str, str], machine_name: str) -> str:
        prefix = self._asset_prefix_for_latest_release(machine_name)
        candidates = sorted(name for name in assets if name.startswith(prefix) and name.endswith(".h5p"))
        if not candidates:
            raise RuntimeError(f"Release-Asset fuer {machine_name} mit Praefix '{prefix}' wurde nicht gefunden.")
        return candidates[-1]

    def _remove_library_dirs_for_machine_name(self, root: Path, machine_name: str) -> None:
        for library_dir in sorted(root.glob(f"{machine_name}-*")):
            if library_dir.is_dir():
                shutil.rmtree(library_dir)

    def _extract_library_root_from_archive_to(self, archive: ZipFile, library_root: str, destination_root: Path) -> Path:
        destination = destination_root / Path(library_root).name
        if destination.exists():
            shutil.rmtree(destination)

        extracted_root = False
        for member in archive.namelist():
            normalized = member.strip("/")
            if not normalized or not normalized.startswith(f"{library_root}/"):
                continue

            relative_path = Path(normalized).relative_to(library_root)
            if not relative_path.parts or relative_path.parts[0] == "content":
                continue

            target_path = destination / relative_path
            if normalized.endswith("/"):
                self._ensure_directory(target_path)
                continue

            self._ensure_directory(target_path.parent)
            with archive.open(member) as source, target_path.open("wb") as target:
                shutil.copyfileobj(source, target)
            extracted_root = True

        if not extracted_root:
            raise RuntimeError(f"Die Library '{Path(library_root).name}' konnte nicht aus dem Archiv extrahiert werden.")

        return destination

    def extract_library_asset_to(self, archive_path: Path, machine_name: str, destination_root: Path) -> Path:
        with ZipFile(archive_path) as archive:
            library_root = None
            for member in archive.namelist():
                normalized = member.strip("/")
                if not normalized.endswith("/library.json"):
                    continue
                metadata = json.loads(archive.read(member).decode("utf-8"))
                if metadata.get("machineName") == machine_name:
                    library_root = normalized.rsplit("/", 1)[0]
                    break

            if library_root is None:
                raise RuntimeError(f"Kein library.json fuer {machine_name} in {archive_path.name} gefunden.")
            return self._extract_library_root_from_archive_to(archive, library_root, destination_root)

    def update_custom_libraries_from_github(self, tag: str | None = None) -> list[dict[str, str]]:
        with self._workspace_lock:
            self._ensure_directory(self._runtime_downloads_dir)
            self._ensure_directory(self._runtime_libraries_dir)
            self._ensure_directory(self._shared_libraries_dir)

            release = self.load_github_release(tag)
            release_tag = str(release.get("tag_name") or tag or "latest")
            assets = {
                asset["name"]: asset["browser_download_url"]
                for asset in release.get("assets", [])
                if isinstance(asset, dict) and asset.get("name") and asset.get("browser_download_url")
            }
            if not assets:
                raise RuntimeError(f"GitHub-Release {release_tag} enthaelt keine downloadbaren Assets.")

            updated: list[dict[str, str]] = []
            for machine_name in self._asset_prefixes:
                asset_name = self._find_release_asset_name(assets, machine_name)
                archive_path = self._runtime_downloads_dir / asset_name
                if not archive_path.exists():
                    self._download_file(assets[asset_name], archive_path)

                self._remove_library_dirs_for_machine_name(self._shared_libraries_dir, machine_name)
                self._remove_library_dirs_for_machine_name(self._runtime_libraries_dir, machine_name)
                shared_library_dir = self.extract_library_asset_to(archive_path, machine_name, self._shared_libraries_dir)
                runtime_library_dir = self._copy_library_dir_to_runtime(shared_library_dir)
                self.register_local_library(runtime_library_dir)
                updated.append(
                    {
                        "machineName": machine_name,
                        "asset": asset_name,
                        "release": release_tag,
                        "path": str(shared_library_dir),
                    }
                )

            self.ensure_registered_local_libraries()
            return updated

    def get_h5p_cli_command(self) -> list[str]:
        if self._resolve_cli_command is not None:
            return self._resolve_cli_command()

        h5p_binary = shutil.which("h5p")
        if h5p_binary:
            return [h5p_binary]

        npx_binary = shutil.which("npx")
        if npx_binary:
            return [npx_binary, "--yes", "h5p-cli"]

        raise RuntimeError(
            "Für vollständige H5P-Pakete benötigt course_sync entweder 'h5p' oder 'npx' im PATH."
        )

    def run_h5p_cli(self, args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        if self._run_cli_command is not None:
            return self._run_cli_command(args, cwd)
        return subprocess.run(
            [*self.get_h5p_cli_command(), *args],
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
        )

    def _find_installed_library_dir(
        self,
        machine_name: str,
        major_version: int | None = None,
        minor_version: int | None = None,
    ) -> Path:
        if major_version is not None and minor_version is not None:
            candidate = self._runtime_libraries_dir / f"{machine_name}-{major_version}.{minor_version}"
            if candidate.exists():
                return candidate

        matches = sorted(self._runtime_libraries_dir.glob(f"{machine_name}-*"))
        if not matches:
            raise FileNotFoundError(
                f"H5P-Library '{machine_name}' wurde in {self._runtime_libraries_dir} nicht gefunden."
            )

        if major_version is None or minor_version is None:
            return matches[-1]

        for candidate in matches:
            metadata = self._read_json(candidate / "library.json")
            if metadata.get("majorVersion") == major_version and metadata.get("minorVersion") == minor_version:
                return candidate

        return matches[-1]

    def _library_matches_requested_version(
        self,
        library_dir: Path,
        major_version: int | None = None,
        minor_version: int | None = None,
    ) -> bool:
        if major_version is None or minor_version is None:
            return True

        library_json = library_dir / "library.json"
        if not library_json.exists():
            return False

        metadata = self._read_json(library_json)
        return metadata.get("majorVersion") == major_version and metadata.get("minorVersion") == minor_version

    def _copy_library_dir_to_runtime(self, library_dir: Path) -> Path:
        destination = self._runtime_libraries_dir / library_dir.name
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(library_dir, destination)
        return destination

    def _extract_library_root_from_archive(self, archive: ZipFile, library_root: str) -> Path:
        destination = self._runtime_libraries_dir / Path(library_root).name
        if destination.exists():
            shutil.rmtree(destination)

        extracted_root = False
        for member in archive.namelist():
            normalized = member.strip("/")
            if not normalized or not normalized.startswith(f"{library_root}/"):
                continue

            relative_path = Path(normalized).relative_to(library_root)
            if not relative_path.parts or relative_path.parts[0] == "content":
                continue

            target_path = destination / relative_path
            if normalized.endswith("/"):
                self._ensure_directory(target_path)
                continue

            self._ensure_directory(target_path.parent)
            with archive.open(member) as source, target_path.open("wb") as target:
                shutil.copyfileobj(source, target)
            extracted_root = True

        if not extracted_root:
            raise RuntimeError(f"Die Library '{Path(library_root).name}' konnte nicht aus dem Archiv extrahiert werden.")

        return destination

    def _seed_library_from_shared_libraries(
        self,
        machine_name: str,
        major_version: int | None = None,
        minor_version: int | None = None,
    ) -> Path | None:
        if not self._shared_libraries_dir.exists():
            return None

        if major_version is not None and minor_version is not None:
            exact_match = self._shared_libraries_dir / f"{machine_name}-{major_version}.{minor_version}"
            if exact_match.exists() and self._library_matches_requested_version(exact_match, major_version, minor_version):
                return self._copy_library_dir_to_runtime(exact_match)

        matches = sorted(self._shared_libraries_dir.glob(f"{machine_name}-*"))
        for candidate in matches:
            if self._library_matches_requested_version(candidate, major_version, minor_version):
                return self._copy_library_dir_to_runtime(candidate)

        if matches and (major_version is None or minor_version is None):
            return self._copy_library_dir_to_runtime(matches[-1])

        return None

    def _seed_library_from_local_archives(
        self,
        machine_name: str,
        major_version: int | None = None,
        minor_version: int | None = None,
    ) -> Path | None:
        if not self._courses_dir.exists():
            return None

        archive_paths = sorted(self._courses_dir.glob("*/build/h5p/**/*.h5p"))
        fallback_root: str | None = None
        fallback_archive: Path | None = None

        for archive_path in archive_paths:
            try:
                with ZipFile(archive_path) as archive:
                    for member in archive.namelist():
                        normalized = member.strip("/")
                        if not normalized.endswith("/library.json"):
                            continue

                        library_root = normalized.rsplit("/", 1)[0]
                        metadata = json.loads(archive.read(member).decode("utf-8"))
                        if metadata.get("machineName") != machine_name:
                            continue

                        if major_version is not None and minor_version is not None:
                            if (
                                metadata.get("majorVersion") == major_version
                                and metadata.get("minorVersion") == minor_version
                            ):
                                return self._extract_library_root_from_archive(archive, library_root)
                        elif fallback_root is None:
                            fallback_root = library_root
                            fallback_archive = archive_path
            except (BadZipFile, OSError, KeyError, json.JSONDecodeError):
                continue

        if fallback_root is None or fallback_archive is None:
            return None

        with ZipFile(fallback_archive) as archive:
            return self._extract_library_root_from_archive(archive, fallback_root)

    def _seed_runtime_library(
        self,
        machine_name: str,
        major_version: int | None = None,
        minor_version: int | None = None,
    ) -> None:
        seeded_library = self._seed_library_from_shared_libraries(machine_name, major_version, minor_version)
        if seeded_library is None:
            seeded_library = self._seed_library_from_local_archives(machine_name, major_version, minor_version)

        if seeded_library is not None:
            self.register_local_library(seeded_library)

    def find_library_dir(
        self,
        machine_name: str,
        major_version: int | None = None,
        minor_version: int | None = None,
    ) -> Path:
        try:
            return self._find_installed_library_dir(machine_name, major_version, minor_version)
        except FileNotFoundError:
            self._seed_runtime_library(machine_name, major_version, minor_version)
            return self._find_installed_library_dir(machine_name, major_version, minor_version)

    def extract_library_asset(self, archive_path: Path, machine_name: str) -> Path:
        with ZipFile(archive_path) as archive:
            library_root = None
            for member in archive.namelist():
                normalized = member.strip("/")
                if normalized.endswith("/library.json"):
                    library_root = normalized.rsplit("/", 1)[0]
                    break

            if library_root is None:
                raise RuntimeError(f"Kein library.json in {archive_path.name} gefunden.")
            return self._extract_library_root_from_archive(archive, library_root)

    def _update_registry_entry(self, library_json: dict, registry: dict) -> None:
        machine_name = library_json["machineName"]
        existing_entry = registry.get(machine_name, {})
        short_name = (
            self._custom_short_names.get(machine_name)
            or existing_entry.get("shortName")
            or machine_name.lower().replace(".", "-")
        )
        registry[machine_name] = {
            **existing_entry,
            "id": machine_name,
            "title": library_json.get("title", machine_name),
            "author": library_json.get("author", ""),
            "runnable": library_json.get("runnable", 0),
            "shortName": short_name,
        }

    def register_local_library(self, library_dir: Path) -> None:
        with self._workspace_lock:
            library_json = self._read_json(library_dir / "library.json")
            registry_path = self._runtime_dir / "libraryRegistry.json"
            registry = self._read_json_or_default(registry_path, {})
            self._update_registry_entry(library_json, registry)
            self._write_json(registry_path, registry)

    def ensure_custom_h5p_libraries(self) -> None:
        self._ensure_directory(self._runtime_downloads_dir)
        self._ensure_directory(self._runtime_libraries_dir)

        missing_machine_names = [
            machine_name
            for machine_name in self._asset_prefixes
            if not list(self._runtime_libraries_dir.glob(f"{machine_name}-*"))
        ]
        if not missing_machine_names:
            return

        assets: dict[str, str] | None = None

        for machine_name, asset_prefix in self._asset_prefixes.items():
            if machine_name not in missing_machine_names:
                continue

            archive_path = self.find_downloaded_asset(asset_prefix)
            if archive_path is None:
                if assets is None:
                    assets = self.load_release_assets()

                asset_name = next((name for name in assets if name.startswith(asset_prefix) and name.endswith(".h5p")), None)
                if asset_name is None:
                    raise RuntimeError(
                        f"Release-Asset für {machine_name} mit Präfix '{asset_prefix}' wurde nicht gefunden."
                    )

                archive_path = self._runtime_downloads_dir / asset_name
                if not archive_path.exists():
                    self._download_file(assets[asset_name], archive_path)

            library_dir = self.extract_library_asset(archive_path, machine_name)
            self.register_local_library(library_dir)

    def ensure_registered_local_libraries(self) -> None:
        with self._workspace_lock:
            registry_path = self._runtime_dir / "libraryRegistry.json"
            registry = self._read_json_or_default(registry_path, {})
            for library_dir in sorted(self._runtime_libraries_dir.glob("*")):
                library_json_path = library_dir / "library.json"
                if library_json_path.exists():
                    self._update_registry_entry(self._read_json(library_json_path), registry)
            self._write_json(registry_path, registry)

    def ensure_h5p_editor_dependencies(self) -> None:
        if not list(self._runtime_libraries_dir.glob("H5PEditor.ShowWhen-*")):
            self.run_h5p_cli(["setup", "h5p-editor-show-when"], cwd=self._runtime_dir)
        if not list(self._runtime_libraries_dir.glob("H5PEditor.DateTime-*")):
            self.run_h5p_cli(["setup", "h5p-editor-datetime"], cwd=self._runtime_dir)

    def ensure_h5p_math_display_registered(self) -> None:
        math_display_dirs = sorted(self._runtime_libraries_dir.glob("H5P.MathDisplay-*"))
        if not math_display_dirs:
            return
        self.register_local_library(math_display_dirs[-1])

    def ensure_h5p_runtime_libraries(self) -> None:
        with self._workspace_lock:
            self._ensure_directory(self._runtime_dir)
            self._ensure_directory(self._runtime_content_dir)
            self._ensure_directory(self._runtime_libraries_dir)

            core_marker = self._runtime_dir / ".core-ready"
            if not core_marker.exists():
                self.run_h5p_cli(["core"], cwd=self._runtime_dir)
                core_marker.write_text("ok\n", encoding="utf-8")

            self.ensure_custom_h5p_libraries()
            self.ensure_registered_local_libraries()
            self.ensure_h5p_math_display_registered()

            if not list(self._runtime_libraries_dir.glob("H5P.Question-*")):
                self.run_h5p_cli(["setup", "h5p-question"], cwd=self._runtime_dir)

            self.ensure_h5p_editor_dependencies()

    def collect_required_library_dirs(
        self,
        machine_name: str,
        major_version: int | None = None,
        minor_version: int | None = None,
        seen: set[str] | None = None,
    ) -> list[Path]:
        seen = seen or set()
        library_dir = self.find_library_dir(machine_name, major_version, minor_version)
        library_name = library_dir.name
        if library_name in seen:
            return []

        seen.add(library_name)
        metadata = self._read_json(library_dir / "library.json")
        required = [library_dir]
        for dependency in metadata.get("preloadedDependencies", []):
            required.extend(
                self.collect_required_library_dirs(
                    dependency["machineName"],
                    dependency.get("majorVersion"),
                    dependency.get("minorVersion"),
                    seen,
                )
            )
        return required

    def collect_required_library_dirs_from_metadata(self, metadata_payload: dict[str, object]) -> list[Path]:
        dependencies = metadata_payload.get("preloadedDependencies", [])
        if not isinstance(dependencies, list):
            return []

        required: list[Path] = []
        seen: set[str] = set()
        for dependency in dependencies:
            if not isinstance(dependency, dict):
                continue
            machine_name = str(dependency.get("machineName") or "").strip()
            if not machine_name:
                continue
            required.extend(
                self.collect_required_library_dirs(
                    machine_name,
                    int(dependency["majorVersion"]) if dependency.get("majorVersion") is not None else None,
                    int(dependency["minorVersion"]) if dependency.get("minorVersion") is not None else None,
                    seen,
                )
            )
        return required
