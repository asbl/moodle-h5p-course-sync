from __future__ import annotations

from pathlib import Path
from typing import Callable, Protocol

from scripts.classes.models import MoodleH5PActivity, PythonQuestionBlock, SyncMetadata, SyncMetadataEntry


class MoodleImportClient(Protocol):
    base_url: str

    def list_course_h5p_activities(self, course_id: int) -> list[MoodleH5PActivity]: ...

    def download_activity_question(self, course_slug: str, activity: MoodleH5PActivity) -> PythonQuestionBlock | None: ...


class MoodlePingClient(Protocol):
    base_url: str

    def get_site_info(self) -> dict[str, object]: ...


class MoodlePushClient(Protocol):
    base_url: str

    def ensure_course_push_supported(self) -> None: ...


class MoodleSyncer:
    """Coordinates syncing imported Moodle activities into local MDX courses."""

    def __init__(
        self,
        *,
        courses_dir: Path,
        ensure_directory: Callable[[Path], None],
        render_imported_question_mdx: Callable[[PythonQuestionBlock], list[str]],
        parse_course: Callable[[Path], tuple[str, list[PythonQuestionBlock], str]],
        compute_question_hash: Callable[[PythonQuestionBlock], str],
        save_sync_metadata: Callable[[Path, SyncMetadata], Path],
        escape_mdx_attribute: Callable[[str], str],
    ) -> None:
        self._courses_dir = courses_dir
        self._ensure_directory = ensure_directory
        self._render_imported_question_mdx = render_imported_question_mdx
        self._parse_course = parse_course
        self._compute_question_hash = compute_question_hash
        self._save_sync_metadata = save_sync_metadata
        self._escape_mdx_attribute = escape_mdx_attribute

    def _build_scaffold_question(self, course_slug: str, activity: MoodleH5PActivity) -> PythonQuestionBlock:
        return PythonQuestionBlock(
            identifier=activity.identifier,
            title=activity.title,
            instructions=activity.intro or f"Importiert aus Moodle: {activity.title}",
            preview_url=activity.url,
            package_url=getattr(activity, "package_url", ""),
            runner="pyodide",
            course_slug=course_slug,
            course_dir=self._courses_dir / course_slug,
        )

    def _ordered_activities(self, activities: list[MoodleH5PActivity]) -> list[MoodleH5PActivity]:
        return sorted(
            activities,
            key=lambda activity: (
                int(getattr(activity, "section_index", 0)),
                int(getattr(activity, "module_index", 0)),
                int(getattr(activity, "activity_id", 0)),
            ),
        )

    def render_imported_course_mdx(self, course_slug: str, activities: list[MoodleH5PActivity]) -> str:
        lines = [f"# {course_slug}", ""]
        current_section: str | None = None
        for activity in self._ordered_activities(activities):
            question = activity.imported_question or self._build_scaffold_question(course_slug, activity)
            if activity.section_title and activity.section_title != current_section:
                lines.extend([f"## {self._escape_mdx_attribute(activity.section_title)}", ""])
                current_section = activity.section_title
            lines.extend(self._render_imported_question_mdx(question))
        return "\n".join(line for line in lines if line != "") + "\n"

    def import_moodle_course(self, *, course: str, remote_course_id: int, client: MoodleImportClient) -> Path:
        course_dir = self._courses_dir / course
        self._ensure_directory(course_dir)
        self._ensure_directory(course_dir / "assets")

        activities = self._ordered_activities(client.list_course_h5p_activities(remote_course_id))

        for activity in activities:
            try:
                activity.imported_question = client.download_activity_question(course, activity)
            except RuntimeError:
                activity.imported_question = None

        mdx = self.render_imported_course_mdx(course, activities)
        (course_dir / "index.mdx").write_text(mdx, encoding="utf-8")

        _, questions, _ = self._parse_course(course_dir)
        question_by_identifier = {question.identifier: question for question in questions}
        metadata = SyncMetadata(
            course_slug=course,
            remote_course_id=remote_course_id,
            moodle_base_url=str(client.base_url),
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

    def push_moodle_course(
        self,
        *,
        course_dir: Path,
        remote_course_id: int,
        client: MoodlePushClient,
        sync_course: Callable[[Path], list[PythonQuestionBlock]],
    ) -> list[PythonQuestionBlock]:
        questions = sync_course(course_dir)
        if not questions:
            raise RuntimeError(f"Kurs '{course_dir.name}' enthaelt keine synchronisierbaren H5P-Aufgaben.")

        client.ensure_course_push_supported()
        raise RuntimeError(
            "Moodle-Push ist fuer diese Installation noch nicht ausfuehrbar. "
            f"Lokale Pakete fuer '{course_dir.name}' wurden erstellt, aber Moodle erlaubt keinen REST-Upload von mod_h5pactivity in Kurs {remote_course_id}."
        )

    @staticmethod
    def build_moodle_ping_report(client: MoodlePingClient) -> dict[str, object]:
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
            "supportsCoursePush": False,
            "pushBlockers": [
                "core_files_get_unused_draft_itemid und core_files_upload muessen freigegeben sein",
                "core_courseformat_new_module oder core_courseformat_create_module muessen freigegeben sein",
                "mod_h5pactivity bietet ohne zusaetzlichen Site-Webservice keinen REST-Create/Update-Pfad fuer packagefile",
            ],
        }
