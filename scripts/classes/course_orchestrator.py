from __future__ import annotations

from pathlib import Path
from threading import RLock
from typing import Callable

from .models import PythonQuestionBlock


class CourseOrchestrator:
    """Coordinates high-level course operations while delegating details to services."""

    def __init__(
        self,
        *,
        workspace_lock: RLock,
        courses_dir: Path,
        preview_cache: dict[str, tuple[int, list[PythonQuestionBlock], str]],
        parse_course: Callable[[Path], tuple[str, list[PythonQuestionBlock], str]],
        write_h5p_package: Callable[[PythonQuestionBlock], Path],
        render_course_page: Callable[[Path, list[PythonQuestionBlock], str], str],
    ) -> None:
        self._workspace_lock = workspace_lock
        self._courses_dir = courses_dir
        self._preview_cache = preview_cache
        self._parse_course = parse_course
        self._write_h5p_package = write_h5p_package
        self._render_course_page = render_course_page

    def sync_course(self, course_dir: Path) -> list[PythonQuestionBlock]:
        with self._workspace_lock:
            _, questions, _ = self._parse_course(course_dir)
            for question in questions:
                self._write_h5p_package(question)
            return questions

    def load_course_preview_state(self, course_dir: Path) -> tuple[list[PythonQuestionBlock], str]:
        mdx_mtime_ns = (course_dir / "index.mdx").stat().st_mtime_ns
        cached = self._preview_cache.get(course_dir.name)
        if cached and cached[0] == mdx_mtime_ns:
            return cached[1], cached[2]

        _, questions, rendered_source = self._parse_course(course_dir)
        html_content = self._render_course_page(course_dir, questions=questions, rendered_source=rendered_source)
        self._preview_cache[course_dir.name] = (mdx_mtime_ns, questions, html_content)
        return questions, html_content

    def find_question_by_runtime_content_id(self, runtime_content_id: str) -> PythonQuestionBlock | None:
        for course_dir in sorted(item for item in self._courses_dir.iterdir() if item.is_dir()):
            questions, _ = self.load_course_preview_state(course_dir)
            for question in questions:
                if question.runtime_content_id == runtime_content_id:
                    return question
        return None
