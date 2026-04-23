"""Centralized singleton service container for lazy-initialized services."""

from __future__ import annotations

import re

from scripts.classes import (
    CourseOrchestrator,
    H5PImportMapper,
    MarkdownRenderer,
    MdxCourseParser,
    PreviewController,
    PreviewViewBuilder,
    RuntimeHtmlRewriter,
    TemplateRenderer,
)
from scripts.classes.component_sync import ComponentSyncer
from scripts.classes.h5p_runtime_manager.runtime_manager import H5PRuntimeManager
from scripts.classes.moodle_sync import MoodleSyncer


class ServiceRegistry:
    """Centralized singleton service container for cached services."""

    def __init__(self) -> None:
        self._preview_view_builder: PreviewViewBuilder | None = None
        self._preview_controller: PreviewController | None = None
        self._markdown_renderer: MarkdownRenderer | None = None
        self._template_renderer: TemplateRenderer | None = None
        self._component_syncer: ComponentSyncer | None = None
        self._h5p_runtime_manager: H5PRuntimeManager | None = None
        self._moodle_syncer: MoodleSyncer | None = None
        self._course_orchestrator: CourseOrchestrator | None = None
        self._runtime_html_rewriter: RuntimeHtmlRewriter | None = None
        self._mdx_course_parser: MdxCourseParser | None = None
        self._h5p_import_mapper: H5PImportMapper | None = None

    def get_preview_view_builder(
        self,
        runtime_proxy_prefix: str,
        quote_path_segment: callable,  # type: ignore[type-arg]
        escape_inline: callable,  # type: ignore[type-arg]
        build_runtime_proxy_path: callable,  # type: ignore[type-arg]
    ) -> PreviewViewBuilder:
        if self._preview_view_builder is None:
            self._preview_view_builder = PreviewViewBuilder(
                runtime_proxy_prefix=runtime_proxy_prefix,
                quote_path_segment=quote_path_segment,
                escape_inline=escape_inline,
                build_runtime_proxy_path=build_runtime_proxy_path,
            )
        return self._preview_view_builder

    def get_preview_controller(
        self,
        courses_dir: object,
        load_course_preview_state: callable,  # type: ignore[type-arg]
        get_runtime_preparation_state: callable,  # type: ignore[type-arg]
        start_runtime_question_preparation: callable,  # type: ignore[type-arg]
        is_runtime_question_ready: callable,  # type: ignore[type-arg]
        build_runtime_proxy_path: callable,  # type: ignore[type-arg]
        render_preview_waiting_page: callable,  # type: ignore[type-arg]
    ) -> PreviewController:
        if self._preview_controller is None:
            self._preview_controller = PreviewController(
                courses_dir=courses_dir,
                load_course_preview_state=load_course_preview_state,
                get_runtime_preparation_state=get_runtime_preparation_state,
                start_runtime_question_preparation=start_runtime_question_preparation,
                is_runtime_question_ready=is_runtime_question_ready,
                build_runtime_proxy_path=build_runtime_proxy_path,
                render_preview_waiting_page=render_preview_waiting_page,
            )
        return self._preview_controller

    def get_markdown_renderer(
        self,
        escape_inline: callable,  # type: ignore[type-arg]
    ) -> MarkdownRenderer:
        if self._markdown_renderer is None:
            self._markdown_renderer = MarkdownRenderer(escape_inline=escape_inline)
        return self._markdown_renderer

    def get_template_renderer(
        self,
        escape_inline: callable,  # type: ignore[type-arg]
    ) -> TemplateRenderer:
        if self._template_renderer is None:
            self._template_renderer = TemplateRenderer(escape_inline=escape_inline)
        return self._template_renderer

    def get_component_syncer(
        self,
        python_question_machine_name: str,
        load_python_question_semantics: callable,  # type: ignore[type-arg]
        load_h5p_payload_from_source_package: callable,  # type: ignore[type-arg]
        build_h5p_metadata: callable,  # type: ignore[type-arg]
    ) -> ComponentSyncer:
        if self._component_syncer is None:
            self._component_syncer = ComponentSyncer(
                python_question_machine_name=python_question_machine_name,
                load_python_question_semantics=load_python_question_semantics,
                load_h5p_payload_from_source_package=load_h5p_payload_from_source_package,
                build_h5p_metadata=build_h5p_metadata,
            )
        return self._component_syncer

    def get_h5p_runtime_manager(
        self,
        runtime_dir: object,
        runtime_port: int,
        runtime_proxy_prefix: str,
        custom_h5p_library_short_names: dict,  # type: ignore[type-arg]
        runtime_preparation: object,
        get_preview_view_builder: callable,  # type: ignore[type-arg]
        compute_question_hash: callable,  # type: ignore[type-arg]
        write_h5p_package: callable,  # type: ignore[type-arg]
        import_question_into_runtime: callable,  # type: ignore[type-arg]
        read_json_or_default: callable,  # type: ignore[type-arg]
    ) -> H5PRuntimeManager:
        if self._h5p_runtime_manager is None:
            self._h5p_runtime_manager = H5PRuntimeManager(
                runtime_dir=runtime_dir,
                runtime_port=runtime_port,
                runtime_proxy_prefix=runtime_proxy_prefix,
                custom_h5p_library_short_names=custom_h5p_library_short_names,
                runtime_preparation=runtime_preparation,
                get_preview_view_builder=get_preview_view_builder,
                compute_question_hash=compute_question_hash,
                write_h5p_package=write_h5p_package,
                import_question_into_runtime=import_question_into_runtime,
                read_json_or_default=read_json_or_default,
            )
        return self._h5p_runtime_manager

    def get_moodle_syncer(
        self,
        courses_dir: object,
        ensure_directory: callable,  # type: ignore[type-arg]
        render_imported_question_mdx: callable,  # type: ignore[type-arg]
        parse_course: callable,  # type: ignore[type-arg]
        compute_question_hash: callable,  # type: ignore[type-arg]
        save_sync_metadata: callable,  # type: ignore[type-arg]
        escape_mdx_attribute: callable,  # type: ignore[type-arg]
    ) -> MoodleSyncer:
        if self._moodle_syncer is None:
            self._moodle_syncer = MoodleSyncer(
                courses_dir=courses_dir,
                ensure_directory=ensure_directory,
                render_imported_question_mdx=render_imported_question_mdx,
                parse_course=parse_course,
                compute_question_hash=compute_question_hash,
                save_sync_metadata=save_sync_metadata,
                escape_mdx_attribute=escape_mdx_attribute,
            )
        return self._moodle_syncer

    def get_course_orchestrator(
        self,
        workspace_lock: object,
        courses_dir: object,
        preview_cache: dict,  # type: ignore[type-arg]
        parse_course: callable,  # type: ignore[type-arg]
        write_h5p_package: callable,  # type: ignore[type-arg]
        render_course_page: callable,  # type: ignore[type-arg]
        load_sync_metadata: callable,  # type: ignore[type-arg]
        compute_question_hash: callable,  # type: ignore[type-arg]
    ) -> CourseOrchestrator:
        if self._course_orchestrator is None:
            self._course_orchestrator = CourseOrchestrator(
                workspace_lock=workspace_lock,
                courses_dir=courses_dir,
                preview_cache=preview_cache,
                parse_course=parse_course,
                write_h5p_package=write_h5p_package,
                render_course_page=render_course_page,
                load_sync_metadata=load_sync_metadata,
                compute_question_hash=compute_question_hash,
            )
        return self._course_orchestrator

    def get_runtime_html_rewriter(
        self,
        runtime_port: int,
        runtime_proxy_prefix: str,
    ) -> RuntimeHtmlRewriter:
        if self._runtime_html_rewriter is None:
            self._runtime_html_rewriter = RuntimeHtmlRewriter(
                runtime_port=runtime_port,
                runtime_proxy_prefix=runtime_proxy_prefix,
            )
        return self._runtime_html_rewriter

    def get_mdx_course_parser(
        self,
        tag_re: re.Pattern,  # type: ignore[type-arg]
        fence_re: re.Pattern,  # type: ignore[type-arg]
        placeholder_template: str,
        python_question_machine_name: str,
        parse_jsx_expression: callable,  # type: ignore[type-arg]
        normalize_whitespace: callable,  # type: ignore[type-arg]
        infer_source_package_sidecar_path: callable,  # type: ignore[type-arg]
        build_imported_question_from_sidecar: callable,  # type: ignore[type-arg]
        load_h5p_sidecar_file_wrapper: callable,  # type: ignore[type-arg]
        apply_editable_h5p_payload: callable,  # type: ignore[type-arg]
    ) -> MdxCourseParser:
        if self._mdx_course_parser is None:
            self._mdx_course_parser = MdxCourseParser(
                tag_re=tag_re,
                fence_re=fence_re,
                placeholder_template=placeholder_template,
                python_question_machine_name=python_question_machine_name,
                parse_jsx_expression=parse_jsx_expression,
                normalize_whitespace=normalize_whitespace,
                infer_source_package_sidecar_path=infer_source_package_sidecar_path,
                build_imported_question_from_sidecar=build_imported_question_from_sidecar,
                load_h5p_sidecar_file=load_h5p_sidecar_file_wrapper,
                apply_editable_h5p_payload=apply_editable_h5p_payload,
            )
        return self._mdx_course_parser

    def get_h5p_import_mapper(
        self,
        compact_text: callable,  # type: ignore[type-arg]
        normalize_whitespace: callable,  # type: ignore[type-arg]
    ) -> H5PImportMapper:
        if self._h5p_import_mapper is None:
            self._h5p_import_mapper = H5PImportMapper(
                compact_text=compact_text,
                normalize_whitespace=normalize_whitespace,
            )
        return self._h5p_import_mapper

