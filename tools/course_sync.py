from __future__ import annotations

import argparse
import hashlib
import html
import http.client
import json
import mimetypes
import os
import re
import shutil
import socket
import subprocess
import tarfile
import tempfile
import textwrap
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterable
from urllib.error import HTTPError
from urllib.parse import parse_qs, quote, urlencode
from urllib.request import Request, urlopen
from urllib.parse import unquote, urljoin, urlparse, urlunparse
from xml.etree import ElementTree
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile

from tools.course_sync_mvc import ContentStore, PreviewViewBuilder, RuntimePreparationService


ROOT_DIR = Path(__file__).resolve().parent.parent
COURSES_DIR = ROOT_DIR / "courses"
DEFAULT_PORT = 8765
DOTENV_FILE = ROOT_DIR / ".env"
H5P_RUNTIME_DIR = ROOT_DIR / ".h5p-runtime"
H5P_RUNTIME_CONTENT_DIR = H5P_RUNTIME_DIR / "content"
H5P_RUNTIME_LIBRARIES_DIR = H5P_RUNTIME_DIR / "libraries"
H5P_RUNTIME_DOWNLOADS_DIR = H5P_RUNTIME_DIR / "downloads"
H5P_RUNTIME_PORT = 8766
RUNTIME_PROXY_PREFIX = "/runtime"
H5P_LIBRARY_RELEASE_REPO = "asbl/h5p-content-python-question"
H5P_LIBRARY_RELEASE_TAG = "v6.73.0"
H5P_LIBRARY_ASSET_PREFIXES = {
    "H5P.PythonQuestion": "H5P.PythonQuestion-6.73_",
    "H5P.CodeQuestion": "H5P.CodeQuestion-6.73_",
    "H5P.LibCodeTools": "H5P.LibCodeTools-6.73_",
    "H5PEditor.CodeWidget": "H5PEditor.CodeWidget-6.73_",
}
CUSTOM_H5P_LIBRARY_SHORT_NAMES = {
    "H5P.PythonQuestion": "h5p-python-question",
    "H5P.CodeQuestion": "h5p-code-question",
    "H5P.LibCodeTools": "h5p-lib-code-tools",
    "H5PEditor.CodeWidget": "h5p-editor-code-widget",
    "H5P.MathDisplay": "h5p-math-display",
}
PYTHON_QUESTION_MACHINE_NAME = "H5P.PythonQuestion"
PLACEHOLDER_TEMPLATE = "[[[PYTHON_QUESTION:{identifier}]]]"
SYNC_METADATA_FILE = ".course-sync.json"
H5P_SIDECAR_DIRNAME = "h5p-imports"
WORKSPACE_LOCK = threading.RLock()
PREVIEW_CACHE: dict[str, tuple[int, list[PythonQuestionBlock], str]] = {}
CONTENT_STORE = ContentStore()
RUNTIME_PREPARATION = RuntimePreparationService(H5P_RUNTIME_CONTENT_DIR)
PREVIEW_VIEW_BUILDER: PreviewViewBuilder | None = None

TAG_RE = re.compile(r"<PythonQuestion(?P<attrs>.*?)\/>", re.DOTALL)
FENCE_RE = re.compile(r"```(?P<spec>[^\n`]*)\n(?P<body>.*?)\n```", re.DOTALL)
HTML_TAG_RE = re.compile(r"<[^>]+>")
WHITESPACE_RE = re.compile(r"\s+")
H5P_EMBED_IFRAME_RE = re.compile(r'<iframe[^>]+src="(?P<src>[^"]+/h5p/embed\.php\?[^"]+)"', re.IGNORECASE)
MBZ_LINK_RE = re.compile(r'https?://[^"\']+\.mbz', re.IGNORECASE)


@dataclass(slots=True)
class TestCase:
    hidden: bool = False
    inputs: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)


@dataclass(slots=True)
class SourceFile:
    file_name: str
    code: str
    visible_to_learner: bool = True
    learner_editable: bool = True


@dataclass(slots=True)
class PythonQuestionBlock:
    identifier: str
    title: str
    instructions: str
    preview_url: str = ""
    main_library: str = PYTHON_QUESTION_MACHINE_NAME
    package_url: str = ""
    raw_package: bool = False
    h5p_metadata: dict[str, object] | None = None
    h5p_content: dict[str, object] | None = None
    h5p_metadata_path: str = ""
    h5p_content_path: str = ""
    source_package_path: str = ""
    runner: str = "pyodide"
    packages: list[str] = field(default_factory=list)
    starter_code: str = ""
    solution_code: str = ""
    pre_code: str = ""
    post_code: str = ""
    grading_method: str = "please_choose"
    show_console: bool = True
    allow_adding_files: bool = False
    source_files: list[SourceFile] = field(default_factory=list)
    test_cases: list[TestCase] = field(default_factory=list)
    course_dir: Path | None = None

    @property
    def package_path(self) -> Path:
        course_dir = self.course_dir or (COURSES_DIR / self.course_slug)
        return course_dir / "h5p" / f"{self.identifier}.h5p"

    @property
    def h5p_dir(self) -> Path:
        course_dir = self.course_dir or (COURSES_DIR / self.course_slug)
        return course_dir / "h5p"

    @property
    def exploded_dir(self) -> Path:
        return self.h5p_dir / self.identifier

    @property
    def shared_libraries_dir(self) -> Path:
        return COURSES_DIR.parent / "libraries"

    @property
    def runtime_content_id(self) -> str:
        return build_runtime_content_id(self.course_slug, self.identifier)

    course_slug: str = ""


@dataclass(slots=True)
class MoodleH5PActivity:
    identifier: str
    title: str
    course_id: int
    activity_id: int
    instance_id: int | None
    section_title: str = ""
    intro: str = ""
    url: str = ""
    visible: bool = True
    package_url: str = ""
    imported_question: PythonQuestionBlock | None = None


@dataclass(slots=True)
class SyncMetadataEntry:
    identifier: str
    remote_activity_id: int
    remote_instance_id: int | None
    remote_title: str
    remote_url: str
    remote_visible: bool
    local_hash: str = ""
    remote_hash: str = ""
    status: str = "tracked"

    def to_dict(self) -> dict[str, object]:
        return {
            "identifier": self.identifier,
            "remoteActivityId": self.remote_activity_id,
            "remoteInstanceId": self.remote_instance_id,
            "remoteTitle": self.remote_title,
            "remoteUrl": self.remote_url,
            "remoteVisible": self.remote_visible,
            "localHash": self.local_hash,
            "remoteHash": self.remote_hash,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> SyncMetadataEntry:
        return cls(
            identifier=str(payload["identifier"]),
            remote_activity_id=int(payload["remoteActivityId"]),
            remote_instance_id=int(payload["remoteInstanceId"]) if payload.get("remoteInstanceId") is not None else None,
            remote_title=str(payload.get("remoteTitle", "")),
            remote_url=str(payload.get("remoteUrl", "")),
            remote_visible=bool(payload.get("remoteVisible", True)),
            local_hash=str(payload.get("localHash", "")),
            remote_hash=str(payload.get("remoteHash", "")),
            status=str(payload.get("status", "tracked")),
        )


@dataclass(slots=True)
class SyncMetadata:
    course_slug: str
    remote_course_id: int
    moodle_base_url: str
    entries: dict[str, SyncMetadataEntry] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "version": 1,
            "course": self.course_slug,
            "remoteCourseId": self.remote_course_id,
            "moodleBaseUrl": self.moodle_base_url,
            "entries": [entry.to_dict() for entry in sorted(self.entries.values(), key=lambda item: item.identifier)],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> SyncMetadata:
        entries = {
            entry.identifier: entry
            for entry in [SyncMetadataEntry.from_dict(item) for item in payload.get("entries", [])]
        }
        return cls(
            course_slug=str(payload.get("course", "")),
            remote_course_id=int(payload.get("remoteCourseId", 0)),
            moodle_base_url=str(payload.get("moodleBaseUrl", "")),
            entries=entries,
        )


class MoodleApiClient:
    def __init__(self, base_url: str, token: str):
        self.base_url = base_url.rstrip("/")
        self.token = token.strip()
        if not self.base_url:
            raise ValueError("Moodle-Basis-URL fehlt.")
        if not self.token:
            raise ValueError("Moodle-Token fehlt.")

    def call(self, function_name: str, **params: object) -> object:
        query = {
            "wstoken": self.token,
            "wsfunction": function_name,
            "moodlewsrestformat": "json",
        }
        query.update({key: value for key, value in params.items() if value is not None})
        url = f"{self.base_url}/webservice/rest/server.php?{urlencode(query, doseq=True)}"
        request = Request(url, headers={"User-Agent": "course-sync"})
        with urlopen(request) as response:
            payload = json.loads(response.read().decode("utf-8"))

        if isinstance(payload, dict) and payload.get("exception"):
            raise RuntimeError(f"Moodle-API-Fehler: {payload.get('message', payload['exception'])}")

        return payload

    def list_course_h5p_activities(self, course_id: int) -> list[MoodleH5PActivity]:
        payload = self.call("core_course_get_contents", courseid=course_id)
        if not isinstance(payload, list):
            raise RuntimeError("Unerwartete Moodle-Antwort für core_course_get_contents.")

        identifiers: set[str] = set()
        activities: list[MoodleH5PActivity] = []
        for section in payload:
            if not isinstance(section, dict):
                continue
            section_title = str(section.get("name") or section.get("section") or "").strip()
            modules = section.get("modules", [])
            if not isinstance(modules, list):
                continue
            for module in modules:
                if not isinstance(module, dict):
                    continue
                if module.get("modname") != "h5pactivity":
                    continue
                title = str(module.get("name") or f"h5p-{module.get('id', 'unknown')}").strip()
                identifier = make_stable_identifier(title, identifiers)
                activities.append(
                    MoodleH5PActivity(
                        identifier=identifier,
                        title=title,
                        course_id=course_id,
                        activity_id=int(module["id"]),
                        instance_id=int(module["instance"]) if module.get("instance") is not None else None,
                        section_title=section_title,
                        intro=strip_html(str(module.get("description") or "")),
                        url=str(module.get("url") or ""),
                        visible=bool(module.get("visible", True)),
                    )
                )
        return activities

    def get_site_info(self) -> dict[str, object]:
        payload = self.call("core_webservice_get_site_info")
        if not isinstance(payload, dict):
            raise RuntimeError("Unerwartete Moodle-Antwort für core_webservice_get_site_info.")
        return payload

    def download_activity_question(self, course_slug: str, activity: MoodleH5PActivity) -> PythonQuestionBlock | None:
        archive_path = None

        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                archive_path = Path(temp_dir) / f"{activity.identifier}.h5p"
                package_url = ""
                if activity.url:
                    page_html = fetch_text(activity.url)
                    package_url = extract_h5p_package_url_from_activity_html(page_html, base_url=self.base_url)
                if package_url:
                    activity.package_url = package_url
                    download_file(package_url, archive_path)
                else:
                    if not extract_h5p_package_from_course_backup(self.base_url, activity, archive_path):
                        return None
                with ZipFile(archive_path) as archive:
                    metadata_payload = json.loads(archive.read("h5p.json").decode("utf-8"))
                    content_payload = json.loads(archive.read("content/content.json").decode("utf-8"))
                if not isinstance(metadata_payload, dict) or not isinstance(content_payload, dict):
                    return None
                question = build_imported_question_from_h5p_package(course_slug, activity, metadata_payload, content_payload)
                if question is not None:
                    question.source_package_path = write_source_package_sidecar(question, archive_path)
                return question
        except (BadZipFile, OSError, KeyError, json.JSONDecodeError):
            return None


def normalize_whitespace(value: str) -> str:
    return textwrap.dedent(value).strip()


def load_dotenv_file(dotenv_path: Path | None = None) -> None:
    dotenv_path = dotenv_path or DOTENV_FILE
    if not dotenv_path.exists():
        return

    for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or key in os.environ:
            continue

        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        os.environ[key] = value


def strip_html(value: str) -> str:
    return html.unescape(HTML_TAG_RE.sub(" ", value)).strip()


def compact_text(value: str) -> str:
    return WHITESPACE_RE.sub(" ", html.unescape(value)).strip()


def slugify_identifier(value: str) -> str:
    normalized = value.strip().lower()
    for source, target in {
        "ä": "ae",
        "ö": "oe",
        "ü": "ue",
        "ß": "ss",
    }.items():
        normalized = normalized.replace(source, target)
    normalized = unicodedata.normalize("NFKD", normalized).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", normalized).strip("-")
    return slug or "h5p"


def make_stable_identifier(title: str, existing_identifiers: set[str]) -> str:
    base = slugify_identifier(title)
    identifier = base
    suffix = 2
    while identifier in existing_identifiers:
        identifier = f"{base}-{suffix}"
        suffix += 1
    existing_identifiers.add(identifier)
    return identifier


def fetch_text(url: str) -> str:
    request = Request(url, headers={"User-Agent": "course-sync"})
    with urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8")


def normalize_http_url(url: str) -> str:
    parsed = urlparse(url)
    normalized_path = quote(parsed.path, safe="/%")
    normalized_query = quote(parsed.query, safe="=&%/:+,-_.~")
    return urlunparse((parsed.scheme, parsed.netloc, normalized_path, parsed.params, normalized_query, parsed.fragment))


def extract_h5p_package_url_from_activity_html(page_html: str, *, base_url: str = "") -> str:
    unescaped_html = html.unescape(page_html)
    iframe_match = H5P_EMBED_IFRAME_RE.search(unescaped_html)
    if not iframe_match:
        return ""

    iframe_src = urljoin(base_url, iframe_match.group("src"))
    iframe_query = parse_qs(urlparse(iframe_src).query)
    package_url = iframe_query.get("url", [""])[0]
    return unquote(package_url).strip()


def discover_course_backup_url(base_url: str, course_id: int, activity_url: str = "") -> str:
    search_urls = []
    if activity_url:
        search_urls.append(activity_url)
    search_urls.append(f"{base_url.rstrip('/')}/course/view.php?id={course_id}")

    for url in search_urls:
        try:
            page_html = fetch_text(url)
        except OSError:
            continue
        match = MBZ_LINK_RE.search(page_html)
        if match:
            return html.unescape(match.group(0))
    return ""


def parse_backup_activity_directory(backup_path: Path, activity: MoodleH5PActivity) -> str:
    with tarfile.open(backup_path, "r:gz") as archive:
        backup_xml = archive.extractfile("moodle_backup.xml")
        if backup_xml is None:
            return ""
        root = ElementTree.fromstring(backup_xml.read())

    for activity_node in root.findall(".//activity"):
        title = (activity_node.findtext("title") or "").strip()
        modulename = (activity_node.findtext("modulename") or "").strip()
        directory = (activity_node.findtext("directory") or "").strip()
        if modulename != "h5pactivity":
            continue
        if title == activity.title and directory:
            return directory
    return ""


def extract_backup_file_records(backup_path: Path, file_ids: set[str]) -> dict[str, dict[str, str]]:
    if not file_ids:
        return {}

    with tarfile.open(backup_path, "r:gz") as archive:
        files_xml = archive.extractfile("files.xml")
        if files_xml is None:
            return {}
        root = ElementTree.fromstring(files_xml.read())

    records: dict[str, dict[str, str]] = {}
    for file_node in root.findall(".//file"):
        file_id = file_node.get("id") or ""
        if file_id not in file_ids:
            continue
        records[file_id] = {
            "contenthash": (file_node.findtext("contenthash") or "").strip(),
            "filename": (file_node.findtext("filename") or "").strip(),
            "component": (file_node.findtext("component") or "").strip(),
            "filearea": (file_node.findtext("filearea") or "").strip(),
        }
    return records


def extract_h5p_package_from_backup_activity(backup_path: Path, activity_dir: str, destination: Path) -> bool:
    with tarfile.open(backup_path, "r:gz") as archive:
        inforef_member = archive.extractfile(f"{activity_dir}/inforef.xml")
        if inforef_member is None:
            return False
        inforef_root = ElementTree.fromstring(inforef_member.read())
        file_ids = {
            (file_node.findtext("id") or "").strip()
            for file_node in inforef_root.findall(".//fileref/file")
            if (file_node.findtext("id") or "").strip()
        }

    file_records = extract_backup_file_records(backup_path, file_ids)
    package_record = next(
        (
            record
            for record in file_records.values()
            if record.get("component") == "mod_h5pactivity"
            and record.get("filearea") == "package"
            and record.get("filename") not in {"", "."}
            and record.get("contenthash")
        ),
        None,
    )
    if package_record is None:
        return False

    content_hash = package_record["contenthash"]
    with tarfile.open(backup_path, "r:gz") as archive:
        source_member = None
        for tar_member_name in [
            f"files/{content_hash[:2]}/{content_hash[2:4]}/{content_hash}",
            f"files/{content_hash[:2]}/{content_hash}",
        ]:
            try:
                source_member = archive.extractfile(tar_member_name)
            except KeyError:
                source_member = None
            if source_member is not None:
                break
        if source_member is None:
            return False
        ensure_directory(destination.parent)
        with destination.open("wb") as target:
            shutil.copyfileobj(source_member, target)
    return True


def extract_h5p_package_from_course_backup(base_url: str, activity: MoodleH5PActivity, destination: Path) -> bool:
    backup_url = discover_course_backup_url(base_url, activity.course_id, activity.url)
    if not backup_url:
        return False

    with tempfile.TemporaryDirectory() as temp_dir:
        backup_path = Path(temp_dir) / f"course-{activity.course_id}.mbz"
        download_file(backup_url, backup_path)
        activity_dir = parse_backup_activity_directory(backup_path, activity)
        if not activity_dir:
            return False
        return extract_h5p_package_from_backup_activity(backup_path, activity_dir, destination)


def extract_h5p_packages(content_payload: dict[str, object]) -> list[str]:
    packages: list[str] = []
    pyodide_options = content_payload.get("pyodideOptions", {})
    if not isinstance(pyodide_options, dict):
        return packages

    for entry in pyodide_options.get("packages", []) or []:
        if isinstance(entry, dict):
            package_name = str(entry.get("package") or entry.get("name") or "").strip()
        else:
            package_name = str(entry).strip()
        if package_name and package_name not in packages:
            packages.append(package_name)
    return packages


def summarize_h5p_instructions(activity: MoodleH5PActivity, content_payload: dict[str, object]) -> str:
    editor_settings = content_payload.get("editorSettings", {})
    if isinstance(editor_settings, dict):
        editor_instructions = compact_text(str(editor_settings.get("instructions") or ""))
        if editor_instructions:
            return editor_instructions

    content_fragments: list[str] = []
    for entry in content_payload.get("contents", []) or []:
        if not isinstance(entry, dict):
            continue
        text = compact_text(str(entry.get("text") or ""))
        if text:
            content_fragments.append(text)

    if content_fragments:
        return " ".join(content_fragments)
    if activity.intro:
        return compact_text(activity.intro)
    return f"Importiert aus Moodle: {activity.title}"


def extract_h5p_editor_instructions(content_payload: dict[str, object]) -> str:
    editor_settings = content_payload.get("editorSettings", {})
    if not isinstance(editor_settings, dict):
        return ""
    raw_instructions = html.unescape(str(editor_settings.get("instructions") or ""))
    return normalize_whitespace(raw_instructions)


def extract_test_case_values(raw_values: object, *, field_name: str) -> list[str]:
    if not isinstance(raw_values, list):
        return []

    values: list[str] = []
    for entry in raw_values:
        if isinstance(entry, dict):
            raw_value = entry.get(field_name)
            if raw_value is None:
                continue
            values.append(str(raw_value))
            continue
        values.append(str(entry))
    return values


def extract_source_files(editor_options: dict[str, object]) -> list[SourceFile]:
    source_files: list[SourceFile] = []
    raw_files = editor_options.get("sourceFiles", [])
    if not isinstance(raw_files, list):
        return source_files

    for index, entry in enumerate(raw_files, start=1):
        if not isinstance(entry, dict):
            continue

        code = normalize_whitespace(html.unescape(str(entry.get("code") or "")))
        file_name = str(entry.get("fileName") or "").strip()
        if not file_name:
            if not code:
                continue
            file_name = f"source-{index}.py"

        source_files.append(
            SourceFile(
                file_name=file_name,
                code=code,
                visible_to_learner=bool(entry.get("visibleToLearner", True)),
                learner_editable=bool(entry.get("learnerEditable", True)),
            )
        )

    return source_files


def build_h5p_sidecar_paths(question: PythonQuestionBlock) -> tuple[str, str]:
    base_dir = Path("h5p") / question.identifier
    return (base_dir / "h5p.json").as_posix(), (base_dir / "content.yml").as_posix()


def build_source_package_sidecar_path(question: PythonQuestionBlock) -> str:
    return (Path("h5p") / question.identifier).as_posix()


def build_legacy_source_archive_sidecar_path(question: PythonQuestionBlock) -> str:
    return (Path(H5P_SIDECAR_DIRNAME) / question.identifier / "source.h5p").as_posix()


def write_h5p_sidecar_files(question: PythonQuestionBlock) -> tuple[str, str]:
    if question.course_dir is None or question.h5p_metadata is None or question.h5p_content is None:
        return question.h5p_metadata_path, question.h5p_content_path

    metadata_rel, content_rel = build_h5p_sidecar_paths(question)
    metadata_path = question.course_dir / metadata_rel
    content_path = question.course_dir / content_rel
    write_json(metadata_path, question.h5p_metadata)
    write_h5p_content_files(content_path.parent, question.h5p_content)
    return metadata_rel, content_rel


def write_source_package_sidecar(question: PythonQuestionBlock, source_archive: Path) -> str:
    if question.course_dir is None:
        return question.source_package_path

    relative_path = build_source_package_sidecar_path(question)
    target_path = question.course_dir / relative_path
    legacy_sidecar_path = question.course_dir / Path(H5P_SIDECAR_DIRNAME) / question.identifier
    legacy_archive_path = question.course_dir / build_legacy_source_archive_sidecar_path(question)

    if target_path.exists():
        if target_path.is_dir():
            shutil.rmtree(target_path)
        else:
            target_path.unlink()

    with ZipFile(source_archive) as archive:
        metadata_payload = json.loads(archive.read("h5p.json").decode("utf-8"))
        content_payload = json.loads(archive.read("content/content.json").decode("utf-8"))

    populate_imported_h5p_directory(source_archive, target_path, metadata_payload, content_payload)

    if legacy_sidecar_path.exists() and legacy_sidecar_path.is_dir():
        shutil.rmtree(legacy_sidecar_path)
    if legacy_archive_path.exists() and legacy_archive_path.is_file():
        legacy_archive_path.unlink()
    return relative_path


def remove_legacy_h5p_json_sidecars(course_dir: Path) -> None:
    sidecar_root = course_dir / H5P_SIDECAR_DIRNAME
    if not sidecar_root.exists():
        return

    for archive_path in sidecar_root.glob("**/source.h5p"):
        sidecar_dir = archive_path.parent
        if (sidecar_dir / "h5p.json").exists() and (
            (sidecar_dir / "content" / "content.json").exists()
            or (sidecar_dir / "content.yml").exists()
            or (sidecar_dir / "content.json").exists()
        ):
            archive_path.unlink()


def load_h5p_sidecar_file(course_dir: Path, relative_path: str, *, description: str) -> dict[str, object]:
    path = (course_dir / relative_path).resolve()
    if not path.exists() or not path.is_file():
        raise ValueError(f"{description} '{relative_path}' wurde nicht gefunden.")

    if path.suffix in {".yml", ".yaml"}:
        payload = read_yaml(path)
    else:
        payload = json.loads(path.read_text(encoding="utf-8"))

    if not isinstance(payload, dict):
        raise ValueError(f"{description} '{relative_path}' muss ein Objekt sein.")
    return payload


def clone_json_value(value: object) -> object:
    return json.loads(json.dumps(value, ensure_ascii=False))


def load_h5p_payload_from_path(source_path: Path) -> tuple[dict[str, object], dict[str, object]] | None:
    try:
        if source_path.is_dir():
            metadata_payload = json.loads((source_path / "h5p.json").read_text(encoding="utf-8"))
            content_payload = read_h5p_content_payload(source_path)
        else:
            with ZipFile(source_path) as archive:
                metadata_payload = json.loads(archive.read("h5p.json").decode("utf-8"))
                content_payload = json.loads(archive.read("content/content.json").decode("utf-8"))
    except (BadZipFile, KeyError, OSError, json.JSONDecodeError):
        return None

    if not isinstance(metadata_payload, dict) or not isinstance(content_payload, dict):
        return None
    return metadata_payload, content_payload


def source_tree_mtime_ns(path: Path | None) -> int:
    if path is None or not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_mtime_ns

    latest_mtime = path.stat().st_mtime_ns
    for child in path.rglob("*"):
        latest_mtime = max(latest_mtime, child.stat().st_mtime_ns)
    return latest_mtime


def populate_imported_h5p_directory(source_path: Path, target_dir: Path, metadata_payload: dict[str, object], content_payload: dict[str, object]) -> None:
    if target_dir.exists():
        shutil.rmtree(target_dir)
    ensure_directory(target_dir)

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
            destination_relative = normalize_h5p_source_asset_path(relative_path, content_root_only=content_root_only)
            if destination_relative is None:
                continue
            destination = target_dir / destination_relative
            ensure_directory(destination.parent)
            shutil.copyfile(source_file, destination)
    else:
        target_root = target_dir.resolve()
        with ZipFile(source_path) as archive:
            for member in archive.infolist():
                if member.is_dir():
                    continue
                destination_relative = normalize_h5p_source_asset_path(member.filename, content_root_only=True)
                if destination_relative is None:
                    continue
                destination = (target_dir / destination_relative).resolve()
                if not str(destination).startswith(str(target_root)):
                    continue
                ensure_directory(destination.parent)
                destination.write_bytes(archive.read(member.filename))

    write_json(target_dir / "h5p.json", metadata_payload)
    write_h5p_content_files(target_dir, content_payload)


def build_imported_question_from_sidecar(course_dir: Path, identifier: str, source_package_path: str) -> PythonQuestionBlock | None:
    payload = load_h5p_payload_from_path(course_dir / source_package_path)
    if payload is None:
        return None

    metadata_payload, content_payload = payload
    question = build_imported_question_from_h5p_package(
        course_dir.name,
        MoodleH5PActivity(
            identifier=identifier,
            title=str(metadata_payload.get("title") or identifier),
            course_id=0,
            activity_id=0,
            instance_id=None,
        ),
        metadata_payload,
        content_payload,
    )
    if question is None:
        return None
    question.course_dir = course_dir
    question.source_package_path = source_package_path
    return question


def unescape_display_value(value: object) -> object:
    if isinstance(value, dict):
        return {key: unescape_display_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [unescape_display_value(item) for item in value]
    if isinstance(value, str):
        return html.unescape(value)
    return value


def escape_h5p_value(value: object) -> object:
    if isinstance(value, dict):
        return {key: escape_h5p_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [escape_h5p_value(item) for item in value]
    if isinstance(value, str):
        return html.escape(value, quote=True)
    return value


def render_template_literal(value: str, *, indent: int = 0) -> str:
    escaped = value.replace("\\", "\\\\").replace("`", "\\`").replace("${", "\\${")
    if "\n" in escaped:
        indented_lines = "\n".join(((" " * indent) + line) if line else "" for line in escaped.split("\n"))
        separator = "" if escaped.endswith("\n") else "\n"
        return "`\n" + indented_lines + separator + (" " * indent) + "`"
    return f"`{escaped}`"


def render_jsx_value(value: object, *, indent: int = 0) -> str:
    if isinstance(value, dict):
        if not value:
            return "{}"
        child_indent = indent + 2
        lines = ["{"]
        items = list(value.items())
        for index, (key, item) in enumerate(items):
            suffix = "," if index < len(items) - 1 else ""
            rendered_item = render_jsx_value(item, indent=child_indent)
            if "\n" in rendered_item:
                rendered_lines = rendered_item.splitlines()
                lines.append(" " * child_indent + json.dumps(key, ensure_ascii=False) + ": " + rendered_lines[0])
                for rendered_line in rendered_lines[1:]:
                    lines.append(rendered_line)
                lines[-1] += suffix
            else:
                lines.append(" " * child_indent + json.dumps(key, ensure_ascii=False) + ": " + rendered_item + suffix)
        lines.append(" " * indent + "}")
        return "\n".join(lines)

    if isinstance(value, list):
        if not value:
            return "[]"
        child_indent = indent + 2
        lines = ["["]
        for index, item in enumerate(value):
            suffix = "," if index < len(value) - 1 else ""
            rendered_item = render_jsx_value(item, indent=child_indent)
            rendered_lines = rendered_item.splitlines()
            lines.append(" " * child_indent + rendered_lines[0])
            for rendered_line in rendered_lines[1:]:
                lines.append(rendered_line)
            lines[-1] += suffix
        lines.append(" " * indent + "]")
        return "\n".join(lines)

    if isinstance(value, str):
        display_value = html.unescape(value)
        if "\n" in display_value or '"' in display_value:
            return render_template_literal(display_value, indent=indent)
        return json.dumps(display_value, ensure_ascii=False)

    return json.dumps(value, ensure_ascii=False)


def render_tag_attribute(name: str, value: str) -> str:
    if "\n" in value or '"' in value:
        return f"  {name}={{{render_template_literal(value, indent=2)}}}"
    return f'  {name}="{escape_mdx_attribute(value)}"'


def normalize_template_literal(content: str) -> str:
    if content.startswith("\n"):
        content = content[1:]
    content = textwrap.dedent(content)
    content = content.replace("\\`", "`").replace("\\${", "${")
    content = content.replace("\\n", "\n").replace("\\r", "\r").replace("\\t", "\t")
    content = content.replace("\\\\", "\\")
    return content


def jsx_expression_to_json(expression: str) -> str:
    result: list[str] = []
    index = 0
    in_string = False
    in_template = False
    escaped = False
    template_buffer: list[str] = []
    while index < len(expression):
        char = expression[index]
        if in_template:
            if escaped:
                template_buffer.append("\\" + char)
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == "`":
                result.append(json.dumps(normalize_template_literal("".join(template_buffer)), ensure_ascii=False))
                template_buffer = []
                in_template = False
            else:
                template_buffer.append(char)
            index += 1
            continue
        if in_string:
            result.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue
        if char == '"':
            in_string = True
            result.append(char)
            index += 1
            continue
        if char == "`":
            in_template = True
            template_buffer = []
            index += 1
            continue
        result.append(char)
        index += 1
    if in_template:
        raise ValueError("Unvollstaendiger Template-String im PythonQuestion-Tag.")
    return "".join(result)


def parse_jsx_expression(expression: str) -> object:
    return json.loads(jsx_expression_to_json(expression))


def diff_json_values(actual: object, default: object) -> object | None:
    if isinstance(actual, dict) and isinstance(default, dict):
        diff: dict[str, object] = {}
        for key, value in actual.items():
            nested = diff_json_values(value, default.get(key)) if key in default else clone_json_value(value)
            if nested is not None:
                diff[key] = nested
        return diff or None

    if isinstance(actual, list) and isinstance(default, list):
        return None if actual == default else clone_json_value(actual)

    return None if actual == default else clone_json_value(actual)


def merge_json_values(default: object, override: object) -> object:
    if isinstance(default, dict) and isinstance(override, dict):
        merged = {key: clone_json_value(value) for key, value in default.items()}
        for key, value in override.items():
            if key in merged:
                merged[key] = merge_json_values(merged[key], value)
            else:
                merged[key] = clone_json_value(value)
        return merged

    if isinstance(override, (dict, list)):
        return clone_json_value(override)
    return override


def build_default_imported_h5p_metadata(question: PythonQuestionBlock) -> dict[str, object]:
    try:
        metadata = build_h5p_metadata(question)
    except (ValueError, FileNotFoundError):
        return {}
    metadata.pop("title", None)
    metadata.pop("mainLibrary", None)
    return metadata


def load_python_question_semantics() -> list[dict[str, object]]:
    payload = read_json(find_library_dir(PYTHON_QUESTION_MACHINE_NAME) / "semantics.json")
    if not isinstance(payload, list):
        raise ValueError("semantics.json fuer H5P.PythonQuestion muss ein JSON-Array sein.")
    return [field for field in payload if isinstance(field, dict)]


def default_from_semantics_field(field: dict[str, object]) -> object:
    field_type = str(field.get("type") or "")
    if field_type == "group":
        defaults: dict[str, object] = {}
        for child in field.get("fields", []) or []:
            if not isinstance(child, dict):
                continue
            child_name = str(child.get("name") or "").strip()
            if not child_name:
                continue
            defaults[child_name] = default_from_semantics_field(child)
        return defaults
    if field_type == "list":
        if "default" in field:
            return clone_json_value(field["default"])
        return []
    if "default" in field:
        return clone_json_value(field["default"])
    if field_type in {"text", "code", "select"}:
        return ""
    if field_type == "boolean":
        return False
    if field_type == "number":
        return 0
    return None


def default_object_from_semantics(fields: list[dict[str, object]]) -> dict[str, object]:
    defaults: dict[str, object] = {}
    for field in fields:
        name = str(field.get("name") or "").strip()
        if not name:
            continue
        defaults[name] = default_from_semantics_field(field)
    return defaults


def compact_by_semantics(value: object, field: dict[str, object]) -> object | None:
    field_type = str(field.get("type") or "")

    if field_type == "group":
        if not isinstance(value, dict):
            return clone_json_value(value)
        children = {
            str(child.get("name") or "").strip(): child
            for child in field.get("fields", []) or []
            if isinstance(child, dict) and str(child.get("name") or "").strip()
        }
        compacted: dict[str, object] = {}
        for key, child_value in value.items():
            child_field = children.get(key)
            if child_field is None:
                compacted[key] = clone_json_value(child_value)
                continue
            child_compacted = compact_by_semantics(child_value, child_field)
            if child_compacted is not None:
                compacted[key] = child_compacted
        return compacted or None

    if field_type == "list":
        if not isinstance(value, list):
            return clone_json_value(value)
        item_field = field.get("field")
        compacted_items: list[object] = []
        for item in value:
            if isinstance(item_field, dict):
                compacted_item = compact_by_semantics(item, item_field)
            else:
                compacted_item = clone_json_value(item)
            if compacted_item in (None, {}, []):
                continue
            compacted_items.append(compacted_item)
        return compacted_items or None

    default_value = default_from_semantics_field(field)
    if value == default_value:
        return None
    if value in (None, "", [], {}) and default_value in (None, "", [], {}):
        return None
    return clone_json_value(value)


def build_default_python_question_content(question: PythonQuestionBlock) -> dict[str, object]:
    defaults = default_object_from_semantics(load_python_question_semantics())

    defaults["pythonRunner"] = question.runner
    defaults.setdefault("advancedOptions", {})
    if isinstance(defaults["advancedOptions"], dict):
        defaults["advancedOptions"]["showConsole"] = question.show_console

    defaults.setdefault("pyodideOptions", {})
    if isinstance(defaults["pyodideOptions"], dict):
        defaults["pyodideOptions"]["packages"] = [{"package": package_name} for package_name in question.packages]

    defaults.setdefault("editorSettings", {})
    if isinstance(defaults["editorSettings"], dict):
        defaults["editorSettings"]["instructions"] = question.instructions
        defaults.setdefault("editorSettings", {}).setdefault("options", {})
        options = defaults["editorSettings"].get("options")
        if isinstance(options, dict):
            options["allowAddingFiles"] = question.allow_adding_files

    defaults.setdefault("gradingSettings", {})
    if isinstance(defaults["gradingSettings"], dict):
        defaults["gradingSettings"]["gradingMethod"] = question.grading_method

    return defaults


def compact_python_question_content(question: PythonQuestionBlock) -> dict[str, object]:
    if question.h5p_content is None or not isinstance(question.h5p_content, dict):
        return {}

    content = clone_json_value(question.h5p_content)
    if not isinstance(content, dict):
        return {}

    defaults = build_default_python_question_content(question)
    semantic_fields = {
        str(field.get("name") or "").strip(): field
        for field in load_python_question_semantics()
        if str(field.get("name") or "").strip()
    }

    compacted: dict[str, object] = {}
    for key, value in content.items():
        if key == "pythonRunner" and value == question.runner:
            continue
        if key == "editorSettings" and isinstance(value, dict):
            value = clone_json_value(value)
            if isinstance(value, dict) and normalize_whitespace(html.unescape(str(value.get("instructions") or ""))) == question.instructions:
                value.pop("instructions", None)
        if key == "advancedOptions" and isinstance(value, dict):
            value = clone_json_value(value)
            if isinstance(value, dict) and value.get("showConsole") == question.show_console:
                value.pop("showConsole", None)
        if key == "pyodideOptions" and isinstance(value, dict):
            value = clone_json_value(value)
            if isinstance(value, dict) and value.get("packages") == defaults.get("pyodideOptions", {}).get("packages"):
                value.pop("packages", None)
        if key == "gradingSettings" and isinstance(value, dict):
            value = clone_json_value(value)
            if isinstance(value, dict) and value.get("gradingMethod") == question.grading_method:
                value.pop("gradingMethod", None)

        field = semantic_fields.get(key)
        if field is None:
            compacted[key] = clone_json_value(value)
            continue
        compacted_value = compact_by_semantics(value, field)
        if compacted_value is not None:
            compacted[key] = compacted_value
    return compacted


def build_default_imported_h5p_content(question: PythonQuestionBlock) -> dict[str, object]:
    if question.main_library != PYTHON_QUESTION_MACHINE_NAME:
        return {}
    return build_default_python_question_content(question)


def build_editable_h5p_payload(question: PythonQuestionBlock) -> dict[str, object]:
    if question.h5p_metadata is None or question.h5p_content is None:
        return {}

    if question.main_library == PYTHON_QUESTION_MACHINE_NAME:
        return compact_python_question_content(question)

    source_payload = load_h5p_payload_from_source_package(question)
    if source_payload is not None:
        source_metadata, source_content = source_payload
        metadata = clone_json_value(question.h5p_metadata)
        content = clone_json_value(question.h5p_content)
        if not isinstance(metadata, dict) or not isinstance(content, dict):
            return {}

        metadata.pop("title", None)
        metadata.pop("mainLibrary", None)

        source_metadata_copy = clone_json_value(source_metadata)
        if isinstance(source_metadata_copy, dict):
            source_metadata_copy.pop("title", None)
            source_metadata_copy.pop("mainLibrary", None)

        metadata_diff = diff_json_values(metadata, source_metadata_copy)
        content_diff = diff_json_values(content, source_content)

        payload: dict[str, object] = {}
        if isinstance(metadata_diff, dict) and metadata_diff:
            payload["metadata"] = metadata_diff
        if isinstance(content_diff, dict) and content_diff:
            payload["content"] = content_diff
        elif isinstance(content_diff, list):
            payload["content"] = content_diff
        return payload

    metadata = clone_json_value(question.h5p_metadata)
    content = clone_json_value(question.h5p_content)
    if not isinstance(metadata, dict) or not isinstance(content, dict):
        return {}

    metadata.pop("title", None)
    metadata.pop("mainLibrary", None)

    metadata_diff = diff_json_values(metadata, build_default_imported_h5p_metadata(question))
    content_diff = diff_json_values(content, build_default_imported_h5p_content(question))

    payload: dict[str, object] = {}
    if isinstance(metadata_diff, dict) and metadata_diff:
        payload["metadata"] = metadata_diff
    if isinstance(content_diff, dict) and content_diff:
        payload["content"] = content_diff
    elif isinstance(content_diff, list):
        payload["content"] = content_diff
    return payload


def apply_editable_h5p_payload(question: PythonQuestionBlock, payload: dict[str, object]) -> None:
    payload = escape_h5p_value(payload)
    if question.main_library == PYTHON_QUESTION_MACHINE_NAME and "metadata" not in payload and "content" not in payload:
        metadata = build_h5p_metadata(question)
        metadata["title"] = question.title
        metadata["mainLibrary"] = question.main_library
        content = merge_json_values(build_default_python_question_content(question), payload)
        if not isinstance(content, dict):
            raise ValueError("Der H5P-Block konnte nicht in ein gueltiges H5P-Objekt umgewandelt werden.")
        question.h5p_metadata = metadata
        question.h5p_content = content
        return

    source_payload = load_h5p_payload_from_source_package(question)
    if source_payload is not None:
        source_metadata, source_content = source_payload
        metadata_override = payload.get("metadata", {})
        content_override = payload.get("content", {})
        if metadata_override is None:
            metadata_override = {}
        if content_override is None:
            content_override = {}
        if not isinstance(metadata_override, dict):
            raise ValueError("Der H5P-Block erwartet fuer 'metadata' ein JSON-Objekt.")
        if not isinstance(content_override, dict):
            raise ValueError("Der H5P-Block erwartet fuer 'content' ein JSON-Objekt.")

        metadata_base = clone_json_value(source_metadata)
        content_base = clone_json_value(source_content)
        if not isinstance(metadata_base, dict) or not isinstance(content_base, dict):
            raise ValueError("Das source.h5p enthaelt keine gueltigen H5P-Daten.")

        metadata_base.pop("title", None)
        metadata_base.pop("mainLibrary", None)
        metadata = merge_json_values(metadata_base, metadata_override)
        content = merge_json_values(content_base, content_override)
        if not isinstance(metadata, dict) or not isinstance(content, dict):
            raise ValueError("Der H5P-Block konnte nicht in ein gueltiges H5P-Objekt umgewandelt werden.")

        metadata["title"] = question.title
        metadata["mainLibrary"] = question.main_library
        question.h5p_metadata = metadata
        question.h5p_content = content
        return

    metadata_override = payload.get("metadata", {})
    content_override = payload.get("content", {})
    if metadata_override is None:
        metadata_override = {}
    if content_override is None:
        content_override = {}
    if not isinstance(metadata_override, dict):
        raise ValueError("Der H5P-Block erwartet fuer 'metadata' ein JSON-Objekt.")
    if not isinstance(content_override, dict):
        raise ValueError("Der H5P-Block erwartet fuer 'content' ein JSON-Objekt.")

    metadata = merge_json_values(build_default_imported_h5p_metadata(question), metadata_override)
    content = merge_json_values(build_default_imported_h5p_content(question), content_override)
    if not isinstance(metadata, dict) or not isinstance(content, dict):
        raise ValueError("Der H5P-Block konnte nicht in ein gueltiges H5P-Objekt umgewandelt werden.")

    metadata["title"] = question.title
    metadata["mainLibrary"] = question.main_library
    if question.main_library == PYTHON_QUESTION_MACHINE_NAME:
        content.setdefault("pythonRunner", question.runner)

    question.h5p_metadata = metadata
    question.h5p_content = content


def infer_source_package_sidecar_path(question: PythonQuestionBlock) -> str:
    if question.course_dir is None:
        return ""
    relative_path = build_source_package_sidecar_path(question)
    if (question.course_dir / relative_path).exists():
        return relative_path
    legacy_relative_path = build_legacy_source_archive_sidecar_path(question)
    if (question.course_dir / legacy_relative_path).exists():
        return legacy_relative_path
    return ""


def load_h5p_payload_from_source_package(question: PythonQuestionBlock) -> tuple[dict[str, object], dict[str, object]] | None:
    if question.course_dir is None or not question.source_package_path:
        return None

    archive_path = question.course_dir / question.source_package_path
    if not archive_path.exists():
        return None
    return load_h5p_payload_from_path(archive_path)


def summarize_questionset(content_payload: dict[str, object]) -> str:
    questions = content_payload.get("questions", [])
    if not isinstance(questions, list) or not questions:
        return "Importiertes Quiz aus Moodle."

    prompts: list[str] = []
    for entry in questions[:5]:
        if not isinstance(entry, dict):
            continue
        params = entry.get("params", {})
        if not isinstance(params, dict):
            continue
        prompt = compact_text(strip_html(str(params.get("question") or "")))
        if prompt:
            prompts.append(prompt)

    if not prompts:
        return f"Importiertes Quiz aus Moodle mit {len(questions)} Teilfragen."
    prompt_summary = " | ".join(prompts)
    return f"Importiertes Quiz aus Moodle mit {len(questions)} Teilfragen: {prompt_summary}"


def build_scaffold_question(course_slug: str, activity: MoodleH5PActivity) -> PythonQuestionBlock:
    return PythonQuestionBlock(
        identifier=activity.identifier,
        title=activity.title,
        instructions=activity.intro or f"Importiert aus Moodle: {activity.title}",
        preview_url=activity.url,
        package_url=getattr(activity, "package_url", ""),
        runner="pyodide",
        course_slug=course_slug,
        course_dir=COURSES_DIR / course_slug,
    )


def build_imported_question_from_h5p_package(
    course_slug: str,
    activity: MoodleH5PActivity,
    metadata_payload: dict[str, object],
    content_payload: dict[str, object],
) -> PythonQuestionBlock | None:
    main_library = str(metadata_payload.get("mainLibrary") or "").strip()
    content_type = str(content_payload.get("contentType") or "").strip()
    if not main_library:
        return None

    metadata_copy = json.loads(json.dumps(metadata_payload, ensure_ascii=False))
    content_copy = json.loads(json.dumps(content_payload, ensure_ascii=False))

    if main_library == "H5P.QuestionSet":
        return PythonQuestionBlock(
            identifier=activity.identifier,
            title=str(metadata_payload.get("title") or activity.title),
            instructions=summarize_questionset(content_payload),
            preview_url=activity.url,
            main_library=main_library,
            package_url=getattr(activity, "package_url", ""),
            h5p_metadata=metadata_copy,
            h5p_content=content_copy,
            runner="pyodide",
            course_slug=course_slug,
            course_dir=COURSES_DIR / course_slug,
        )

    if main_library != PYTHON_QUESTION_MACHINE_NAME:
        return PythonQuestionBlock(
            identifier=activity.identifier,
            title=str(metadata_payload.get("title") or activity.title),
            instructions=activity.intro or f"Importiert aus Moodle: {activity.title}",
            preview_url=activity.url,
            main_library=main_library,
            package_url=getattr(activity, "package_url", ""),
            raw_package=True,
            h5p_metadata=metadata_copy,
            h5p_content=content_copy,
            runner="pyodide",
            course_slug=course_slug,
            course_dir=COURSES_DIR / course_slug,
        )

    if content_type and content_type != "ide_only":
        return PythonQuestionBlock(
            identifier=activity.identifier,
            title=str(metadata_payload.get("title") or activity.title),
            instructions=summarize_h5p_instructions(activity, content_payload),
            preview_url=activity.url,
            main_library=main_library,
            package_url=getattr(activity, "package_url", ""),
            h5p_metadata=metadata_copy,
            h5p_content=content_copy,
            runner=str(content_payload.get("pythonRunner") or "pyodide").strip() or "pyodide",
            course_slug=course_slug,
            course_dir=COURSES_DIR / course_slug,
        )

    editor_settings = content_payload.get("editorSettings", {})
    grading_settings = content_payload.get("gradingSettings", {})
    advanced_options = content_payload.get("advancedOptions", {})
    if not isinstance(editor_settings, dict) or not isinstance(grading_settings, dict):
        return None
    if not isinstance(advanced_options, dict):
        advanced_options = {}

    editor_options = editor_settings.get("options", {})
    if not isinstance(editor_options, dict):
        editor_options = {}

    test_cases: list[TestCase] = []
    for raw_test_case in grading_settings.get("testCases", []) or []:
        if not isinstance(raw_test_case, dict):
            continue
        test_cases.append(
            TestCase(
                hidden=bool(raw_test_case.get("hidden", False)),
                inputs=extract_test_case_values(raw_test_case.get("inputs", []), field_name="input"),
                outputs=extract_test_case_values(raw_test_case.get("outputs", []), field_name="output"),
            )
        )

    return PythonQuestionBlock(
        identifier=activity.identifier,
        title=str(metadata_payload.get("title") or activity.title),
        instructions=extract_h5p_editor_instructions(content_payload) or summarize_h5p_instructions(activity, content_payload),
        preview_url=activity.url,
        main_library=main_library,
        package_url=getattr(activity, "package_url", ""),
        h5p_metadata=metadata_copy,
        h5p_content=content_copy,
        runner=str(content_payload.get("pythonRunner") or "pyodide").strip() or "pyodide",
        packages=extract_h5p_packages(content_payload),
        starter_code=normalize_whitespace(html.unescape(str(editor_settings.get("startingCode") or ""))),
        solution_code=normalize_whitespace(html.unescape(str(grading_settings.get("targetCode") or ""))),
        pre_code=normalize_whitespace(html.unescape(str(editor_settings.get("preCode") or ""))),
        post_code=normalize_whitespace(html.unescape(str(editor_settings.get("postCode") or ""))),
        grading_method=str(grading_settings.get("gradingMethod") or "please_choose"),
        show_console=bool(advanced_options.get("showConsole", True)),
        allow_adding_files=bool(editor_options.get("allowAddingFiles", False)),
        source_files=extract_source_files(editor_options),
        test_cases=test_cases,
        course_slug=course_slug,
        course_dir=COURSES_DIR / course_slug,
    )


def render_imported_question_mdx(question: PythonQuestionBlock) -> list[str]:
    if question.source_package_path:
        return [
            "<PythonQuestion",
            render_tag_attribute("identifier", question.identifier),
            "/>",
            "",
        ]

    if question.h5p_metadata is not None and question.h5p_content is not None:
        payload_lines = render_jsx_value(unescape_display_value(build_editable_h5p_payload(question)), indent=2).splitlines()
    else:
        payload_lines = []

    lines = [
        "<PythonQuestion",
        render_tag_attribute("identifier", question.identifier),
        render_tag_attribute("title", question.title),
        render_tag_attribute("instructions", question.instructions),
    ]
    if question.main_library != PYTHON_QUESTION_MACHINE_NAME:
        lines.append(render_tag_attribute("h5pLibrary", question.main_library))
    if question.raw_package:
        lines.append('  rawPackage="true"')
    lines.append(render_tag_attribute("runner", question.runner))
    if question.packages:
        lines.append(render_tag_attribute("packages", ", ".join(question.packages)))
    if question.grading_method != "please_choose":
        lines.append(render_tag_attribute("gradingMethod", question.grading_method))
    if not question.show_console:
        lines.append('  showConsole="false"')
    if question.allow_adding_files:
        lines.append('  allowAddingFiles="true"')
    if payload_lines:
        if len(payload_lines) == 1:
            lines.append("  h5p={" + payload_lines[0] + "}")
        else:
            lines.append("  h5p={" + payload_lines[0])
            for line in payload_lines[1:-1]:
                lines.append(f"  {line}")
            lines.append("  }}")
    lines.extend(["/>", ""])

    if payload_lines:
        return lines

    for role, body in [
        ("pre", question.pre_code),
        ("starter", question.starter_code),
        ("solution", question.solution_code),
        ("post", question.post_code),
    ]:
        if not body:
            continue
        lines.extend([
            f"```python question:{question.identifier} {role}",
            body,
            "```",
            "",
        ])

    for source_file in question.source_files:
        if not source_file.code:
            continue
        file_tokens = [f"file:{source_file.file_name}"]
        if not source_file.visible_to_learner:
            file_tokens.append("hidden-file")
        if not source_file.learner_editable:
            file_tokens.append("readonly-file")
        language = "python" if source_file.file_name.endswith(".py") else "text"
        lines.extend([
            f"```{language} question:{question.identifier} {' '.join(file_tokens)}",
            source_file.code,
            "```",
            "",
        ])

    for test_case in question.test_cases:
        test_case_payload = {
            "hidden": test_case.hidden,
            "inputs": test_case.inputs,
            "outputs": test_case.outputs,
        }
        lines.extend([
            f"```json question:{question.identifier} testcase",
            json.dumps(test_case_payload, ensure_ascii=False, indent=2),
            "```",
            "",
        ])

    return lines


def escape_mdx_attribute(value: str) -> str:
    return html.escape(value, quote=True)


def parse_bool(value: str, default: bool = False) -> bool:
    if value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def parse_braced_attribute(raw: str, start_index: int) -> tuple[str, int]:
    depth = 0
    in_string = False
    in_template = False
    escaped = False
    for index in range(start_index, len(raw)):
        char = raw[index]
        if in_template:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == "`":
                in_template = False
            continue
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == "`":
            in_template = True
            continue
        if char == '"':
            in_string = True
            continue
        if char == "{":
            depth += 1
            continue
        if char == "}":
            depth -= 1
            if depth == 0:
                return raw[start_index:index + 1], index + 1
    raise ValueError("Unvollstaendiger JSX-Ausdruck im PythonQuestion-Tag.")


def parse_tag_attributes(raw_attrs: str) -> dict[str, object]:
    attrs: dict[str, object] = {}
    index = 0
    while index < len(raw_attrs):
        while index < len(raw_attrs) and raw_attrs[index].isspace():
            index += 1
        if index >= len(raw_attrs):
            break

        key_match = re.match(r"([A-Za-z_:][A-Za-z0-9_:-]*)", raw_attrs[index:])
        if key_match is None:
            index += 1
            continue
        key = key_match.group(1)
        index += len(key)
        while index < len(raw_attrs) and raw_attrs[index].isspace():
            index += 1
        if index >= len(raw_attrs) or raw_attrs[index] != "=":
            attrs[key] = ""
            continue
        index += 1
        while index < len(raw_attrs) and raw_attrs[index].isspace():
            index += 1
        if index >= len(raw_attrs):
            break

        if raw_attrs[index] == '"':
            index += 1
            value_start = index
            while index < len(raw_attrs):
                if raw_attrs[index] == '"' and raw_attrs[index - 1] != "\\":
                    break
                index += 1
            attrs[key] = html.unescape(raw_attrs[value_start:index].strip())
            index += 1
            continue

        if raw_attrs[index] == "{":
            expression, index = parse_braced_attribute(raw_attrs, index)
            attrs[key] = parse_jsx_expression(expression[1:-1].strip())
            continue

        value_start = index
        while index < len(raw_attrs) and not raw_attrs[index].isspace():
            index += 1
        attrs[key] = raw_attrs[value_start:index]
    return attrs


def build_question_from_attrs(course_dir: Path, attrs: dict[str, object]) -> PythonQuestionBlock:
    course_slug = course_dir.name
    identifier = str(attrs.get("identifier", "")).strip()
    if not identifier:
        raise ValueError("PythonQuestion benötigt ein identifier-Attribut.")

    title = str(attrs.get("title", identifier))
    instructions = str(attrs.get("instructions", ""))
    preview_url = str(attrs.get("previewUrl", attrs.get("preview-url", "")))
    main_library = str(attrs.get("h5pLibrary", attrs.get("h5p-library", PYTHON_QUESTION_MACHINE_NAME))).strip() or PYTHON_QUESTION_MACHINE_NAME
    package_url = str(attrs.get("packageUrl", attrs.get("package-url", ""))).strip()
    raw_package = parse_bool(str(attrs.get("rawPackage", attrs.get("raw-package", "false"))), default=False)
    h5p_metadata_path = str(attrs.get("h5pMetadataPath", attrs.get("h5p-metadata-path", ""))).strip()
    h5p_content_path = str(attrs.get("h5pContentPath", attrs.get("h5p-content-path", ""))).strip()
    source_package_path = str(attrs.get("sourcePackagePath", attrs.get("source-package-path", ""))).strip()
    runner = str(attrs.get("runner", "pyodide")).strip() or "pyodide"
    grading_method = str(attrs.get("gradingMethod", attrs.get("grading-method", "please_choose")))
    packages = split_csv(str(attrs.get("packages", "")))
    show_console = parse_bool(str(attrs.get("showConsole", "true")), default=True)
    allow_adding_files = parse_bool(str(attrs.get("allowAddingFiles", "false")), default=False)
    editable_h5p_payload = attrs.get("h5p")

    if not source_package_path:
        source_package_path = infer_source_package_sidecar_path(
            PythonQuestionBlock(
                identifier=identifier,
                title=identifier,
                instructions="",
                course_slug=course_slug,
                course_dir=course_dir,
            )
        )

    question = build_imported_question_from_sidecar(course_dir, identifier, source_package_path) if source_package_path else None

    if question is None:
        question = PythonQuestionBlock(
            identifier=identifier,
            title=title,
            instructions=instructions,
            preview_url=preview_url,
            main_library=main_library,
            package_url=package_url,
            raw_package=raw_package,
            h5p_metadata_path=h5p_metadata_path,
            h5p_content_path=h5p_content_path,
            source_package_path=source_package_path,
            runner=runner,
            packages=packages,
            grading_method=grading_method,
            show_console=show_console,
            allow_adding_files=allow_adding_files,
            course_slug=course_slug,
            course_dir=course_dir,
        )

    if "title" in attrs:
        question.title = title
    if "instructions" in attrs:
        question.instructions = instructions
    if "previewUrl" in attrs or "preview-url" in attrs:
        question.preview_url = preview_url
    if "h5pLibrary" in attrs or "h5p-library" in attrs:
        question.main_library = main_library
    if "packageUrl" in attrs or "package-url" in attrs:
        question.package_url = package_url
    if "rawPackage" in attrs or "raw-package" in attrs:
        question.raw_package = raw_package
    if source_package_path:
        question.source_package_path = source_package_path
    if "runner" in attrs:
        question.runner = runner
    if "packages" in attrs:
        question.packages = packages
    if "gradingMethod" in attrs or "grading-method" in attrs:
        question.grading_method = grading_method
    if "showConsole" in attrs:
        question.show_console = show_console
    if "allowAddingFiles" in attrs:
        question.allow_adding_files = allow_adding_files

    if h5p_metadata_path:
        question.h5p_metadata = load_h5p_sidecar_file(question.course_dir, h5p_metadata_path, description="H5P-Metadaten")
    if h5p_content_path:
        question.h5p_content = load_h5p_sidecar_file(question.course_dir, h5p_content_path, description="H5P-Content")
    if isinstance(editable_h5p_payload, dict):
        apply_editable_h5p_payload(question, editable_h5p_payload)
    return question


def parse_test_case(raw: str) -> TestCase:
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("Testfall muss ein JSON-Objekt sein.")

    inputs = payload.get("inputs", []) or []
    outputs = payload.get("outputs", []) or []

    return TestCase(
        hidden=bool(payload.get("hidden", False)),
        inputs=[str(item) for item in inputs],
        outputs=[str(item) for item in outputs],
    )


def parse_json_object(raw: str, *, description: str) -> dict[str, object]:
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError(f"{description} muss ein JSON-Objekt sein.")
    return payload


def parse_source_file(spec_parts: list[str], body: str) -> SourceFile:
    file_token = next((part for part in spec_parts if part.startswith("file:")), "")
    if not file_token:
        raise ValueError("Datei-Codeblock benötigt ein file:NAME.py-Token.")

    file_name = file_token.split(":", 1)[1].strip()
    if not file_name:
        raise ValueError("Datei-Codeblock benötigt einen Dateinamen.")

    visible = True
    editable = True
    for part in spec_parts:
        if part == "hidden-file":
            visible = False
        if part == "readonly-file":
            editable = False

    return SourceFile(
        file_name=file_name,
        code=normalize_whitespace(body),
        visible_to_learner=visible,
        learner_editable=editable,
    )


def parse_course(course_dir: Path) -> tuple[str, list[PythonQuestionBlock], str]:
    mdx_path = course_dir / "index.mdx"
    source = mdx_path.read_text(encoding="utf-8")
    course_slug = course_dir.name

    questions: dict[str, PythonQuestionBlock] = {}
    rendered_source = source

    for match in TAG_RE.finditer(source):
        attrs = parse_tag_attributes(match.group("attrs"))
        question = build_question_from_attrs(course_dir, attrs)
        question.course_dir = course_dir
        if question.identifier in questions:
            raise ValueError(
                f"PythonQuestion-Identifier '{question.identifier}' ist in {mdx_path} mehrfach vergeben."
            )
        questions[question.identifier] = question
        rendered_source = rendered_source.replace(match.group(0), PLACEHOLDER_TEMPLATE.format(identifier=question.identifier), 1)

    for fence in FENCE_RE.finditer(source):
        spec = fence.group("spec").strip()
        if not spec:
            continue

        spec_parts = spec.split()
        if len(spec_parts) < 3 or not spec_parts[1].startswith("question:"):
            continue

        identifier = spec_parts[1].split(":", 1)[1].strip()
        question = questions.get(identifier)
        if question is None:
            raise ValueError(f"Codeblock referenziert unbekannte PythonQuestion '{identifier}'.")

        role = spec_parts[2]
        body = normalize_whitespace(fence.group("body"))

        if role == "starter":
            question.starter_code = body
        elif role == "solution":
            question.solution_code = body
        elif role == "pre":
            question.pre_code = body
        elif role == "post":
            question.post_code = body
        elif role == "testcase":
            question.test_cases.append(parse_test_case(body))
        elif role == "h5p-metadata":
            question.h5p_metadata = parse_json_object(body, description="H5P-Metadaten")
        elif role == "h5p-content":
            question.h5p_content = parse_json_object(body, description="H5P-Content")
        elif role == "h5p":
            apply_editable_h5p_payload(question, parse_json_object(body, description="H5P-Daten"))
        elif role.startswith("file:"):
            question.source_files.append(parse_source_file(spec_parts[2:], fence.group("body")))
        else:
            raise ValueError(f"Unbekannte PythonQuestion-Rolle '{role}' in {mdx_path}.")

    def strip_question_fence(match: re.Match[str]) -> str:
        spec = match.group("spec").strip()
        spec_parts = spec.split()
        if len(spec_parts) >= 2 and spec_parts[1].startswith("question:"):
            return ""
        return match.group(0)

    rendered_source = FENCE_RE.sub(strip_question_fence, rendered_source)

    return source, list(questions.values()), rendered_source


def sync_metadata_path(course_dir: Path) -> Path:
    return course_dir / SYNC_METADATA_FILE


def load_sync_metadata(course_dir: Path) -> SyncMetadata | None:
    metadata_path = sync_metadata_path(course_dir)
    if not metadata_path.exists():
        return None
    payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Sync-Metadaten in {metadata_path} sind kein JSON-Objekt.")
    return SyncMetadata.from_dict(payload)


def save_sync_metadata(course_dir: Path, metadata: SyncMetadata) -> Path:
    ensure_directory(course_dir)
    metadata_path = sync_metadata_path(course_dir)
    metadata_path.write_text(json.dumps(metadata.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return metadata_path


def compute_question_hash(question: PythonQuestionBlock) -> str:
    payload = {
        "identifier": question.identifier,
        "title": question.title,
        "instructions": question.instructions,
        "mainLibrary": question.main_library,
        "rawPackage": question.raw_package,
        "h5pMetadata": question.h5p_metadata,
        "h5pContent": question.h5p_content,
        "runner": question.runner,
        "packages": question.packages,
        "starterCode": question.starter_code,
        "solutionCode": question.solution_code,
        "preCode": question.pre_code,
        "postCode": question.post_code,
        "gradingMethod": question.grading_method,
        "showConsole": question.show_console,
        "allowAddingFiles": question.allow_adding_files,
        "sourceFiles": [
            {
                "fileName": source_file.file_name,
                "code": source_file.code,
                "visibleToLearner": source_file.visible_to_learner,
                "learnerEditable": source_file.learner_editable,
            }
            for source_file in question.source_files
        ],
        "testCases": [
            {
                "hidden": test_case.hidden,
                "inputs": test_case.inputs,
                "outputs": test_case.outputs,
            }
            for test_case in question.test_cases
        ],
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def render_imported_course_mdx(course_slug: str, activities: list[MoodleH5PActivity]) -> str:
    lines = [f"# {course_slug}", ""]
    current_section = None
    for activity in activities:
        question = getattr(activity, "imported_question", None) or build_scaffold_question(course_slug, activity)
        if activity.section_title and activity.section_title != current_section:
            lines.extend([f"## {escape_mdx_attribute(activity.section_title)}", ""])
            current_section = activity.section_title

        lines.extend(render_imported_question_mdx(question))

    return "\n".join(line for line in lines if line != "") + "\n"


def import_moodle_course(course: str, remote_course_id: int, client: MoodleApiClient) -> Path:
    course_dir = COURSES_DIR / course
    ensure_directory(course_dir)
    ensure_directory(course_dir / "assets")
    activities = client.list_course_h5p_activities(remote_course_id)

    download_activity_question = getattr(client, "download_activity_question", None)
    if callable(download_activity_question):
        for activity in activities:
            try:
                activity.imported_question = download_activity_question(course, activity)
            except RuntimeError:
                activity.imported_question = None

    remove_legacy_h5p_json_sidecars(course_dir)
    mdx = render_imported_course_mdx(course, activities)
    (course_dir / "index.mdx").write_text(mdx, encoding="utf-8")

    source, questions, _ = parse_course(course_dir)
    _ = source
    question_by_identifier = {question.identifier: question for question in questions}
    metadata = SyncMetadata(course_slug=course, remote_course_id=remote_course_id, moodle_base_url=client.base_url)
    for activity in activities:
        question = question_by_identifier[activity.identifier]
        metadata.entries[activity.identifier] = SyncMetadataEntry(
            identifier=activity.identifier,
            remote_activity_id=activity.activity_id,
            remote_instance_id=activity.instance_id,
            remote_title=activity.title,
            remote_url=activity.url,
            remote_visible=activity.visible,
            local_hash=compute_question_hash(question),
            status="imported",
        )

    save_sync_metadata(course_dir, metadata)
    return course_dir


def build_course_status(course_dir: Path) -> dict[str, object]:
    metadata = load_sync_metadata(course_dir)
    if metadata is None:
        raise FileNotFoundError(f"Keine Sync-Metadaten in {sync_metadata_path(course_dir)} gefunden.")

    _, questions, _ = parse_course(course_dir)
    local_questions = {question.identifier: question for question in questions}
    items: list[dict[str, object]] = []
    counts = {"tracked": 0, "modified-local": 0, "local-only": 0, "remote-only": 0}

    for identifier, question in sorted(local_questions.items()):
        entry = metadata.entries.get(identifier)
        if entry is None:
            status = "local-only"
        elif compute_question_hash(question) != entry.local_hash:
            status = "modified-local"
        else:
            status = "tracked"
        counts[status] += 1
        items.append(
            {
                "identifier": identifier,
                "title": question.title,
                "status": status,
                "remoteActivityId": entry.remote_activity_id if entry else None,
            }
        )

    for identifier, entry in sorted(metadata.entries.items()):
        if identifier in local_questions:
            continue
        counts["remote-only"] += 1
        items.append(
            {
                "identifier": identifier,
                "title": entry.remote_title,
                "status": "remote-only",
                "remoteActivityId": entry.remote_activity_id,
            }
        )

    return {
        "course": course_dir.name,
        "remoteCourseId": metadata.remote_course_id,
        "moodleBaseUrl": metadata.moodle_base_url,
        "counts": counts,
        "items": items,
    }


def build_moodle_ping_report(client: MoodleApiClient) -> dict[str, object]:
    site_info = client.get_site_info()
    functions = site_info.get("functions", [])
    function_names: list[str] = []
    if isinstance(functions, list):
        for item in functions:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if name:
                function_names.append(name)

    return {
        "baseUrl": client.base_url,
        "siteName": str(site_info.get("sitename") or ""),
        "siteUrl": str(site_info.get("siteurl") or client.base_url),
        "userId": site_info.get("userid"),
        "userName": str(site_info.get("username") or ""),
        "fullName": str(site_info.get("fullname") or ""),
        "functions": sorted(function_names),
        "supportsCourseImport": "core_course_get_contents" in function_names,
    }


def resolve_moodle_client(base_url: str | None = None, token: str | None = None) -> MoodleApiClient:
    load_dotenv_file()
    resolved_base_url = (base_url or os.environ.get("MOODLE_BASE_URL", "")).strip()
    resolved_token = (token or os.environ.get("MOODLE_TOKEN", "")).strip()
    if not resolved_base_url:
        raise RuntimeError("MOODLE_BASE_URL ist nicht gesetzt.")
    if not resolved_token:
        raise RuntimeError("MOODLE_TOKEN ist nicht gesetzt.")
    return MoodleApiClient(resolved_base_url, resolved_token)


def ensure_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def read_yaml(path: Path) -> object:
    return CONTENT_STORE.read_yaml(path)


def read_json_or_default(path: Path, default: dict) -> dict:
    if not path.exists():
        return default

    content = path.read_text(encoding="utf-8")
    if not content.strip():
        return default

    return json.loads(content)


def write_json(path: Path, payload: dict) -> None:
    ensure_directory(path.parent)
    temp_path = path.with_name(f".{path.name}.tmp")
    temp_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temp_path, path)


def write_yaml(path: Path, payload: object) -> None:
    CONTENT_STORE.write_yaml(path, payload)


def read_h5p_content_payload(source_dir: Path) -> dict[str, object]:
    return CONTENT_STORE.read_h5p_content_payload(source_dir)


def write_h5p_content_files(target_dir: Path, payload: dict[str, object]) -> None:
    CONTENT_STORE.write_h5p_content_files(target_dir, payload)


def normalize_h5p_source_asset_path(relative_path: str, *, content_root_only: bool = False) -> str | None:
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


def write_h5p_archive_from_directory(
    archive: ZipFile,
    source_dir: Path,
    *,
    shared_libraries: Iterable[Path] = (),
    shared_libraries_root: Path | None = None,
) -> None:
    content_payload: dict[str, object] | None = None
    try:
        content_payload = read_h5p_content_payload(source_dir)
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

    library_root = shared_libraries_root or (COURSES_DIR.parent / "libraries")
    for library_dir in shared_libraries:
        for file_path in sorted(library_dir.rglob("*")):
            if file_path.is_file():
                archive.write(file_path, file_path.relative_to(library_root).as_posix())


def fetch_json(url: str) -> dict:
    request = Request(url, headers={"Accept": "application/json", "User-Agent": "course-sync"})
    with urlopen(request, timeout=30) as response:
        return json.load(response)


def download_file(url: str, destination: Path) -> None:
    request = Request(normalize_http_url(url), headers={"User-Agent": "course-sync"})
    with urlopen(request, timeout=60) as response, destination.open("wb") as target:
        shutil.copyfileobj(response, target)


def find_downloaded_asset(asset_prefix: str) -> Path | None:
    matches = sorted(H5P_RUNTIME_DOWNLOADS_DIR.glob(f"{asset_prefix}*.h5p"))
    if not matches:
        return None
    return matches[-1]


def release_metadata_cache_path() -> Path:
    return H5P_RUNTIME_DOWNLOADS_DIR / f"release-{H5P_LIBRARY_RELEASE_TAG}.json"


def load_release_assets() -> dict[str, str]:
    cache_path = release_metadata_cache_path()
    cached_release = read_json_or_default(cache_path, {})
    if cached_release:
        return {asset["name"]: asset["browser_download_url"] for asset in cached_release.get("assets", [])}

    try:
        release = fetch_json(f"https://api.github.com/repos/{H5P_LIBRARY_RELEASE_REPO}/releases/tags/{H5P_LIBRARY_RELEASE_TAG}")
    except HTTPError as error:
        if error.code == HTTPStatus.FORBIDDEN:
            raise RuntimeError(
                "GitHub API Rate-Limit erreicht und keine lokale Release-Metadatenkopie gefunden. "
                "Falls die Libraries schon einmal geladen wurden, reicht ein vorhandener Inhalt in .h5p-runtime/downloads/."
            ) from error
        raise

    write_json(cache_path, release)
    return {asset["name"]: asset["browser_download_url"] for asset in release.get("assets", [])}


def get_h5p_cli_command() -> list[str]:
    h5p_binary = shutil.which("h5p")
    if h5p_binary:
        return [h5p_binary]

    npx_binary = shutil.which("npx")
    if npx_binary:
        return [npx_binary, "--yes", "h5p-cli"]

    raise RuntimeError(
        "Für vollständige H5P-Pakete benötigt course_sync entweder 'h5p' oder 'npx' im PATH."
    )


def run_h5p_cli(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [*get_h5p_cli_command(), *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


def find_library_dir(machine_name: str, major_version: int | None = None, minor_version: int | None = None) -> Path:
    if major_version is not None and minor_version is not None:
        candidate = H5P_RUNTIME_LIBRARIES_DIR / f"{machine_name}-{major_version}.{minor_version}"
        if candidate.exists():
            return candidate

    matches = sorted(H5P_RUNTIME_LIBRARIES_DIR.glob(f"{machine_name}-*"))
    if not matches:
        raise FileNotFoundError(f"H5P-Library '{machine_name}' wurde in {H5P_RUNTIME_LIBRARIES_DIR} nicht gefunden.")

    if major_version is None or minor_version is None:
        return matches[-1]

    for candidate in matches:
        metadata = read_json(candidate / "library.json")
        if metadata.get("majorVersion") == major_version and metadata.get("minorVersion") == minor_version:
            return candidate

    # Imported packages may reference older patch/minor versions that are not present locally.
    # Fallback to the newest available version of the same machine name.
    return matches[-1]


def extract_library_asset(archive_path: Path, machine_name: str) -> Path:
    with ZipFile(archive_path) as archive:
        library_root = None
        for member in archive.namelist():
            normalized = member.strip("/")
            if normalized.endswith("/library.json"):
                library_root = normalized.rsplit("/", 1)[0]
                break

        if library_root is None:
            raise RuntimeError(f"Kein library.json in {archive_path.name} gefunden.")

        destination = H5P_RUNTIME_LIBRARIES_DIR / Path(library_root).name
        if destination.exists():
            shutil.rmtree(destination)

        extracted_root = None
        for member in archive.namelist():
            normalized = member.strip("/")
            if not normalized or not normalized.startswith(f"{library_root}/"):
                continue

            relative_path = Path(normalized).relative_to(library_root)
            if not relative_path.parts or relative_path.parts[0] == "content":
                continue

            target_path = destination / relative_path
            if normalized.endswith("/"):
                ensure_directory(target_path)
                continue

            ensure_directory(target_path.parent)
            with archive.open(member) as source, target_path.open("wb") as target:
                shutil.copyfileobj(source, target)
            extracted_root = destination

    if extracted_root is None:
        raise RuntimeError(f"Die Library '{machine_name}' konnte aus {archive_path.name} nicht extrahiert werden.")

    return extracted_root


def register_local_library(library_dir: Path) -> None:
    with WORKSPACE_LOCK:
        library_json = read_json(library_dir / "library.json")
        registry_path = H5P_RUNTIME_DIR / "libraryRegistry.json"
        registry = read_json_or_default(registry_path, {})
        machine_name = library_json["machineName"]
        existing_entry = registry.get(machine_name, {})
        short_name = CUSTOM_H5P_LIBRARY_SHORT_NAMES.get(machine_name) or existing_entry.get("shortName") or machine_name.lower().replace(".", "-")
        registry[machine_name] = {
            **existing_entry,
            "id": machine_name,
            "title": library_json.get("title", machine_name),
            "author": library_json.get("author", ""),
            "runnable": library_json.get("runnable", 0),
            "shortName": short_name,
        }
        write_json(registry_path, registry)


def ensure_custom_h5p_libraries() -> None:
    ensure_directory(H5P_RUNTIME_DOWNLOADS_DIR)
    ensure_directory(H5P_RUNTIME_LIBRARIES_DIR)

    missing_machine_names = [
        machine_name
        for machine_name in H5P_LIBRARY_ASSET_PREFIXES
        if not list(H5P_RUNTIME_LIBRARIES_DIR.glob(f"{machine_name}-*"))
    ]
    if not missing_machine_names:
        return

    assets: dict[str, str] | None = None

    for machine_name, asset_prefix in H5P_LIBRARY_ASSET_PREFIXES.items():
        if machine_name not in missing_machine_names:
            continue

        archive_path = find_downloaded_asset(asset_prefix)
        if archive_path is None:
            if assets is None:
                assets = load_release_assets()

            asset_name = next((name for name in assets if name.startswith(asset_prefix) and name.endswith(".h5p")), None)
            if asset_name is None:
                raise RuntimeError(f"Release-Asset für {machine_name} mit Präfix '{asset_prefix}' wurde nicht gefunden.")

            archive_path = H5P_RUNTIME_DOWNLOADS_DIR / asset_name
            if not archive_path.exists():
                download_file(assets[asset_name], archive_path)

        library_dir = extract_library_asset(archive_path, machine_name)
        register_local_library(library_dir)


def ensure_registered_local_libraries() -> None:
    for library_dir in sorted(H5P_RUNTIME_LIBRARIES_DIR.glob("*")):
        library_json = library_dir / "library.json"
        if library_json.exists():
            register_local_library(library_dir)


def ensure_h5p_editor_dependencies() -> None:
    if not list(H5P_RUNTIME_LIBRARIES_DIR.glob("H5PEditor.ShowWhen-*")):
        run_h5p_cli(["setup", "h5p-editor-show-when"], cwd=H5P_RUNTIME_DIR)
    if not list(H5P_RUNTIME_LIBRARIES_DIR.glob("H5PEditor.DateTime-*")):
        run_h5p_cli(["setup", "h5p-editor-datetime"], cwd=H5P_RUNTIME_DIR)


def ensure_h5p_math_display_registered() -> None:
    math_display_dirs = sorted(H5P_RUNTIME_LIBRARIES_DIR.glob("H5P.MathDisplay-*"))
    if not math_display_dirs:
        return
    register_local_library(math_display_dirs[-1])


def ensure_h5p_runtime_libraries() -> None:
    with WORKSPACE_LOCK:
        ensure_directory(H5P_RUNTIME_DIR)
        ensure_directory(H5P_RUNTIME_CONTENT_DIR)
        ensure_directory(H5P_RUNTIME_LIBRARIES_DIR)

        core_marker = H5P_RUNTIME_DIR / ".core-ready"
        if not core_marker.exists():
            run_h5p_cli(["core"], cwd=H5P_RUNTIME_DIR)
            core_marker.write_text("ok\n", encoding="utf-8")

        ensure_custom_h5p_libraries()
        ensure_registered_local_libraries()
        ensure_h5p_math_display_registered()

        if not list(H5P_RUNTIME_LIBRARIES_DIR.glob("H5P.Question-*")):
            run_h5p_cli(["setup", "h5p-question"], cwd=H5P_RUNTIME_DIR)

        ensure_h5p_editor_dependencies()


def collect_required_library_dirs(machine_name: str, major_version: int | None = None, minor_version: int | None = None, seen: set[str] | None = None) -> list[Path]:
    seen = seen or set()
    library_dir = find_library_dir(machine_name, major_version, minor_version)
    library_name = library_dir.name
    if library_name in seen:
        return []

    seen.add(library_name)
    metadata = read_json(library_dir / "library.json")
    required = [library_dir]
    for dependency in metadata.get("preloadedDependencies", []):
        required.extend(
            collect_required_library_dirs(
                dependency["machineName"],
                dependency.get("majorVersion"),
                dependency.get("minorVersion"),
                seen,
            )
        )
    return required


def collect_required_library_dirs_from_metadata(metadata_payload: dict[str, object]) -> list[Path]:
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
            collect_required_library_dirs(
                machine_name,
                int(dependency["majorVersion"]) if dependency.get("majorVersion") is not None else None,
                int(dependency["minorVersion"]) if dependency.get("minorVersion") is not None else None,
                seen,
            )
        )
    return required


def is_port_open(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        return sock.connect_ex((host, port)) == 0


def wait_for_port(host: str, port: int, timeout_seconds: float = 30.0) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if is_port_open(host, port):
            return
        time.sleep(0.2)
    raise TimeoutError(f"Der H5P-Preview-Server auf Port {port} wurde nicht rechtzeitig erreichbar.")


def ensure_h5p_runtime_server(port: int = H5P_RUNTIME_PORT) -> subprocess.Popen[str] | None:
    ensure_h5p_runtime_libraries()
    if is_port_open("127.0.0.1", port):
        return None

    process = subprocess.Popen(
        [*get_h5p_cli_command(), "server", str(port)],
        cwd=H5P_RUNTIME_DIR,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )

    try:
        wait_for_port("127.0.0.1", port)
    except Exception:
        process.terminate()
        process.wait(timeout=5)
        raise

    return process


def import_question_into_runtime(question: PythonQuestionBlock) -> None:
    with WORKSPACE_LOCK:
        content_dir = H5P_RUNTIME_CONTENT_DIR / question.runtime_content_id
        if content_dir.exists():
            shutil.rmtree(content_dir)
        run_h5p_cli(["import", question.runtime_content_id, str(question.package_path)], cwd=H5P_RUNTIME_DIR)


def build_runtime_preview_url(question: PythonQuestionBlock) -> str:
    return f"http://127.0.0.1:{H5P_RUNTIME_PORT}{build_runtime_route_path(question, 'view')}"


def resolve_runtime_short_name(machine_name: str = PYTHON_QUESTION_MACHINE_NAME) -> str:
    registry_path = H5P_RUNTIME_DIR / "libraryRegistry.json"
    if registry_path.exists():
        registry = read_json_or_default(registry_path, {})
        return registry.get(machine_name, {}).get("shortName", machine_name)
    return CUSTOM_H5P_LIBRARY_SHORT_NAMES.get(machine_name, machine_name)


def build_runtime_route_path(question: PythonQuestionBlock, mode: str, *, simple: bool = False) -> str:
    short_name = resolve_runtime_short_name(question.main_library)
    path = f"/{mode}/{quote_path_segment(short_name)}/{quote_path_segment(question.runtime_content_id)}"
    if simple:
        path = f"{path}?simple=1"
    return path


def build_runtime_proxy_path(question: PythonQuestionBlock, mode: str, *, simple: bool = False) -> str:
    return f"{RUNTIME_PROXY_PREFIX}{build_runtime_route_path(question, mode, simple=simple)}"


def build_h5p_metadata(question: PythonQuestionBlock) -> dict:
    library_metadata = read_json(find_library_dir(question.main_library) / "library.json")
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


def build_h5p_content(question: PythonQuestionBlock) -> dict:
    grading_method = question.grading_method
    if question.test_cases and grading_method == "please_choose":
        grading_method = "ioTestCases"

    return {
        "contentType": "ide_only",
        "pythonRunner": question.runner,
        "advancedOptions": {
            "showConsole": question.show_console,
            "disableOutputPopups": False,
            "enableSaveLoadButtons": True,
            "execLimit": 0,
        },
        "pyodideOptions": {
            "pyodideCdnUrl": "",
            "packages": [{"package": package_name} for package_name in question.packages],
        },
        "contents": [],
        "editorSettings": {
            "instructions": question.instructions,
            "preCode": question.pre_code,
            "startingCode": question.starter_code,
            "postCode": question.post_code,
            "options": {
                "enableImageUploads": False,
                "enableSoundUploads": False,
                "sourceFiles": [
                    {
                        "fileName": source_file.file_name,
                        "code": source_file.code,
                        "visibleToLearner": source_file.visible_to_learner,
                        "learnerEditable": source_file.learner_editable,
                    }
                    for source_file in question.source_files
                ],
                "allowAddingFiles": question.allow_adding_files,
                "editorMode": "code",
            },
        },
        "gradingSettings": {
            "gradingMethod": grading_method,
            "dueDateGroup": {
                "enableDueDate": False,
                "duedate": "01.01.1970",
            },
            "testCases": [
                {
                    "hidden": test_case.hidden,
                    "inputs": [{"input": value} for value in test_case.inputs],
                    "outputs": [{"output": value} for value in test_case.outputs],
                }
                for test_case in question.test_cases
            ],
            "targetCode": question.solution_code,
        },
    }


def sync_shared_h5p_libraries(question: PythonQuestionBlock, required_libraries: Iterable[Path]) -> list[Path]:
    ensure_directory(question.shared_libraries_dir)
    shared_libraries: list[Path] = []
    for library_dir in required_libraries:
        destination = question.shared_libraries_dir / library_dir.name
        if not destination.exists():
            shutil.copytree(library_dir, destination)
        shared_libraries.append(destination)
    return shared_libraries


def write_h5p_package(question: PythonQuestionBlock) -> Path:
    with WORKSPACE_LOCK:
        ensure_directory(question.package_path.parent)

        source_archive_path = (question.course_dir / question.source_package_path) if question.source_package_path else None
        source_mtime_ns = source_tree_mtime_ns(source_archive_path)
        index_mtime_ns = (question.course_dir / "index.mdx").stat().st_mtime_ns
        freshness_reference = max(index_mtime_ns, source_mtime_ns)

        if (question.package_url or source_archive_path is not None) and question.h5p_metadata is not None and question.h5p_content is not None:
            if question.package_path.exists() and question.package_path.stat().st_mtime_ns >= freshness_reference:
                return question.package_path

            metadata_payload = json.loads(json.dumps(question.h5p_metadata, ensure_ascii=False))
            content_payload = json.loads(json.dumps(question.h5p_content, ensure_ascii=False))
            metadata_payload["title"] = question.title
            metadata_payload["mainLibrary"] = question.main_library
            if "pythonRunner" in content_payload or question.main_library == PYTHON_QUESTION_MACHINE_NAME:
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
                    download_file(question.package_url, source_path)

                populate_imported_h5p_directory(source_path, question.exploded_dir, metadata_payload, content_payload)
                shared_libraries = sync_shared_h5p_libraries(
                    question,
                    collect_required_library_dirs_from_metadata(metadata_payload),
                )

                with ZipFile(question.package_path, "w", compression=ZIP_DEFLATED) as target_archive:
                    write_h5p_archive_from_directory(
                        target_archive,
                        question.exploded_dir,
                        shared_libraries=shared_libraries,
                        shared_libraries_root=question.shared_libraries_dir,
                    )

            return question.package_path

        if question.package_url and (question.raw_package or question.main_library != PYTHON_QUESTION_MACHINE_NAME):
            if question.package_path.exists() and question.package_path.stat().st_mtime_ns >= freshness_reference:
                return question.package_path
            download_file(question.package_url, question.package_path)
            return question.package_path

        ensure_h5p_runtime_libraries()
        if question.exploded_dir.exists():
            shutil.rmtree(question.exploded_dir)
        ensure_directory(question.exploded_dir)

        h5p_json = build_h5p_metadata(question)
        content_json = build_h5p_content(question)
        required_libraries = collect_required_library_dirs(question.main_library)
        shared_libraries = sync_shared_h5p_libraries(question, required_libraries)

        write_json(question.exploded_dir / "h5p.json", h5p_json)
        write_h5p_content_files(question.exploded_dir, content_json)

        with ZipFile(question.package_path, "w", compression=ZIP_DEFLATED) as archive:
            write_h5p_archive_from_directory(
                archive,
                question.exploded_dir,
                shared_libraries=shared_libraries,
                shared_libraries_root=question.shared_libraries_dir,
            )

        return question.package_path


def build_local_preview_path(question: PythonQuestionBlock) -> str:
    return preview_view_builder().build_local_preview_path(question)


def build_local_preview_path_with_options(question: PythonQuestionBlock, *, mode: str = "view", simple: bool = False) -> str:
    return preview_view_builder().build_local_preview_path_with_options(question, mode=mode, simple=simple)


def preview_view_builder() -> PreviewViewBuilder:
    global PREVIEW_VIEW_BUILDER
    if PREVIEW_VIEW_BUILDER is None:
        PREVIEW_VIEW_BUILDER = PreviewViewBuilder(
            runtime_proxy_prefix=RUNTIME_PROXY_PREFIX,
            quote_path_segment=quote_path_segment,
            escape_inline=escape_inline,
            build_runtime_proxy_path=build_runtime_proxy_path,
        )
    return PREVIEW_VIEW_BUILDER


def rewrite_runtime_html(document: str, runtime_path: str, query: str = "") -> str:
    runtime_origin_pattern = rf"https?://(?:localhost|127\.0\.0\.1):{H5P_RUNTIME_PORT}"
    document = re.sub(runtime_origin_pattern + r"(?=/|[\"'])", RUNTIME_PROXY_PREFIX, document)
    document = re.sub(
        r'([\'"`])/(?!runtime(?:/|[\'"`]|$))',
        lambda match: f"{match.group(1)}{RUNTIME_PROXY_PREFIX}/",
        document,
    )

    if runtime_path.startswith("/view/"):
        view_override = """
<style>
    #sessions,
    #newSessionButton,
    #newSession,
    #resetSessionButton,
    .submenu {
        display: none !important;
    }
</style>
<script>
window.addEventListener('load', () => {
    const sessionSelect = document.getElementById('sessions');
    const sessionContainer = sessionSelect?.closest('.menu-holder');
    if (sessionContainer) {
        sessionContainer.remove();
    }
});
</script>
""".strip()
        document = document.replace("</head>", f"{view_override}\n</head>", 1)

    if runtime_path.startswith("/edit/"):
        edit_override = """
<style>
  #menu {
    display: none !important;
  }
</style>
""".strip()
        document = document.replace("</head>", f"{edit_override}\n</head>", 1)

    if runtime_path.startswith("/split/"):
        split_override = """
<style>
  .h5p-cli-view > .col50 {
    display: none !important;
  }
</style>
""".strip()
        document = document.replace("</head>", f"{split_override}\n</head>", 1)

    if runtime_path.startswith("/view/") and "simple=1" in query:
        chrome_override = f"""
<style>
    html, body {{
        margin: 0 !important;
        padding: 0 !important;
        background: transparent !important;
    }}
    #status,
    .menu-holder,
    .theme-controls {{
        display: none !important;
    }}
    .holder,
    .h5p-cli-iframe-wrapper {{
        margin: 0 !important;
        padding: 0 !important;
    }}
    .h5p-cli-iframe-wrapper {{
        border: 0 !important;
        box-shadow: none !important;
    }}
    .h5p-iframe {{
        display: block;
        width: 100%;
        min-height: 540px;
    }}
</style>
""".strip()
        document = document.replace("</head>", f"{chrome_override}\n</head>", 1)

    return document


def sync_course(course_dir: Path) -> list[PythonQuestionBlock]:
    with WORKSPACE_LOCK:
        _, questions, _ = parse_course(course_dir)
        for question in questions:
            write_h5p_package(question)
        return questions


def load_course_preview_state(course_dir: Path) -> tuple[list[PythonQuestionBlock], str]:
    mdx_mtime_ns = (course_dir / "index.mdx").stat().st_mtime_ns
    cached = PREVIEW_CACHE.get(course_dir.name)
    if cached and cached[0] == mdx_mtime_ns:
        return cached[1], cached[2]

    _, questions, rendered_source = parse_course(course_dir)
    html_content = render_course_page(course_dir, questions=questions, rendered_source=rendered_source)
    PREVIEW_CACHE[course_dir.name] = (mdx_mtime_ns, questions, html_content)
    return questions, html_content


def find_question_by_runtime_content_id(runtime_content_id: str) -> PythonQuestionBlock | None:
    for course_dir in sorted(item for item in COURSES_DIR.iterdir() if item.is_dir()):
        questions, _ = load_course_preview_state(course_dir)
        for question in questions:
            if question.runtime_content_id == runtime_content_id:
                return question
    return None


def ensure_runtime_question_ready(question: PythonQuestionBlock) -> None:
    RUNTIME_PREPARATION.ensure_runtime_question_ready(
        question,
        compute_question_hash=compute_question_hash,
        write_h5p_package=write_h5p_package,
        import_question_into_runtime=import_question_into_runtime,
    )


def is_runtime_question_ready(question: PythonQuestionBlock) -> bool:
    return RUNTIME_PREPARATION.is_runtime_question_ready(question, compute_question_hash=compute_question_hash)


def start_runtime_question_preparation(question: PythonQuestionBlock) -> None:
    RUNTIME_PREPARATION.start_runtime_question_preparation(
    question,
    is_runtime_question_ready=is_runtime_question_ready,
    ensure_runtime_question_ready=ensure_runtime_question_ready,
    )


def get_runtime_preparation_state(question: PythonQuestionBlock) -> dict[str, str]:
    return RUNTIME_PREPARATION.get_runtime_preparation_state(
        question,
        is_runtime_question_ready=is_runtime_question_ready,
    )


def render_preview_waiting_page(question: PythonQuestionBlock, *, mode: str = "view", simple: bool = False) -> str:
    return preview_view_builder().render_preview_waiting_page(question, mode=mode, simple=simple)


def resolve_runtime_question_from_path(runtime_path: str) -> PythonQuestionBlock | None:
    parts = [part for part in runtime_path.strip("/").split("/") if part]
    if len(parts) >= 3 and parts[0] in {"view", "edit", "split"}:
        return find_question_by_runtime_content_id(unquote(parts[2]))
    if len(parts) >= 2 and parts[0] == "remove":
        return find_question_by_runtime_content_id(unquote(parts[1]))
    return None


def escape_inline(value: str) -> str:
    return html.escape(value, quote=True)


def quote_path_segment(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._~-]", lambda match: f"%{ord(match.group(0)):02X}", value)


def build_runtime_content_id(course_slug: str, identifier: str) -> str:
    return f"{quote_path_segment(course_slug)}-{quote_path_segment(identifier)}"


def render_markdown(markdown_text: str, question_html: dict[str, str]) -> str:
    lines = markdown_text.splitlines()
    blocks: list[str] = []
    paragraph: list[str] = []
    list_items: list[str] = []
    in_code = False
    code_lines: list[str] = []
    code_language = ""

    def flush_paragraph() -> None:
        if paragraph:
            blocks.append(f"<p>{escape_inline(' '.join(paragraph))}</p>")
            paragraph.clear()

    def flush_list() -> None:
        if list_items:
            items = "".join(f"<li>{escape_inline(item)}</li>" for item in list_items)
            blocks.append(f"<ul>{items}</ul>")
            list_items.clear()

    for raw_line in lines:
        line = raw_line.rstrip()

        if in_code:
            if line.startswith("```"):
                code_html = escape_inline("\n".join(code_lines))
                language_class = f" language-{escape_inline(code_language)}" if code_language else ""
                blocks.append(f"<pre><code class=\"{language_class.strip()}\">{code_html}</code></pre>")
                code_lines.clear()
                code_language = ""
                in_code = False
            else:
                code_lines.append(raw_line)
            continue

        if line.startswith("```"):
            flush_paragraph()
            flush_list()
            in_code = True
            code_language = line[3:].strip().split()[0] if line[3:].strip() else ""
            continue

        placeholder_match = re.fullmatch(r"\[\[\[PYTHON_QUESTION:(.+?)\]\]\]", line.strip())
        if placeholder_match:
            flush_paragraph()
            flush_list()
            identifier = placeholder_match.group(1)
            blocks.append(question_html.get(identifier, ""))
            continue

        if not line.strip():
            flush_paragraph()
            flush_list()
            continue

        if line.startswith("# "):
            flush_paragraph()
            flush_list()
            blocks.append(f"<h1>{escape_inline(line[2:].strip())}</h1>")
            continue

        if line.startswith("## "):
            flush_paragraph()
            flush_list()
            blocks.append(f"<h2>{escape_inline(line[3:].strip())}</h2>")
            continue

        if line.startswith("### "):
            flush_paragraph()
            flush_list()
            blocks.append(f"<h3>{escape_inline(line[4:].strip())}</h3>")
            continue

        if line.startswith("- "):
            flush_paragraph()
            list_items.append(line[2:].strip())
            continue

        paragraph.append(line.strip())

    flush_paragraph()
    flush_list()

    return "\n".join(blocks)


def build_question_component(question: PythonQuestionBlock) -> str:
    return preview_view_builder().build_question_component(question)


def render_course_page(
    course_dir: Path,
    *,
    questions: list[PythonQuestionBlock] | None = None,
    rendered_source: str | None = None,
) -> str:
    if questions is None or rendered_source is None:
        _, questions, rendered_source = parse_course(course_dir)

    question_html = {question.identifier: build_question_component(question) for question in questions}
    content_html = render_markdown(rendered_source, question_html)

    title = course_dir.name
    return f"""
<!doctype html>
<html lang="de">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{escape_inline(title)} Vorschau</title>
    <style>
      :root {{
        color-scheme: light;
        --bg: #f4efe8;
        --surface: rgba(255, 250, 244, 0.9);
        --surface-strong: #fffdf9;
        --ink: #1c140d;
        --muted: #725b47;
        --accent: #b6532f;
        --accent-soft: #f1d7c5;
        --border: rgba(92, 58, 32, 0.18);
        --shadow: 0 20px 45px rgba(73, 43, 22, 0.12);
      }}
      * {{ box-sizing: border-box; }}
      body {{
        margin: 0;
        font-family: Georgia, "Iowan Old Style", "Palatino Linotype", serif;
        color: var(--ink);
        background:
          radial-gradient(circle at top left, rgba(182, 83, 47, 0.12), transparent 26%),
          linear-gradient(180deg, #fbf6ef 0%, var(--bg) 100%);
      }}
      main {{
        width: min(1100px, calc(100% - 32px));
        margin: 0 auto;
        padding: 48px 0 96px;
      }}
      h1, h2, h3 {{
        line-height: 1.1;
        letter-spacing: -0.03em;
      }}
      p, li {{
        font-size: 1.05rem;
        line-height: 1.65;
      }}
      pre {{
        overflow-x: auto;
        padding: 16px;
        border-radius: 16px;
        background: #22180f;
        color: #f7f0e8;
        box-shadow: inset 0 1px 0 rgba(255,255,255,0.06);
      }}
      code {{ font-family: "Fira Code", "SFMono-Regular", Consolas, monospace; }}
            .python-question-card {{
                padding: 18px;
        margin: 28px 0;
        border: 1px solid var(--border);
        border-radius: 28px;
        background: var(--surface);
        box-shadow: var(--shadow);
        backdrop-filter: blur(10px);
      }}
            .question-toolbar {{
                display: flex;
                align-items: center;
                justify-content: space-between;
                gap: 16px;
                margin-bottom: 14px;
                padding: 10px 12px 14px;
                border-bottom: 1px solid var(--border);
            }}
            .question-toolbar-title {{
                min-width: 0;
                font-size: 1rem;
                white-space: nowrap;
                overflow: hidden;
                text-overflow: ellipsis;
      }}
            .question-toolbar-actions {{
        display: flex;
        flex-wrap: wrap;
        justify-content: flex-end;
        gap: 10px;
      }}
            .question-action-link,
            .question-action-button {{
        display: inline-flex;
        align-items: center;
        padding: 8px 12px;
        border-radius: 999px;
                text-decoration: none;
        color: var(--ink);
        background: var(--accent-soft);
        border: 1px solid rgba(182, 83, 47, 0.18);
                font: inherit;
                cursor: pointer;
            }}
            .question-action-button {{
                appearance: none;
            }}
                        .question-action-button.primary {{
                                background: var(--accent);
                                border-color: var(--accent);
                                color: #fff7f0;
                        }}
            .question-action-button.danger {{
                background: rgba(126, 33, 23, 0.08);
                border-color: rgba(126, 33, 23, 0.18);
                color: #7e2117;
      }}
            .preview-modal {{
                position: fixed;
                inset: 0;
                display: none;
                align-items: center;
                justify-content: center;
                padding: 20px;
                background: rgba(28, 20, 13, 0.42);
                backdrop-filter: blur(8px);
                z-index: 1000;
            }}
            .preview-modal.is-open {{
                display: flex;
            }}
            .preview-modal-dialog {{
                width: min(1200px, 100%);
                height: min(90vh, 920px);
                display: flex;
                flex-direction: column;
                border-radius: 28px;
                border: 1px solid var(--border);
                background: var(--surface-strong);
                box-shadow: 0 24px 60px rgba(34, 24, 15, 0.28);
                overflow: hidden;
            }}
            .preview-modal-header {{
                display: flex;
                align-items: center;
                justify-content: space-between;
                gap: 16px;
                padding: 16px 18px;
                border-bottom: 1px solid var(--border);
                background: rgba(255, 248, 241, 0.94);
            }}
            .preview-modal-title {{
                font-size: 1.05rem;
                font-weight: 700;
            }}
            .preview-modal-body {{
                flex: 1;
                min-height: 0;
                background: #fff;
            }}
            .preview-modal-frame {{
        width: 100%;
                height: 100%;
        border: 0;
        display: block;
      }}
      @media (max-width: 900px) {{
                .question-toolbar {{
                    align-items: flex-start;
                    flex-direction: column;
        }}
                .question-toolbar-actions {{ justify-content: flex-start; }}
                .preview-modal {{ padding: 12px; }}
                .preview-modal-dialog {{ height: min(94vh, 920px); }}
                .preview-modal-header {{ align-items: flex-start; flex-direction: column; }}
      }}
    </style>
  </head>
  <body>
    <main>
      {content_html}
    </main>
                <div class="preview-modal" id="preview-modal" aria-hidden="true">
                        <div class="preview-modal-dialog" role="dialog" aria-modal="true" aria-labelledby="preview-modal-title">
                                <div class="preview-modal-header">
                                        <div class="preview-modal-title" id="preview-modal-title">H5P Vorschau</div>
                                        <div class="question-toolbar-actions">
                                                <button class="question-action-button" type="button" data-modal-action="close">Schließen</button>
                                        </div>
                                </div>
                                <div class="preview-modal-body">
                                        <iframe class="preview-modal-frame" id="preview-modal-frame" loading="lazy" title="H5P Vorschau"></iframe>
                                </div>
                        </div>
                </div>
        <script>
                        const modal = document.getElementById("preview-modal");
                        const modalFrame = document.getElementById("preview-modal-frame");
                        const modalTitle = document.getElementById("preview-modal-title");

                        const closeModal = () => {{
                                if (!(modal instanceof HTMLElement) || !(modalFrame instanceof HTMLIFrameElement)) {{
                                        return;
                                }}
                                modal.classList.remove("is-open");
                                modal.setAttribute("aria-hidden", "true");
                                modalFrame.src = "about:blank";
                        }};

                        const openModal = (frameSrc, frameTitle) => {{
                                if (!(modal instanceof HTMLElement) || !(modalFrame instanceof HTMLIFrameElement) || !(modalTitle instanceof HTMLElement)) {{
                                        return;
                                }}
                                modal.classList.add("is-open");
                                modal.setAttribute("aria-hidden", "false");
                                modalTitle.textContent = frameTitle || "H5P Vorschau";
                                modalFrame.src = frameSrc;
                        }};

            document.addEventListener("click", (event) => {{
                const target = event.target;
                if (!(target instanceof HTMLElement)) {{
                    return;
                }}

                                if (target.dataset.openModal === "true" && target.dataset.frameSrc) {{
                                        openModal(target.dataset.frameSrc, target.dataset.frameTitle || target.textContent || "H5P Vorschau");
                                        return;
                                }}

                                if (target.dataset.modalAction === "close" || target === modal) {{
                                        closeModal();
                                        return;
                }}

                if (target.dataset.action === "delete-runtime") {{
                    const card = target.closest(".python-question-card");
                    const deleteUrl = target.dataset.deleteUrl;
                    if (!deleteUrl) {{
                        return;
                    }}

                    if (!window.confirm("Are you sure you want to delete this H5P content?")) {{
                        return;
                    }}

                    fetch(deleteUrl, {{ method: "POST" }})
                        .then((response) => {{
                            if (!response.ok) {{
                                throw new Error(`Delete failed: ${{response.status}}`);
                            }}
                            closeModal();
                            if (card instanceof HTMLElement) {{
                                card.remove();
                            }}
                        }})
                        .catch((error) => {{
                            console.error(error);
                            window.alert("Löschen fehlgeschlagen.");
                        }});
                    }}
            }});

            document.addEventListener("keydown", (event) => {{
                if (event.key === "Escape") {{
                    closeModal();
                }}
            }});
        </script>
  </body>
</html>
""".strip()


class CoursePreviewHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)

        if path.startswith(f"{RUNTIME_PROXY_PREFIX}/"):
            self.proxy_runtime_request("GET", parsed)
            return

        if path in {"/", ""}:
            course_dirs = sorted([item for item in COURSES_DIR.iterdir() if item.is_dir()])
            if not course_dirs:
                self.send_error(HTTPStatus.NOT_FOUND, "Kein Kurs gefunden.")
                return
            self.respond_html(self.render_index(course_dirs))
            return

        if path.startswith("/courses/"):
            parts = path.strip("/").split("/")
            if len(parts) != 2:
                self.send_error(HTTPStatus.NOT_FOUND, "Unbekannter Kurs-Pfad.")
                return
            course_dir = COURSES_DIR / parts[1]
            if not course_dir.exists():
                self.send_error(HTTPStatus.NOT_FOUND, "Kurs nicht gefunden.")
                return
            questions, html_content = load_course_preview_state(course_dir)
            if questions:
                start_runtime_question_preparation(questions[0])
            self.respond_html(html_content)
            return

        if path.startswith("/preview-status/"):
            parts = path.strip("/").split("/")
            if len(parts) != 3:
                self.send_error(HTTPStatus.NOT_FOUND, "Unbekannter Preview-Status-Pfad.")
                return

            course_dir = COURSES_DIR / parts[1]
            if not course_dir.exists():
                self.send_error(HTTPStatus.NOT_FOUND, "Kurs nicht gefunden.")
                return

            questions, _ = load_course_preview_state(course_dir)
            question = next((item for item in questions if item.identifier == parts[2]), None)
            if question is None:
                self.send_error(HTTPStatus.NOT_FOUND, "PythonQuestion nicht gefunden.")
                return

            state = get_runtime_preparation_state(question)
            if state["status"] == "idle":
                start_runtime_question_preparation(question)
                state = get_runtime_preparation_state(question)
            self.respond_json(state)
            return

        if path.startswith("/preview/"):
            parts = path.strip("/").split("/")
            if len(parts) != 3:
                self.send_error(HTTPStatus.NOT_FOUND, "Unbekannter Preview-Pfad.")
                return

            course_dir = COURSES_DIR / parts[1]
            if not course_dir.exists():
                self.send_error(HTTPStatus.NOT_FOUND, "Kurs nicht gefunden.")
                return

            questions, _ = load_course_preview_state(course_dir)
            question = next((item for item in questions if item.identifier == parts[2]), None)
            if question is None:
                self.send_error(HTTPStatus.NOT_FOUND, "PythonQuestion nicht gefunden.")
                return

            query = parse_qs(parsed.query)
            mode = str(query.get("mode", ["view"])[0]).strip().lower() or "view"
            if mode not in {"view", "edit", "split"}:
                mode = "view"
            simple = str(query.get("simple", [""])[0]).strip().lower() in {"1", "true", "yes", "on"}

            if is_runtime_question_ready(question):
                self.respond_redirect(build_runtime_proxy_path(question, mode, simple=simple))
                return

            start_runtime_question_preparation(question)
            self.respond_html(render_preview_waiting_page(question, mode=mode, simple=simple))
            return

        if path.startswith("/files/"):
            relative = path.removeprefix("/files/")
            file_path = COURSES_DIR / relative
            if not file_path.exists() or not file_path.is_file():
                self.send_error(HTTPStatus.NOT_FOUND, "Datei nicht gefunden.")
                return
            self.respond_file(file_path)
            return

        self.send_error(HTTPStatus.NOT_FOUND, "Route nicht gefunden.")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        if path.startswith(f"{RUNTIME_PROXY_PREFIX}/"):
            self.proxy_runtime_request("POST", parsed)
            return
        self.send_error(HTTPStatus.NOT_FOUND, "Route nicht gefunden.")

    def do_DELETE(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        if path.startswith(f"{RUNTIME_PROXY_PREFIX}/"):
            self.proxy_runtime_request("DELETE", parsed)
            return
        self.send_error(HTTPStatus.NOT_FOUND, "Route nicht gefunden.")

    def log_message(self, format: str, *args: object) -> None:
        return

    def proxy_runtime_request(self, method: str, parsed) -> None:
        ensure_h5p_runtime_server()

        runtime_path = parsed.path.removeprefix(RUNTIME_PROXY_PREFIX) or "/"
        question = resolve_runtime_question_from_path(runtime_path)
        if question is not None and method != "DELETE":
            ensure_runtime_question_ready(question)
        connection = http.client.HTTPConnection("127.0.0.1", H5P_RUNTIME_PORT, timeout=30)
        body = self.read_request_body()
        headers = self.build_runtime_proxy_headers(body)
        target = runtime_path
        if parsed.query:
            target = f"{target}?{parsed.query}"

        try:
            connection.request(method, target, body=body, headers=headers)
            response = connection.getresponse()
            payload = response.read()
        except OSError as exc:
            self.send_error(HTTPStatus.BAD_GATEWAY, f"H5P-Runtime nicht erreichbar: {exc}")
            return
        finally:
            connection.close()

        self.send_response(response.status)
        for header, value in response.getheaders():
            lower = header.lower()
            if lower in {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers", "transfer-encoding", "upgrade"}:
                continue
            if lower == "location" and value.startswith("/"):
                value = f"{RUNTIME_PROXY_PREFIX}{value}"
            if lower == "content-length":
                continue
            self.send_header(header, value)
        content_type = response.getheader("Content-Type", "")
        rewritten_payload = payload
        if "text/html" in content_type:
            document = payload.decode("utf-8")
            document = rewrite_runtime_html(document, runtime_path, parsed.query)
            rewritten_payload = document.encode("utf-8")
        self.send_header("Content-Length", str(len(rewritten_payload)))
        self.end_headers()
        self.wfile.write(rewritten_payload)

    def build_runtime_proxy_headers(self, body: bytes) -> dict[str, str]:
        headers: dict[str, str] = {}
        for header, value in self.headers.items():
            lower = header.lower()
            if lower in {"host", "connection", "content-length"}:
                continue
            headers[header] = value
        if body:
            headers["Content-Length"] = str(len(body))
        return headers

    def read_request_body(self) -> bytes:
        length = self.headers.get("Content-Length")
        if not length:
            return b""
        return self.rfile.read(int(length))

    def render_index(self, course_dirs: Iterable[Path]) -> str:
        links = "".join(
            f'<li><a href="/courses/{escape_inline(course_dir.name)}">{escape_inline(course_dir.name)}</a></li>'
            for course_dir in course_dirs
        )
        return f"""
<!doctype html>
<html lang="de">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Kursvorschau</title>
    <style>
      body {{
        margin: 0;
        font-family: Georgia, serif;
        background: #f4efe8;
        color: #1c140d;
      }}
      main {{ width: min(800px, calc(100% - 32px)); margin: 0 auto; padding: 48px 0; }}
      ul {{ padding-left: 20px; }}
      a {{ color: #9b431f; }}
    </style>
  </head>
  <body>
    <main>
      <h1>Lokale Kursvorschau</h1>
    <p>Die Vorschau rendert die Kursseite sofort und baut einzelne H5P-Inhalte erst beim Öffnen.</p>
      <ul>{links}</ul>
    </main>
  </body>
</html>
""".strip()

    def respond_html(self, content: str) -> None:
        payload = content.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def respond_redirect(self, location: str) -> None:
        self.send_response(HTTPStatus.FOUND)
        self.send_header("Location", location)
        self.end_headers()

    def respond_json(self, payload: dict[str, str]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def respond_file(self, file_path: Path) -> None:
        data = file_path.read_bytes()
        content_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def serve_preview(port: int) -> None:
    runtime_process = ensure_h5p_runtime_server()
    server = ThreadingHTTPServer(("127.0.0.1", port), CoursePreviewHandler)
    print(f"Preview läuft auf http://127.0.0.1:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        if runtime_process is not None:
            runtime_process.terminate()
            runtime_process.wait(timeout=5)


def resolve_course_dir(course: str) -> Path:
    course_dir = COURSES_DIR / course
    if not course_dir.exists():
        raise FileNotFoundError(f"Kurs '{course}' wurde nicht gefunden.")
    return course_dir


def print_course_status(status: dict[str, object]) -> None:
    counts = status["counts"]
    print(f"Kurs: {status['course']} (remote course id: {status['remoteCourseId']})")
    print(f"Moodle: {status['moodleBaseUrl']}")
    print(
        "Status: "
        f"tracked={counts['tracked']}, "
        f"modified-local={counts['modified-local']}, "
        f"local-only={counts['local-only']}, "
        f"remote-only={counts['remote-only']}"
    )
    for item in status["items"]:
        print(f"- {item['identifier']}: {item['status']} (remote activity id: {item['remoteActivityId']})")


def print_moodle_ping_report(report: dict[str, object]) -> None:
    print(f"Moodle erreichbar: {report['baseUrl']}")
    print(f"Site: {report['siteName']} ({report['siteUrl']})")
    print(f"Benutzer: {report['fullName']} ({report['userName']}, id={report['userId']})")
    print(
        "Import-API: "
        + ("verfügbar" if report["supportsCourseImport"] else "fehlt: core_course_get_contents nicht freigegeben")
    )


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Synchronisiert PythonQuestion-Blöcke aus MDX nach H5P und stellt eine Browser-Vorschau bereit.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    sync_parser = subparsers.add_parser("sync", help="Erzeugt H5P-Dateien aus einer Kurs-MDX.")
    sync_parser.add_argument("course", help="Kursordner unter courses/, zum Beispiel python-2026")

    serve_parser = subparsers.add_parser("serve", help="Startet die lokale Browser-Vorschau.")
    serve_parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"Port für den Preview-Server. Standard: {DEFAULT_PORT}")

    import_parser = subparsers.add_parser("import-moodle", help="Importiert einen vorhandenen Moodle-Kurs als lokale MDX-Struktur.")
    import_parser.add_argument("course", help="Lokaler Kursordner unter courses/, zum Beispiel python-2026")
    import_parser.add_argument("remote_course_id", type=int, help="Remote Moodle Course ID")
    import_parser.add_argument("--base-url", help="Moodle-Basis-URL. Fällt sonst auf MOODLE_BASE_URL zurück.")
    import_parser.add_argument("--token", help="Moodle-Token. Fällt sonst auf MOODLE_TOKEN zurück.")

    ping_parser = subparsers.add_parser("moodle-ping", help="Prüft, ob die konfigurierte Moodle-Webservice-Verbindung funktioniert.")
    ping_parser.add_argument("--base-url", help="Moodle-Basis-URL. Fällt sonst auf MOODLE_BASE_URL zurück.")
    ping_parser.add_argument("--token", help="Moodle-Token. Fällt sonst auf MOODLE_TOKEN zurück.")

    status_parser = subparsers.add_parser("status", help="Zeigt den lokalen Sync-Status eines importierten Moodle-Kurses.")
    status_parser.add_argument("course", help="Lokaler Kursordner unter courses/, zum Beispiel python-2026")

    return parser


def main() -> None:
    parser = build_arg_parser()
    args = parser.parse_args()

    try:
        if args.command == "sync":
            course_dir = resolve_course_dir(args.course)
            questions = sync_course(course_dir)
            for question in questions:
                print(question.package_path.relative_to(ROOT_DIR))
            return

        if args.command == "serve":
            serve_preview(args.port)
            return

        if args.command == "import-moodle":
            client = resolve_moodle_client(base_url=args.base_url, token=args.token)
            course_dir = import_moodle_course(args.course, args.remote_course_id, client)
            print(course_dir.relative_to(ROOT_DIR))
            print(sync_metadata_path(course_dir).relative_to(ROOT_DIR))
            return

        if args.command == "moodle-ping":
            client = resolve_moodle_client(base_url=args.base_url, token=args.token)
            print_moodle_ping_report(build_moodle_ping_report(client))
            return

        if args.command == "status":
            course_dir = resolve_course_dir(args.course)
            print_course_status(build_course_status(course_dir))
            return
    except (FileNotFoundError, RuntimeError, ValueError) as error:
        parser.exit(1, f"Fehler: {error}\n")

    parser.error("Unbekanntes Kommando.")


if __name__ == "__main__":
    main()