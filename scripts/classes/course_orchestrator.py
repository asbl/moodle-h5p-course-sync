from __future__ import annotations

from pathlib import Path
from threading import RLock
from typing import Callable

from .models import PythonQuestionBlock, SyncMetadata


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
        load_sync_metadata: Callable[[Path], SyncMetadata | None],
        compute_question_hash: Callable[[PythonQuestionBlock], str],
    ) -> None:
        self._workspace_lock = workspace_lock
        self._courses_dir = courses_dir
        self._preview_cache = preview_cache
        self._parse_course = parse_course
        self._write_h5p_package = write_h5p_package
        self._render_course_page = render_course_page
        self._load_sync_metadata = load_sync_metadata
        self._compute_question_hash = compute_question_hash

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

    def build_course_status(self, course_dir: Path) -> dict[str, object]:
        metadata = self._load_sync_metadata(course_dir)
        if metadata is None:
            raise FileNotFoundError(f"Keine Sync-Metadaten fuer {course_dir.name} gefunden.")

        _, questions, _ = self._parse_course(course_dir)
        local_questions = {question.identifier: question for question in questions}
        items: list[dict[str, object]] = []
        counts = {"tracked": 0, "modified-local": 0, "local-only": 0, "remote-only": 0}

        for identifier, question in sorted(local_questions.items()):
            entry = metadata.entries.get(identifier)
            if entry is None:
                status = "local-only"
            elif self._compute_question_hash(question) != entry.local_hash:
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
