from __future__ import annotations

from pathlib import Path
from typing import Callable

from scripts.classes.models import MoodleH5PActivity, PythonQuestionBlock, SyncMetadata, SyncMetadataEntry


class MoodleSyncer:
    """Coordinates syncing imported Moodle activities into local MDX courses."""

    def __init__(
        self,
        *,
        courses_dir: Path,
        ensure_directory: Callable[[Path], None],
        remove_legacy_h5p_json_sidecars: Callable[[Path], None],
        render_imported_question_mdx: Callable[[PythonQuestionBlock], list[str]],
        build_scaffold_question: Callable[[str, MoodleH5PActivity], PythonQuestionBlock],
        parse_course: Callable[[Path], tuple[str, list[PythonQuestionBlock], str]],
        compute_question_hash: Callable[[PythonQuestionBlock], str],
        save_sync_metadata: Callable[[Path, SyncMetadata], Path],
        escape_mdx_attribute: Callable[[str], str],
    ) -> None:
        self._courses_dir = courses_dir
        self._ensure_directory = ensure_directory
        self._remove_legacy_h5p_json_sidecars = remove_legacy_h5p_json_sidecars
        self._render_imported_question_mdx = render_imported_question_mdx
        self._build_scaffold_question = build_scaffold_question
        self._parse_course = parse_course
        self._compute_question_hash = compute_question_hash
        self._save_sync_metadata = save_sync_metadata
        self._escape_mdx_attribute = escape_mdx_attribute

    def render_imported_course_mdx(self, course_slug: str, activities: list[MoodleH5PActivity]) -> str:
        lines = [f"# {course_slug}", ""]
        current_section: str | None = None
        for activity in activities:
            question = getattr(activity, "imported_question", None) or self._build_scaffold_question(course_slug, activity)
            if activity.section_title and activity.section_title != current_section:
                lines.extend([f"## {self._escape_mdx_attribute(activity.section_title)}", ""])
                current_section = activity.section_title
            lines.extend(self._render_imported_question_mdx(question))
        return "\n".join(line for line in lines if line != "") + "\n"

    def import_moodle_course(self, *, course: str, remote_course_id: int, client: object) -> Path:
        course_dir = self._courses_dir / course
        self._ensure_directory(course_dir)
        self._ensure_directory(course_dir / "assets")

        list_course_h5p_activities = getattr(client, "list_course_h5p_activities")
        activities = list_course_h5p_activities(remote_course_id)

        download_activity_question = getattr(client, "download_activity_question", None)
        if callable(download_activity_question):
            for activity in activities:
                try:
                    activity.imported_question = download_activity_question(course, activity)
                except RuntimeError:
                    activity.imported_question = None

        self._remove_legacy_h5p_json_sidecars(course_dir)
        mdx = self.render_imported_course_mdx(course, activities)
        (course_dir / "index.mdx").write_text(mdx, encoding="utf-8")

        _, questions, _ = self._parse_course(course_dir)
        question_by_identifier = {question.identifier: question for question in questions}
        metadata = SyncMetadata(
            course_slug=course,
            remote_course_id=remote_course_id,
            moodle_base_url=str(getattr(client, "base_url")),
        )
        for activity in activities:
            question = question_by_identifier[activity.identifier]
            metadata.entries[activity.identifier] = SyncMetadataEntry(
                identifier=activity.identifier,
                remote_activity_id=activity.activity_id,
                remote_instance_id=activity.instance_id,
                remote_title=activity.title,
                remote_url=activity.url,
                remote_visible=activity.visible,
                local_hash=self._compute_question_hash(question),
                status="imported",
            )

        self._save_sync_metadata(course_dir, metadata)
        return course_dir
