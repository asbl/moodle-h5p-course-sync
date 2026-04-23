"""Centralized singleton service container for lazy initialization of cached services.

This module defines the ServiceRegistry class which manages all singleton services
in the application. Services are lazily initialized on first access. All services
take their dependencies as parameters from main.py provider functions.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

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

if TYPE_CHECKING:
    pass


class ServiceRegistry:
    """
    Centralized singleton service container. Lazily initializes cached services on first access.
    
    All dependencies are passed in via parameters from main.py provider functions at the time
    each getter is called. This avoids circular import issues and simplifies testing.
    """

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
        self, factory: callable,  # type: ignore[type-arg]
    ) -> PreviewViewBuilder:
        if self._preview_view_builder is None:
            self._preview_view_builder = factory()
        return self._preview_view_builder

    def get_preview_controller(
        self, factory: callable,  # type: ignore[type-arg]
    ) -> PreviewController:
        if self._preview_controller is None:
            self._preview_controller = factory()
        return self._preview_controller

    def get_markdown_renderer(
        self, factory: callable,  # type: ignore[type-arg]
    ) -> MarkdownRenderer:
        if self._markdown_renderer is None:
            self._markdown_renderer = factory()
        return self._markdown_renderer

    def get_template_renderer(
        self, factory: callable,  # type: ignore[type-arg]
    ) -> TemplateRenderer:
        if self._template_renderer is None:
            self._template_renderer = factory()
        return self._template_renderer

    def get_component_syncer(
        self, factory: callable,  # type: ignore[type-arg]
    ) -> ComponentSyncer:
        if self._component_syncer is None:
            self._component_syncer = factory()
        return self._component_syncer

    def get_h5p_runtime_manager(
        self, factory: callable,  # type: ignore[type-arg]
    ) -> H5PRuntimeManager:
        if self._h5p_runtime_manager is None:
            self._h5p_runtime_manager = factory()
        return self._h5p_runtime_manager

    def get_moodle_syncer(
        self, factory: callable,  # type: ignore[type-arg]
    ) -> MoodleSyncer:
        if self._moodle_syncer is None:
            self._moodle_syncer = factory()
        return self._moodle_syncer

    def get_course_orchestrator(
        self, factory: callable,  # type: ignore[type-arg]
    ) -> CourseOrchestrator:
        if self._course_orchestrator is None:
            self._course_orchestrator = factory()
        return self._course_orchestrator

    def get_runtime_html_rewriter(
        self, factory: callable,  # type: ignore[type-arg]
    ) -> RuntimeHtmlRewriter:
        if self._runtime_html_rewriter is None:
            self._runtime_html_rewriter = factory()
        return self._runtime_html_rewriter

    def get_mdx_course_parser(
        self, factory: callable,  # type: ignore[type-arg]
    ) -> MdxCourseParser:
        if self._mdx_course_parser is None:
            self._mdx_course_parser = factory()
        return self._mdx_course_parser

    def get_h5p_import_mapper(
        self, factory: callable,  # type: ignore[type-arg]
    ) -> H5PImportMapper:
        if self._h5p_import_mapper is None:
            self._h5p_import_mapper = factory()
        return self._h5p_import_mapper

