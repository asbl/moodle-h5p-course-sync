from __future__ import annotations

import html
import os
import subprocess
import threading
from pathlib import Path
from typing import Iterable
from zipfile import ZipFile

from scripts._http_client import (
    download_file,
    extract_h5p_package_url_from_activity_html,
    fetch_json,
    fetch_text,
)

from scripts.classes import (
    CourseOrchestrator,
    ContentStore,
    H5PFileService,
    H5PImportMapper,
    H5PLibraryManager,
    H5PPackageBuilder,
    MarkdownRenderer,
    MdxCourseParser,
    MoodleH5PActivity,
    PreviewController,
    PreviewViewBuilder,
    PythonQuestionBlock,
    RuntimeCliService,
    RuntimePreparationService,
    RuntimeHtmlRewriter,
    SourceFile,
    SyncMetadata,
    SyncMetadataEntry,
    TemplateRenderer,
    TestCase,
    TextOperations,
)
from scripts.classes.cli import (
    build_arg_parser as build_cli_arg_parser,
    print_course_status as print_cli_course_status,
    print_moodle_ping_report as print_cli_moodle_ping_report,
    run_cli_command,
    serve_preview as serve_preview_impl,
)
from scripts.classes.component_sync import ComponentSyncer
from scripts.classes.content_types import ImportedQuestionFactory
from scripts.classes.sync_metadata_store import SyncMetadataStore
from scripts.classes.workspace_io import WorkspaceIO
from scripts.config import AppConfig, settings
from scripts.classes.h5p_runtime_manager import quote_path_segment as quote_path_segment_helper
from scripts.classes.h5p_runtime_manager.runtime_manager import H5PRuntimeManager
from scripts.classes.moodle_sync import MoodleSyncer
from scripts.classes.moodle_sync import MoodleApiClient
from scripts.classes.moodle_sync import MoodleBackupExtractor
from scripts.classes.moodle_sync import MoodleClientResolver
from scripts._service_registry import ServiceRegistry


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
WORKSPACE_IO = WorkspaceIO(content_store=CONTENT_STORE)
RUNTIME_PREPARATION = RuntimePreparationService(H5P_RUNTIME_CONTENT_DIR)

TAG_RE = APP_CONFIG.tag_re
FENCE_RE = APP_CONFIG.fence_re
HTML_TAG_RE = APP_CONFIG.html_tag_re
WHITESPACE_RE = APP_CONFIG.whitespace_re
MBZ_LINK_RE = APP_CONFIG.mbz_link_re


_SERVICE_REGISTRY = ServiceRegistry()



def moodle_backup_extractor() -> MoodleBackupExtractor:
    # Intentionally not cached so test monkeypatches on module-level helpers stay effective.
    return MoodleBackupExtractor(
        mbz_link_re=MBZ_LINK_RE,
        fetch_text=fetch_text,
        download_file=download_file,
        ensure_directory=ensure_directory,
    )


def normalize_whitespace(value: str) -> str:
    return text_operations().normalize_whitespace(value)


def strip_html(value: str) -> str:
    return text_operations().strip_html(value)


def compact_text(value: str) -> str:
    return text_operations().compact_text(value)


def make_stable_identifier(title: str, existing_identifiers: set[str]) -> str:
    return text_operations().make_stable_identifier(title, existing_identifiers)


def build_source_package_sidecar_path(question: PythonQuestionBlock) -> str:
    return h5p_file_service().build_source_package_sidecar_path(question)


def write_source_package_sidecar(question: PythonQuestionBlock, source_archive: Path) -> str:
    return h5p_file_service().write_source_package_sidecar(question, source_archive)


def load_h5p_payload_from_path(source_path: Path) -> tuple[dict[str, object], dict[str, object]] | None:
    return h5p_file_service().load_h5p_payload_from_path(source_path)


def source_tree_mtime_ns(path: Path | None) -> int:
    return h5p_file_service().source_tree_mtime_ns(path)


def populate_imported_h5p_directory(source_path: Path, target_dir: Path, metadata_payload: dict[str, object], content_payload: dict[str, object]) -> None:
    h5p_file_service().populate_imported_h5p_directory(source_path, target_dir, metadata_payload, content_payload)


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


def parse_course(course_dir: Path) -> tuple[str, list[PythonQuestionBlock], str]:
    return mdx_course_parser().parse_course(course_dir)


def load_sync_metadata(course_dir: Path) -> SyncMetadata | None:
    return _SYNC_METADATA_STORE.load(course_dir)


def save_sync_metadata(course_dir: Path, metadata: SyncMetadata) -> Path:
    return _SYNC_METADATA_STORE.save(course_dir, metadata)


def compute_question_hash(question: PythonQuestionBlock) -> str:
    return component_syncer().compute_question_hash(question)


def import_moodle_course(course: str, remote_course_id: int, client: MoodleApiClient) -> Path:
    return moodle_syncer().import_moodle_course(
        course=course,
        remote_course_id=remote_course_id,
        client=client,
    )


def push_moodle_course(course_dir: Path, remote_course_id: int, client: MoodleApiClient) -> list[PythonQuestionBlock]:
    return moodle_syncer().push_moodle_course(
        course_dir=course_dir,
        remote_course_id=remote_course_id,
        client=client,
        sync_course=sync_course,
    )


def build_course_status(course_dir: Path) -> dict[str, object]:
    return course_orchestrator().build_course_status(course_dir)


def build_moodle_ping_report(client: MoodleApiClient) -> dict[str, object]:
    return MoodleSyncer.build_moodle_ping_report(client)


def resolve_moodle_client(base_url: str | None = None, token: str | None = None) -> MoodleApiClient:
    return moodle_client_resolver().resolve_moodle_client(base_url, token)


def ensure_directory(path: Path) -> None:
    WORKSPACE_IO.ensure_directory(path)


_SYNC_METADATA_STORE = SyncMetadataStore(
    sync_metadata_file=SYNC_METADATA_FILE,
    ensure_directory=ensure_directory,
)


def read_json(path: Path) -> dict:
    return WORKSPACE_IO.read_json(path)


def read_yaml(path: Path) -> object:
    return WORKSPACE_IO.read_yaml(path)


def read_json_or_default(path: Path, default: dict) -> dict:
    return WORKSPACE_IO.read_json_or_default(path, default)


def write_json(path: Path, payload: dict) -> None:
    WORKSPACE_IO.write_json(path, payload)


def read_h5p_content_payload(source_dir: Path) -> dict[str, object]:
    return WORKSPACE_IO.read_h5p_content_payload(source_dir)


def write_h5p_content_files(target_dir: Path, payload: dict[str, object]) -> None:
    WORKSPACE_IO.write_h5p_content_files(target_dir, payload)


def write_h5p_archive_from_directory(
    archive: ZipFile,
    source_dir: Path,
    *,
    shared_libraries: Iterable[Path] = (),
    shared_libraries_root: Path | None = None,
) -> None:
    h5p_file_service().write_h5p_archive_from_directory(
        archive,
        source_dir,
        shared_libraries=shared_libraries,
        shared_libraries_root=shared_libraries_root,
    )


def find_library_dir(machine_name: str, major_version: int | None = None, minor_version: int | None = None) -> Path:
    return h5p_library_manager().find_library_dir(machine_name, major_version, minor_version)


def ensure_h5p_runtime_libraries() -> None:
    h5p_library_manager().ensure_h5p_runtime_libraries()


def collect_required_library_dirs(machine_name: str, major_version: int | None = None, minor_version: int | None = None, seen: set[str] | None = None) -> list[Path]:
    return h5p_library_manager().collect_required_library_dirs(machine_name, major_version, minor_version, seen)


def collect_required_library_dirs_from_metadata(metadata_payload: dict[str, object]) -> list[Path]:
    return h5p_library_manager().collect_required_library_dirs_from_metadata(metadata_payload)


def ensure_h5p_runtime_server(port: int = H5P_RUNTIME_PORT) -> subprocess.Popen[str] | None:
    return runtime_cli_service().ensure_h5p_runtime_server(port)


def import_question_into_runtime(question: PythonQuestionBlock) -> None:
    runtime_cli_service().import_question_into_runtime(question)


def build_runtime_proxy_path(question: PythonQuestionBlock, mode: str, *, simple: bool = False) -> str:
    return h5p_runtime_manager().build_runtime_proxy_path(question, mode, simple=simple)


def build_h5p_metadata(question: PythonQuestionBlock) -> dict:
    return h5p_package_builder().build_h5p_metadata(question)


def build_h5p_content(question: PythonQuestionBlock) -> dict:
    return h5p_package_builder().build_h5p_content(question)


def write_h5p_package(question: PythonQuestionBlock) -> Path:
    return h5p_package_builder().write_h5p_package(question)


def preview_view_builder() -> PreviewViewBuilder:
    return _SERVICE_REGISTRY.get_preview_view_builder(
        runtime_proxy_prefix=RUNTIME_PROXY_PREFIX,
        quote_path_segment=quote_path_segment,
        escape_inline=escape_inline,
        build_runtime_proxy_path=build_runtime_proxy_path,
    )


def preview_controller() -> PreviewController:
    return _SERVICE_REGISTRY.get_preview_controller(
        courses_dir=COURSES_DIR,
        load_course_preview_state=load_course_preview_state,
        get_runtime_preparation_state=get_runtime_preparation_state,
        start_runtime_question_preparation=start_runtime_question_preparation,
        is_runtime_question_ready=is_runtime_question_ready,
        build_runtime_proxy_path=build_runtime_proxy_path,
        render_preview_waiting_page=render_preview_waiting_page,
    )


def markdown_renderer() -> MarkdownRenderer:
    return _SERVICE_REGISTRY.get_markdown_renderer(escape_inline=escape_inline)


def template_renderer() -> TemplateRenderer:
    return _SERVICE_REGISTRY.get_template_renderer(escape_inline=escape_inline)


def component_syncer() -> ComponentSyncer:
    return _SERVICE_REGISTRY.get_component_syncer(
        python_question_machine_name=PYTHON_QUESTION_MACHINE_NAME,
        load_python_question_semantics=lambda: [
            field
            for field in payload
            if isinstance(field, dict)
        ]
        if isinstance((payload := read_json(find_library_dir(PYTHON_QUESTION_MACHINE_NAME) / "semantics.json")), list)
        else (_ for _ in ()).throw(ValueError("semantics.json fuer H5P.PythonQuestion muss ein JSON-Array sein.")),
        load_h5p_payload_from_source_package=load_h5p_payload_from_source_package,
        build_h5p_metadata=build_h5p_metadata,
    )


def h5p_library_manager() -> H5PLibraryManager:
    # Intentionally not cached so tests can monkeypatch module-level dependencies safely.
    return H5PLibraryManager(
        workspace_lock=WORKSPACE_LOCK,
        runtime_dir=H5P_RUNTIME_DIR,
        runtime_content_dir=H5P_RUNTIME_CONTENT_DIR,
        runtime_libraries_dir=H5P_RUNTIME_LIBRARIES_DIR,
        runtime_downloads_dir=H5P_RUNTIME_DOWNLOADS_DIR,
        shared_libraries_dir=ROOT_DIR / "libraries",
        courses_dir=COURSES_DIR,
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
    )


def runtime_cli_service() -> RuntimeCliService:
    # Intentionally not cached so tests can monkeypatch module-level dependencies safely.
    library_manager = h5p_library_manager()
    return RuntimeCliService(
        workspace_lock=WORKSPACE_LOCK,
        runtime_dir=H5P_RUNTIME_DIR,
        runtime_content_dir=H5P_RUNTIME_CONTENT_DIR,
        backend=library_manager,
    )


def h5p_file_service() -> H5PFileService:
    # Intentionally not cached so tests can monkeypatch module-level dependencies safely.
    return H5PFileService(
        courses_dir=COURSES_DIR,
        ensure_directory=ensure_directory,
        read_yaml=read_yaml,
        read_h5p_content_payload=read_h5p_content_payload,
        write_h5p_content_files=write_h5p_content_files,
        write_json=write_json,
    )


def moodle_client_resolver() -> MoodleClientResolver:
    # Intentionally not cached so tests can monkeypatch module-level dependencies safely.
    return MoodleClientResolver(
        dotenv_file=DOTENV_FILE,
        make_stable_identifier=make_stable_identifier,
        strip_html=strip_html,
        fetch_text=fetch_text,
        extract_h5p_package_url_from_activity_html=lambda page_html, base_url: extract_h5p_package_url_from_activity_html(
            page_html,
            base_url=base_url,
        ),
        download_file=download_file,
        moodle_backup_extractor_factory=moodle_backup_extractor,
        build_imported_question_from_h5p_package=build_imported_question_from_h5p_package,
        write_source_package_sidecar=write_source_package_sidecar,
        environ=os.environ,
    )


def text_operations() -> TextOperations:
    # Intentionally not cached so tests can monkeypatch module-level dependencies safely.
    return TextOperations(
        html_tag_re=HTML_TAG_RE,
        whitespace_re=WHITESPACE_RE,
    )


def h5p_runtime_manager() -> H5PRuntimeManager:
    return _SERVICE_REGISTRY.get_h5p_runtime_manager(
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


def moodle_syncer() -> MoodleSyncer:
    return _SERVICE_REGISTRY.get_moodle_syncer(
        courses_dir=COURSES_DIR,
        ensure_directory=ensure_directory,
        render_imported_question_mdx=render_imported_question_mdx,
        parse_course=parse_course,
        compute_question_hash=compute_question_hash,
        save_sync_metadata=save_sync_metadata,
        escape_mdx_attribute=escape_mdx_attribute,
    )


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
    return _SERVICE_REGISTRY.get_course_orchestrator(
        workspace_lock=WORKSPACE_LOCK,
        courses_dir=COURSES_DIR,
        preview_cache=PREVIEW_CACHE,
        parse_course=parse_course,
        write_h5p_package=write_h5p_package,
        render_course_page=render_course_page,
        load_sync_metadata=load_sync_metadata,
        compute_question_hash=compute_question_hash,
    )


def runtime_html_rewriter() -> RuntimeHtmlRewriter:
    return _SERVICE_REGISTRY.get_runtime_html_rewriter(
        runtime_port=H5P_RUNTIME_PORT,
        runtime_proxy_prefix=RUNTIME_PROXY_PREFIX,
    )


def mdx_course_parser() -> MdxCourseParser:
    return _SERVICE_REGISTRY.get_mdx_course_parser(
        tag_re=TAG_RE,
        fence_re=FENCE_RE,
        placeholder_template=PLACEHOLDER_TEMPLATE,
        python_question_machine_name=PYTHON_QUESTION_MACHINE_NAME,
        parse_jsx_expression=lambda expression: component_syncer().parse_jsx_expression(expression),
        normalize_whitespace=normalize_whitespace,
        infer_source_package_sidecar_path=infer_source_package_sidecar_path,
        build_imported_question_from_sidecar=build_imported_question_from_sidecar,
        load_h5p_sidecar_file_wrapper=lambda course_dir, relative_path: h5p_file_service().load_h5p_sidecar_file(
            course_dir,
            relative_path,
            description="H5P-Sidecar",
        ),
        apply_editable_h5p_payload=lambda question, payload: component_syncer().apply_editable_h5p_payload(question, payload),
    )


def h5p_import_mapper() -> H5PImportMapper:
    return _SERVICE_REGISTRY.get_h5p_import_mapper(
        compact_text=compact_text,
        normalize_whitespace=normalize_whitespace,
    )


def imported_question_factory() -> ImportedQuestionFactory:
    # Intentionally not cached so tests can monkeypatch module-level dependencies safely.
    return ImportedQuestionFactory(
        courses_dir=COURSES_DIR,
        python_question_machine_name=PYTHON_QUESTION_MACHINE_NAME,
        normalize_whitespace=normalize_whitespace,
        strip_html=strip_html,
        import_mapper=h5p_import_mapper(),
    )


def rewrite_runtime_html(document: str, runtime_path: str, query: str = "") -> str:
    return runtime_html_rewriter().rewrite(document, runtime_path, query)


def sync_course(course_dir: Path) -> list[PythonQuestionBlock]:
    return course_orchestrator().sync_course(course_dir)


def list_course_dirs() -> list[Path]:
    return sorted(item for item in COURSES_DIR.iterdir() if item.is_dir())


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


def prepare_preview_runtime(course_dir: Path | None = None) -> list[PythonQuestionBlock]:
    ensure_h5p_runtime_libraries()
    target_course_dirs = [course_dir] if course_dir is not None else list_course_dirs()
    prepared_questions: list[PythonQuestionBlock] = []
    for target_course_dir in target_course_dirs:
        questions, _ = load_course_preview_state(target_course_dir)
        for question in questions:
            ensure_runtime_question_ready(question)
        prepared_questions.extend(questions)
    return prepared_questions


def main() -> None:
    parser = build_cli_arg_parser(DEFAULT_PORT)
    args = parser.parse_args()
    run_cli_command(
        args,
        parser=parser,
        root_dir=ROOT_DIR,
        courses_dir=COURSES_DIR,
        sync_course=sync_course,
        build_preview_runtime=prepare_preview_runtime,
        serve_preview=lambda port: serve_preview_impl(
            port,
            courses_dir=COURSES_DIR,
            runtime_proxy_prefix=RUNTIME_PROXY_PREFIX,
            h5p_runtime_port=H5P_RUNTIME_PORT,
            ensure_h5p_runtime_server=ensure_h5p_runtime_server,
            load_course_preview_state=load_course_preview_state,
            preview_controller=preview_controller,
            resolve_runtime_question_from_path=resolve_runtime_question_from_path,
            ensure_runtime_question_ready=ensure_runtime_question_ready,
            rewrite_runtime_html=rewrite_runtime_html,
            escape_inline=escape_inline,
            start_runtime_question_preparation=start_runtime_question_preparation,
            prepare_preview_runtime=lambda: prepare_preview_runtime(),
            template_renderer=template_renderer,
        ),
        resolve_moodle_client=resolve_moodle_client,
        import_moodle_course=import_moodle_course,
        push_moodle_course=push_moodle_course,
        sync_metadata_path=_SYNC_METADATA_STORE.path,
        build_moodle_ping_report=build_moodle_ping_report,
        print_moodle_ping_report=print_cli_moodle_ping_report,
        build_course_status=build_course_status,
        print_course_status=print_cli_course_status,
    )


if __name__ == "__main__":
    main()