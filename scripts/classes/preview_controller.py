from __future__ import annotations

from dataclasses import dataclass
from http import HTTPStatus
from pathlib import Path
from typing import Callable, Protocol


class QuestionLike(Protocol):
    identifier: str


@dataclass(slots=True)
class PreviewStatusResult:
    status_code: int
    payload: dict[str, str] | None = None
    error_message: str = ""


@dataclass(slots=True)
class PreviewRenderResult:
    status_code: int
    redirect_url: str | None = None
    waiting_page_html: str | None = None
    error_message: str = ""


class PreviewController:
    """Controller for preview-specific route decisions."""

    def __init__(
        self,
        *,
        courses_dir: Path,
        load_course_preview_state: Callable[[Path], tuple[list[QuestionLike], str]],
        get_runtime_preparation_state: Callable[[QuestionLike], dict[str, str]],
        start_runtime_question_preparation: Callable[[QuestionLike], None],
        is_runtime_question_ready: Callable[[QuestionLike], bool],
        build_runtime_proxy_path: Callable[..., str],
        render_preview_waiting_page: Callable[..., str],
    ) -> None:
        self._courses_dir = courses_dir
        self._load_course_preview_state = load_course_preview_state
        self._get_runtime_preparation_state = get_runtime_preparation_state
        self._start_runtime_question_preparation = start_runtime_question_preparation
        self._is_runtime_question_ready = is_runtime_question_ready
        self._build_runtime_proxy_path = build_runtime_proxy_path
        self._render_preview_waiting_page = render_preview_waiting_page

    def _resolve_question(self, course_slug: str, identifier: str) -> tuple[QuestionLike | None, str]:
        course_dir = self._courses_dir / course_slug
        if not course_dir.exists():
            return None, "Kurs nicht gefunden."

        questions, _ = self._load_course_preview_state(course_dir)
        question = next((item for item in questions if item.identifier == identifier), None)
        if question is None:
            return None, "PythonQuestion nicht gefunden."
        return question, ""

    def preview_status(self, course_slug: str, identifier: str) -> PreviewStatusResult:
        question, error_message = self._resolve_question(course_slug, identifier)
        if question is None:
            return PreviewStatusResult(status_code=HTTPStatus.NOT_FOUND, error_message=error_message)

        state = self._get_runtime_preparation_state(question)
        if state["status"] == "idle":
            self._start_runtime_question_preparation(question)
            state = self._get_runtime_preparation_state(question)
        return PreviewStatusResult(status_code=HTTPStatus.OK, payload=state)

    def preview_route(
        self,
        course_slug: str,
        identifier: str,
        *,
        mode: str,
        simple: bool,
    ) -> PreviewRenderResult:
        question, error_message = self._resolve_question(course_slug, identifier)
        if question is None:
            return PreviewRenderResult(status_code=HTTPStatus.NOT_FOUND, error_message=error_message)

        if self._is_runtime_question_ready(question):
            return PreviewRenderResult(
                status_code=HTTPStatus.FOUND,
                redirect_url=self._build_runtime_proxy_path(question, mode, simple=simple),
            )

        self._start_runtime_question_preparation(question)
        return PreviewRenderResult(
            status_code=HTTPStatus.OK,
            waiting_page_html=self._render_preview_waiting_page(question, mode=mode, simple=simple),
        )
