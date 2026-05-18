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
        frame_src = self.build_local_preview_path_with_options(question, mode="view", simple=True)
        status_src = (
            f"/preview-status/{self.quote_path_segment(question.course_slug)}/"
            f"{self.quote_path_segment(question.identifier)}"
        )
        rebuild_src = (
            f"/preview-rebuild/{self.quote_path_segment(question.course_slug)}/"
            f"{self.quote_path_segment(question.identifier)}"
        )
        template = (_TEMPLATES_DIR / "question_card.html").read_text("utf-8")
        return _fill(
            template,
            identifier=self.escape_inline(question.identifier),
            title=self.escape_inline(question.title),
            frame_src=self.escape_inline(frame_src),
            status_src=self.escape_inline(status_src),
            rebuild_src=self.escape_inline(rebuild_src),
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
