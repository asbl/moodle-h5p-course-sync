from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Callable
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zipfile import BadZipFile, ZipFile

from scripts.classes.models import MoodleH5PActivity, PythonQuestionBlock


class MoodleApiClient:
    """Moodle webservice client with pluggable import dependencies."""

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        make_stable_identifier: Callable[[str, set[str]], str],
        strip_html: Callable[[str], str],
        fetch_text: Callable[[str], str],
        extract_h5p_package_url_from_activity_html: Callable[[str], str],
        download_file: Callable[[str, Path], None],
        extract_h5p_package_from_course_backup: Callable[[str, MoodleH5PActivity, Path], bool],
        build_imported_question_from_h5p_package: Callable[[str, MoodleH5PActivity, dict[str, object], dict[str, object]], PythonQuestionBlock | None],
        write_source_package_sidecar: Callable[[PythonQuestionBlock, Path], str],
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token.strip()
        if not self.base_url:
            raise ValueError("Moodle-Basis-URL fehlt.")
        if not self.token:
            raise ValueError("Moodle-Token fehlt.")

        self._make_stable_identifier = make_stable_identifier
        self._strip_html = strip_html
        self._fetch_text = fetch_text
        self._extract_h5p_package_url_from_activity_html = extract_h5p_package_url_from_activity_html
        self._download_file = download_file
        self._extract_h5p_package_from_course_backup = extract_h5p_package_from_course_backup
        self._build_imported_question_from_h5p_package = build_imported_question_from_h5p_package
        self._write_source_package_sidecar = write_source_package_sidecar

    def call(self, function_name: str, **params: object) -> object:
        query = {
            "wstoken": self.token,
            "wsfunction": function_name,
            "moodlewsrestformat": "json",
        }
        query.update({key: value for key, value in params.items() if value is not None})
        url = f"{self.base_url}/webservice/rest/server.php?{urlencode(query, doseq=True)}"
        request = Request(url, headers={"User-Agent": "course-sync"})
        with urlopen(request) as response:
            payload = json.loads(response.read().decode("utf-8"))

        if isinstance(payload, dict) and payload.get("exception"):
            raise RuntimeError(f"Moodle-API-Fehler: {payload.get('message', payload['exception'])}")

        return payload

    def list_course_h5p_activities(self, course_id: int) -> list[MoodleH5PActivity]:
        payload = self.call("core_course_get_contents", courseid=course_id)
        if not isinstance(payload, list):
            raise RuntimeError("Unerwartete Moodle-Antwort für core_course_get_contents.")

        identifiers: set[str] = set()
        activities: list[MoodleH5PActivity] = []
        for section in payload:
            if not isinstance(section, dict):
                continue
            section_title = str(section.get("name") or section.get("section") or "").strip()
            modules = section.get("modules", [])
            if not isinstance(modules, list):
                continue
            for module in modules:
                if not isinstance(module, dict):
                    continue
                if module.get("modname") != "h5pactivity":
                    continue
                title = str(module.get("name") or f"h5p-{module.get('id', 'unknown')}").strip()
                identifier = self._make_stable_identifier(title, identifiers)
                activities.append(
                    MoodleH5PActivity(
                        identifier=identifier,
                        title=title,
                        course_id=course_id,
                        activity_id=int(module["id"]),
                        instance_id=int(module["instance"]) if module.get("instance") is not None else None,
                        section_title=section_title,
                        intro=self._strip_html(str(module.get("description") or "")),
                        url=str(module.get("url") or ""),
                        visible=bool(module.get("visible", True)),
                    )
                )
        return activities

    def get_site_info(self) -> dict[str, object]:
        payload = self.call("core_webservice_get_site_info")
        if not isinstance(payload, dict):
            raise RuntimeError("Unerwartete Moodle-Antwort für core_webservice_get_site_info.")
        return payload

    def download_activity_question(self, course_slug: str, activity: MoodleH5PActivity) -> PythonQuestionBlock | None:
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                archive_path = Path(temp_dir) / f"{activity.identifier}.h5p"
                package_url = ""
                if activity.url:
                    page_html = self._fetch_text(activity.url)
                    package_url = self._extract_h5p_package_url_from_activity_html(page_html)
                if package_url:
                    activity.package_url = package_url
                    self._download_file(package_url, archive_path)
                else:
                    if not self._extract_h5p_package_from_course_backup(self.base_url, activity, archive_path):
                        return None

                with ZipFile(archive_path) as archive:
                    metadata_payload = json.loads(archive.read("h5p.json").decode("utf-8"))
                    content_payload = json.loads(archive.read("content/content.json").decode("utf-8"))
                if not isinstance(metadata_payload, dict) or not isinstance(content_payload, dict):
                    return None

                question = self._build_imported_question_from_h5p_package(
                    course_slug,
                    activity,
                    metadata_payload,
                    content_payload,
                )
                if question is not None:
                    question.source_package_path = self._write_source_package_sidecar(question, archive_path)
                return question
        except (BadZipFile, OSError, KeyError, json.JSONDecodeError):
            return None
