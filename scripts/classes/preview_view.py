from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Protocol
from urllib.parse import urlencode

from .template_renderer import _fill

_TEMPLATES_DIR = Path(__file__).parent / "templates"


class QuestionLike(Protocol):
    identifier: str
    title: str
    course_slug: str
    runtime_content_id: str
    h5p_subdir: str
    package_path: Path


class PreviewViewBuilder:
    """View-builder for preview URLs and waiting page HTML."""

    def __init__(
        self,
        *,
        runtime_proxy_prefix: str,
        quote_path_segment: Callable[[str], str],
        escape_inline: Callable[[str], str],
        build_runtime_proxy_path: Callable[..., str],
    ) -> None:
        self.runtime_proxy_prefix = runtime_proxy_prefix
        self.quote_path_segment = quote_path_segment
        self.escape_inline = escape_inline
        self.build_runtime_proxy_path = build_runtime_proxy_path

    def build_local_preview_path(self, question: QuestionLike) -> str:
        return f"/preview/{self.quote_path_segment(question.course_slug)}/{self.quote_path_segment(question.identifier)}"

    def build_local_preview_path_with_options(self, question: QuestionLike, *, mode: str = "view", simple: bool = False) -> str:
        path = self.build_local_preview_path(question)
        query = {"mode": mode}
        if simple:
            query["simple"] = "1"
        return f"{path}?{urlencode(query)}"

    def build_question_component(self, question: QuestionLike) -> str:
        package_name = self.quote_path_segment(question.package_path.name)
        package_src = f"build/h5p/{package_name}"
        if question.h5p_subdir:
            package_src = f"build/h5p/{self.quote_path_segment(question.h5p_subdir)}/{package_name}"
        return self.build_static_question_component(question, package_src=package_src)

    def build_static_question_component(self, question: QuestionLike, *, package_src: str) -> str:
        template = (_TEMPLATES_DIR / "static_question_card.html").read_text("utf-8")
        return _fill(
            template,
            identifier=self.escape_inline(question.identifier),
            title=self.escape_inline(question.title),
            package_src=self.escape_inline(package_src),
        ).strip()

    def render_preview_waiting_page(self, question: QuestionLike, *, mode: str = "view", simple: bool = False) -> str:
        status_url = (
            f"/preview-status/{self.quote_path_segment(question.course_slug)}/"
            f"{self.quote_path_segment(question.identifier)}"
        )
        target_url = self.build_runtime_proxy_path(question, mode, simple=simple)
        template = (_TEMPLATES_DIR / "waiting_page.html").read_text("utf-8")
        return _fill(
            template,
            status_url_json=json.dumps(status_url),
            target_url_json=json.dumps(target_url),
        ).strip()
