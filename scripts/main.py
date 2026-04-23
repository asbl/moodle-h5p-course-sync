from __future__ import annotations

import argparse
import html
import json
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
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Iterable
from urllib.parse import parse_qs, quote, urlencode
from urllib.request import Request, urlopen
from urllib.parse import unquote, urljoin, urlparse, urlunparse
from xml.etree import ElementTree
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile

from scripts.classes import (
    CourseOrchestrator,
    ContentStore,
    H5PImportMapper,
    H5PLibraryManager,
    H5PPackageBuilder,
    MarkdownRenderer,
    MdxCourseParser,
    MoodleH5PActivity,
    PreviewController,
    PreviewHandlerContext,
    PreviewViewBuilder,
    PythonQuestionBlock,
    RuntimePreparationService,
    RuntimeHtmlRewriter,
    SourceFile,
    SyncMetadata,
    SyncMetadataEntry,
    TemplateRenderer,
    TestCase,
    build_course_preview_handler,
)
from scripts.classes.component_sync import ComponentSyncer
from scripts.classes.content_types import ImportedQuestionFactory
from scripts.config import AppConfig, settings
from scripts.classes.h5p_runtime_manager import build_runtime_content_id as build_runtime_content_id_helper
from scripts.classes.h5p_runtime_manager import quote_path_segment as quote_path_segment_helper
from scripts.classes.h5p_runtime_manager.runtime_manager import H5PRuntimeManager
from scripts.classes.moodle_sync import MoodleSyncer
from scripts.classes.moodle_sync import MoodleApiClient
from scripts.classes.moodle_sync import MoodleBackupExtractor


APP_CONFIG: AppConfig = settings.build_app_config()
ROOT_DIR = APP_CONFIG.root_dir
COURSES_DIR = APP_CONFIG.courses_dir
DEFAULT_PORT = APP_CONFIG.default_port
DOTENV_FILE = APP_CONFIG.dotenv_file
H5P_RUNTIME_DIR = APP_CONFIG.h5p_runtime_dir
H5P_RUNTIME_CONTENT_DIR = APP_CONFIG.h5p_runtime_content_dir
H5P_RUNTIME_LIBRARIES_DIR = APP_CONFIG.h5p_runtime_libraries_dir
H5P_RUNTIME_DOWNLOADS_DIR = APP_CONFIG.h5p_runtime_downloads_dir
H5P_RUNTIME_PORT = APP_CONFIG.h5p_runtime_port
RUNTIME_PROXY_PREFIX = APP_CONFIG.runtime_proxy_prefix
H5P_LIBRARY_RELEASE_REPO = APP_CONFIG.h5p_library_release_repo
H5P_LIBRARY_RELEASE_TAG = APP_CONFIG.h5p_library_release_tag
H5P_LIBRARY_ASSET_PREFIXES = APP_CONFIG.h5p_library_asset_prefixes
CUSTOM_H5P_LIBRARY_SHORT_NAMES = APP_CONFIG.custom_h5p_library_short_names
PYTHON_QUESTION_MACHINE_NAME = APP_CONFIG.python_question_machine_name
PLACEHOLDER_TEMPLATE = APP_CONFIG.placeholder_template
SYNC_METADATA_FILE = APP_CONFIG.sync_metadata_file
H5P_SIDECAR_DIRNAME = APP_CONFIG.h5p_sidecar_dirname
WORKSPACE_LOCK = threading.RLock()
PREVIEW_CACHE: dict[str, tuple[int, list[PythonQuestionBlock], str]] = {}
CONTENT_STORE = ContentStore()
RUNTIME_PREPARATION = RuntimePreparationService(H5P_RUNTIME_CONTENT_DIR)
PREVIEW_VIEW_BUILDER: PreviewViewBuilder | None = None
PREVIEW_CONTROLLER: PreviewController | None = None
MARKDOWN_RENDERER: MarkdownRenderer | None = None
TEMPLATE_RENDERER: TemplateRenderer | None = None
COMPONENT_SYNCER: ComponentSyncer | None = None
H5P_RUNTIME_MANAGER: H5PRuntimeManager | None = None
MOODLE_SYNCER: MoodleSyncer | None = None
H5P_PACKAGE_BUILDER: H5PPackageBuilder | None = None
COURSE_ORCHESTRATOR: CourseOrchestrator | None = None
RUNTIME_HTML_REWRITER: RuntimeHtmlRewriter | None = None
MDX_COURSE_PARSER: MdxCourseParser | None = None
H5P_IMPORT_MAPPER: H5PImportMapper | None = None

TAG_RE = APP_CONFIG.tag_re
FENCE_RE = APP_CONFIG.fence_re
HTML_TAG_RE = APP_CONFIG.html_tag_re
WHITESPACE_RE = APP_CONFIG.whitespace_re
H5P_EMBED_IFRAME_RE = APP_CONFIG.h5p_embed_iframe_re
MBZ_LINK_RE = APP_CONFIG.mbz_link_re


def moodle_backup_extractor() -> MoodleBackupExtractor:
    # Intentionally not cached so test monkeypatches on module-level helpers stay effective.
    return MoodleBackupExtractor(
        mbz_link_re=MBZ_LINK_RE,
        fetch_text=fetch_text,
        download_file=download_file,
        ensure_directory=ensure_directory,
    )


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


def build_h5p_sidecar_paths(question: PythonQuestionBlock) -> tuple[str, str]:
    base_dir = Path("h5p") / question.identifier
    return (base_dir / "h5p.json").as_posix(), (base_dir / "content.yml").as_posix()


def build_source_package_sidecar_path(question: PythonQuestionBlock) -> str:
    return (Path("h5p") / question.identifier).as_posix()


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

    if target_path.exists():
        if target_path.is_dir():
            shutil.rmtree(target_path)
        else:
            target_path.unlink()

    with ZipFile(source_archive) as archive:
        metadata_payload = json.loads(archive.read("h5p.json").decode("utf-8"))
        content_payload = json.loads(archive.read("content/content.json").decode("utf-8"))

    populate_imported_h5p_directory(source_archive, target_path, metadata_payload, content_payload)
    return relative_path


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
    return component_syncer().normalize_template_literal(content)


def jsx_expression_to_json(expression: str) -> str:
    return component_syncer().jsx_expression_to_json(expression)


def parse_jsx_expression(expression: str) -> object:
    return component_syncer().parse_jsx_expression(expression)


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


def build_default_imported_h5p_content(question: PythonQuestionBlock) -> dict[str, object]:
    if question.main_library != PYTHON_QUESTION_MACHINE_NAME:
        return {}
    return build_default_python_question_content(question)


def build_editable_h5p_payload(question: PythonQuestionBlock) -> dict[str, object]:
    return component_syncer().build_editable_h5p_payload(question)


def apply_editable_h5p_payload(question: PythonQuestionBlock, payload: dict[str, object]) -> None:
    component_syncer().apply_editable_h5p_payload(question, payload)


def infer_source_package_sidecar_path(question: PythonQuestionBlock) -> str:
    if question.course_dir is None:
        return ""
    relative_path = build_source_package_sidecar_path(question)
    if (question.course_dir / relative_path).exists():
        return relative_path
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
    return imported_question_factory().create_from_h5p_package(
        course_slug=course_slug,
        activity=activity,
        metadata_payload=metadata_payload,
        content_payload=content_payload,
    )


def render_imported_question_mdx(question: PythonQuestionBlock) -> list[str]:
    return component_syncer().render_imported_question_mdx(question)


def escape_mdx_attribute(value: str) -> str:
    return html.escape(value, quote=True)


def parse_tag_attributes(raw_attrs: str) -> dict[str, object]:
    return mdx_course_parser().parse_tag_attributes(raw_attrs)


def parse_course(course_dir: Path) -> tuple[str, list[PythonQuestionBlock], str]:
    return mdx_course_parser().parse_course(course_dir)


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
    return component_syncer().compute_question_hash(question)


def render_imported_course_mdx(course_slug: str, activities: list[MoodleH5PActivity]) -> str:
    return moodle_syncer().render_imported_course_mdx(course_slug, activities)


def import_moodle_course(course: str, remote_course_id: int, client: MoodleApiClient) -> Path:
    return moodle_syncer().import_moodle_course(
        course=course,
        remote_course_id=remote_course_id,
        client=client,
    )


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
    backup_extractor = moodle_backup_extractor()
    return MoodleApiClient(
        resolved_base_url,
        resolved_token,
        make_stable_identifier=make_stable_identifier,
        strip_html=strip_html,
        fetch_text=fetch_text,
        extract_h5p_package_url_from_activity_html=lambda page_html: extract_h5p_package_url_from_activity_html(
            page_html,
            base_url=resolved_base_url,
        ),
        download_file=download_file,
        extract_h5p_package_from_course_backup=lambda base_url, activity, destination: backup_extractor.extract_h5p_package_from_course_backup(
            base_url,
            activity,
            destination,
        ),
        build_imported_question_from_h5p_package=build_imported_question_from_h5p_package,
        write_source_package_sidecar=write_source_package_sidecar,
    )


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


def release_metadata_cache_path() -> Path:
    return h5p_library_manager().release_metadata_cache_path()


def get_h5p_cli_command() -> list[str]:
    return h5p_library_manager().get_h5p_cli_command()


def resolve_h5p_cli_command() -> list[str]:
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
    return h5p_library_manager().run_h5p_cli(args, cwd)


def run_h5p_cli_command(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [*resolve_h5p_cli_command(), *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


def find_library_dir(machine_name: str, major_version: int | None = None, minor_version: int | None = None) -> Path:
    return h5p_library_manager().find_library_dir(machine_name, major_version, minor_version)


def extract_library_asset(archive_path: Path, machine_name: str) -> Path:
    return h5p_library_manager().extract_library_asset(archive_path, machine_name)


def register_local_library(library_dir: Path) -> None:
    h5p_library_manager().register_local_library(library_dir)


def ensure_custom_h5p_libraries() -> None:
    h5p_library_manager().ensure_custom_h5p_libraries()


def ensure_h5p_runtime_libraries() -> None:
    h5p_library_manager().ensure_h5p_runtime_libraries()


def collect_required_library_dirs(machine_name: str, major_version: int | None = None, minor_version: int | None = None, seen: set[str] | None = None) -> list[Path]:
    return h5p_library_manager().collect_required_library_dirs(machine_name, major_version, minor_version, seen)


def collect_required_library_dirs_from_metadata(metadata_payload: dict[str, object]) -> list[Path]:
    return h5p_library_manager().collect_required_library_dirs_from_metadata(metadata_payload)


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


def build_runtime_proxy_path(question: PythonQuestionBlock, mode: str, *, simple: bool = False) -> str:
    return h5p_runtime_manager().build_runtime_proxy_path(question, mode, simple=simple)


def build_h5p_metadata(question: PythonQuestionBlock) -> dict:
    return h5p_package_builder().build_h5p_metadata(question)


def build_h5p_content(question: PythonQuestionBlock) -> dict:
    return h5p_package_builder().build_h5p_content(question)


def write_h5p_package(question: PythonQuestionBlock) -> Path:
    return h5p_package_builder().write_h5p_package(question)


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


def preview_controller() -> PreviewController:
    global PREVIEW_CONTROLLER
    if PREVIEW_CONTROLLER is None:
        PREVIEW_CONTROLLER = PreviewController(
            courses_dir=COURSES_DIR,
            load_course_preview_state=load_course_preview_state,
            get_runtime_preparation_state=get_runtime_preparation_state,
            start_runtime_question_preparation=start_runtime_question_preparation,
            is_runtime_question_ready=is_runtime_question_ready,
            build_runtime_proxy_path=build_runtime_proxy_path,
            render_preview_waiting_page=render_preview_waiting_page,
        )
    return PREVIEW_CONTROLLER


def markdown_renderer() -> MarkdownRenderer:
    global MARKDOWN_RENDERER
    if MARKDOWN_RENDERER is None:
        MARKDOWN_RENDERER = MarkdownRenderer(escape_inline=escape_inline)
    return MARKDOWN_RENDERER


def template_renderer() -> TemplateRenderer:
    global TEMPLATE_RENDERER
    if TEMPLATE_RENDERER is None:
        TEMPLATE_RENDERER = TemplateRenderer(escape_inline=escape_inline)
    return TEMPLATE_RENDERER


def component_syncer() -> ComponentSyncer:
    global COMPONENT_SYNCER
    if COMPONENT_SYNCER is None:
        COMPONENT_SYNCER = ComponentSyncer(
            python_question_machine_name=PYTHON_QUESTION_MACHINE_NAME,
            load_python_question_semantics=load_python_question_semantics,
            load_h5p_payload_from_source_package=load_h5p_payload_from_source_package,
            clone_json_value=clone_json_value,
            escape_h5p_value=escape_h5p_value,
            merge_json_values=merge_json_values,
            build_h5p_metadata=build_h5p_metadata,
            build_default_python_question_content=build_default_python_question_content,
            build_default_imported_h5p_metadata=build_default_imported_h5p_metadata,
            build_default_imported_h5p_content=build_default_imported_h5p_content,
        )
    return COMPONENT_SYNCER


def h5p_library_manager() -> H5PLibraryManager:
    # Intentionally not cached so tests can monkeypatch module-level dependencies safely.
    return H5PLibraryManager(
        workspace_lock=WORKSPACE_LOCK,
        runtime_dir=H5P_RUNTIME_DIR,
        runtime_content_dir=H5P_RUNTIME_CONTENT_DIR,
        runtime_libraries_dir=H5P_RUNTIME_LIBRARIES_DIR,
        runtime_downloads_dir=H5P_RUNTIME_DOWNLOADS_DIR,
        release_repo=H5P_LIBRARY_RELEASE_REPO,
        release_tag=H5P_LIBRARY_RELEASE_TAG,
        asset_prefixes=H5P_LIBRARY_ASSET_PREFIXES,
        custom_short_names=CUSTOM_H5P_LIBRARY_SHORT_NAMES,
        ensure_directory=ensure_directory,
        read_json=read_json,
        read_json_or_default=read_json_or_default,
        write_json=write_json,
        fetch_json=fetch_json,
        download_file=download_file,
        run_cli_command=run_h5p_cli_command,
        resolve_cli_command=resolve_h5p_cli_command,
    )


def h5p_runtime_manager() -> H5PRuntimeManager:
    global H5P_RUNTIME_MANAGER
    if H5P_RUNTIME_MANAGER is None:
        H5P_RUNTIME_MANAGER = H5PRuntimeManager(
            runtime_dir=H5P_RUNTIME_DIR,
            runtime_port=H5P_RUNTIME_PORT,
            runtime_proxy_prefix=RUNTIME_PROXY_PREFIX,
            custom_h5p_library_short_names=CUSTOM_H5P_LIBRARY_SHORT_NAMES,
            runtime_preparation=RUNTIME_PREPARATION,
            get_preview_view_builder=preview_view_builder,
            compute_question_hash=compute_question_hash,
            write_h5p_package=write_h5p_package,
            import_question_into_runtime=import_question_into_runtime,
            read_json_or_default=read_json_or_default,
        )
    return H5P_RUNTIME_MANAGER


def moodle_syncer() -> MoodleSyncer:
    global MOODLE_SYNCER
    if MOODLE_SYNCER is None:
        MOODLE_SYNCER = MoodleSyncer(
            courses_dir=COURSES_DIR,
            ensure_directory=ensure_directory,
            render_imported_question_mdx=render_imported_question_mdx,
            build_scaffold_question=build_scaffold_question,
            parse_course=parse_course,
            compute_question_hash=compute_question_hash,
            save_sync_metadata=save_sync_metadata,
            escape_mdx_attribute=escape_mdx_attribute,
        )
    return MOODLE_SYNCER


def h5p_package_builder() -> H5PPackageBuilder:
    return H5PPackageBuilder(
        workspace_lock=WORKSPACE_LOCK,
        python_question_machine_name=PYTHON_QUESTION_MACHINE_NAME,
        ensure_directory=ensure_directory,
        source_tree_mtime_ns=source_tree_mtime_ns,
        download_file=download_file,
        populate_imported_h5p_directory=populate_imported_h5p_directory,
        collect_required_library_dirs_from_metadata=collect_required_library_dirs_from_metadata,
        collect_required_library_dirs=collect_required_library_dirs,
        write_h5p_archive_from_directory=write_h5p_archive_from_directory,
        write_h5p_content_files=write_h5p_content_files,
        ensure_h5p_runtime_libraries=ensure_h5p_runtime_libraries,
        build_h5p_content=lambda question: component_syncer().build_h5p_content(question),
        read_json=read_json,
        find_library_dir=find_library_dir,
    )


def course_orchestrator() -> CourseOrchestrator:
    global COURSE_ORCHESTRATOR
    if COURSE_ORCHESTRATOR is None:
        COURSE_ORCHESTRATOR = CourseOrchestrator(
            workspace_lock=WORKSPACE_LOCK,
            courses_dir=COURSES_DIR,
            preview_cache=PREVIEW_CACHE,
            parse_course=parse_course,
            write_h5p_package=write_h5p_package,
            render_course_page=render_course_page,
        )
    return COURSE_ORCHESTRATOR


def runtime_html_rewriter() -> RuntimeHtmlRewriter:
    global RUNTIME_HTML_REWRITER
    if RUNTIME_HTML_REWRITER is None:
        RUNTIME_HTML_REWRITER = RuntimeHtmlRewriter(
            runtime_port=H5P_RUNTIME_PORT,
            runtime_proxy_prefix=RUNTIME_PROXY_PREFIX,
        )
    return RUNTIME_HTML_REWRITER


def mdx_course_parser() -> MdxCourseParser:
    global MDX_COURSE_PARSER
    if MDX_COURSE_PARSER is None:
        MDX_COURSE_PARSER = MdxCourseParser(
            tag_re=TAG_RE,
            fence_re=FENCE_RE,
            placeholder_template=PLACEHOLDER_TEMPLATE,
            python_question_machine_name=PYTHON_QUESTION_MACHINE_NAME,
            parse_jsx_expression=parse_jsx_expression,
            normalize_whitespace=normalize_whitespace,
            infer_source_package_sidecar_path=infer_source_package_sidecar_path,
            build_imported_question_from_sidecar=build_imported_question_from_sidecar,
            load_h5p_sidecar_file=lambda course_dir, relative_path: load_h5p_sidecar_file(
                course_dir,
                relative_path,
                description="H5P-Sidecar",
            ),
            apply_editable_h5p_payload=apply_editable_h5p_payload,
        )
    return MDX_COURSE_PARSER


def h5p_import_mapper() -> H5PImportMapper:
    global H5P_IMPORT_MAPPER
    if H5P_IMPORT_MAPPER is None:
        H5P_IMPORT_MAPPER = H5PImportMapper(
            compact_text=compact_text,
            normalize_whitespace=normalize_whitespace,
        )
    return H5P_IMPORT_MAPPER


def imported_question_factory() -> ImportedQuestionFactory:
    # Intentionally not cached so tests can monkeypatch module-level dependencies safely.
    return ImportedQuestionFactory(
        courses_dir=COURSES_DIR,
        python_question_machine_name=PYTHON_QUESTION_MACHINE_NAME,
        normalize_whitespace=normalize_whitespace,
        summarize_questionset=summarize_questionset,
        import_mapper=h5p_import_mapper(),
    )


def rewrite_runtime_html(document: str, runtime_path: str, query: str = "") -> str:
    return runtime_html_rewriter().rewrite(document, runtime_path, query)


def sync_course(course_dir: Path) -> list[PythonQuestionBlock]:
    return course_orchestrator().sync_course(course_dir)


def load_course_preview_state(course_dir: Path) -> tuple[list[PythonQuestionBlock], str]:
    return course_orchestrator().load_course_preview_state(course_dir)


def find_question_by_runtime_content_id(runtime_content_id: str) -> PythonQuestionBlock | None:
    return course_orchestrator().find_question_by_runtime_content_id(runtime_content_id)


def ensure_runtime_question_ready(question: PythonQuestionBlock) -> None:
    h5p_runtime_manager().ensure_runtime_question_ready(question)


def is_runtime_question_ready(question: PythonQuestionBlock) -> bool:
    return h5p_runtime_manager().is_runtime_question_ready(question)


def start_runtime_question_preparation(question: PythonQuestionBlock) -> None:
    h5p_runtime_manager().start_runtime_question_preparation(question)


def get_runtime_preparation_state(question: PythonQuestionBlock) -> dict[str, str]:
    return h5p_runtime_manager().get_runtime_preparation_state(question)


def render_preview_waiting_page(question: PythonQuestionBlock, *, mode: str = "view", simple: bool = False) -> str:
    return h5p_runtime_manager().render_preview_waiting_page(question, mode=mode, simple=simple)


def resolve_runtime_question_from_path(runtime_path: str) -> PythonQuestionBlock | None:
    runtime_content_id = h5p_runtime_manager().resolve_runtime_content_id_from_path(runtime_path)
    if runtime_content_id is None:
        return None
    return find_question_by_runtime_content_id(runtime_content_id)


def escape_inline(value: str) -> str:
    return html.escape(value, quote=True)


def quote_path_segment(value: str) -> str:
    return quote_path_segment_helper(value)


def build_runtime_content_id(course_slug: str, identifier: str) -> str:
    return build_runtime_content_id_helper(course_slug, identifier)


def render_course_page(
    course_dir: Path,
    *,
    questions: list[PythonQuestionBlock] | None = None,
    rendered_source: str | None = None,
) -> str:
    if questions is None or rendered_source is None:
        _, questions, rendered_source = parse_course(course_dir)

    view_builder = preview_view_builder()
    question_html = {question.identifier: view_builder.build_question_component(question) for question in questions}
    content_html = markdown_renderer().render(rendered_source, question_html)

    return template_renderer().render_course_page(title=course_dir.name, content_html=content_html)


def serve_preview(port: int) -> None:
    runtime_process = ensure_h5p_runtime_server()
    handler = build_course_preview_handler(
        PreviewHandlerContext(
            courses_dir=COURSES_DIR,
            runtime_proxy_prefix=RUNTIME_PROXY_PREFIX,
            h5p_runtime_port=H5P_RUNTIME_PORT,
            load_course_preview_state=load_course_preview_state,
            preview_controller=preview_controller,
            resolve_runtime_question_from_path=resolve_runtime_question_from_path,
            ensure_runtime_question_ready=ensure_runtime_question_ready,
            ensure_h5p_runtime_server=ensure_h5p_runtime_server,
            rewrite_runtime_html=rewrite_runtime_html,
            escape_inline=escape_inline,
            start_runtime_question_preparation=start_runtime_question_preparation,
            template_renderer=template_renderer,
        )
    )
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
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