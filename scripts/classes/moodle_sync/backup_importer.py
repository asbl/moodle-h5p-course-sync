"""Import a Moodle course from a local .mbz backup file without API credentials."""
from __future__ import annotations

import json
import tarfile
import tempfile
from pathlib import Path
from typing import Callable
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

from scripts.classes.models import MoodleH5PActivity, PythonQuestionBlock
from .backup_extractor import MoodleBackupExtractor


class MoodleBackupImporter:
    """Implements the MoodleImportClient protocol using a local .mbz file.

    Parses the MBZ course structure and extracts H5P packages without requiring
    Moodle API credentials.
    """

    def __init__(
        self,
        *,
        mbz_path: Path,
        base_url: str,
        backup_extractor: MoodleBackupExtractor,
        make_stable_identifier: Callable[[str, set[str]], str],
        strip_html: Callable[[str], str],
        build_imported_question_from_h5p_package: Callable[
            [str, MoodleH5PActivity, dict[str, object], dict[str, object]],
            PythonQuestionBlock | None,
        ],
        write_source_package_sidecar: Callable[[PythonQuestionBlock, Path], str],
    ) -> None:
        self._mbz_path = mbz_path
        self.base_url = base_url
        self._backup_extractor = backup_extractor
        self._make_stable_identifier = make_stable_identifier
        self._strip_html = strip_html
        self._build_imported_question_from_h5p_package = build_imported_question_from_h5p_package
        self._write_source_package_sidecar = write_source_package_sidecar
        self._activity_directories: dict[int, str] = {}

    def _parse_backup_xml(self) -> tuple[list[dict[str, object]], dict[int, str]]:
        """Parse moodle_backup.xml and return (h5p_activity_list, section_dir_by_id)."""
        with tarfile.open(self._mbz_path, "r:gz") as archive:
            backup_member = archive.extractfile("moodle_backup.xml")
            if backup_member is None:
                return [], {}
            root = ElementTree.fromstring(backup_member.read())

        h5p_activities: list[dict[str, object]] = []
        for activity_node in root.findall(".//activity"):
            modulename = (activity_node.findtext("modulename") or "").strip()
            if modulename != "h5pactivity":
                continue
            h5p_activities.append({
                "moduleid": int(activity_node.findtext("moduleid") or 0),
                "sectionid": int(activity_node.findtext("sectionid") or 0),
                "title": (activity_node.findtext("title") or "").strip(),
                "directory": (activity_node.findtext("directory") or "").strip(),
            })

        section_dirs: dict[int, str] = {}
        for section_node in root.findall(".//section"):
            sectionid_text = (section_node.findtext("sectionid") or "").strip()
            directory = (section_node.findtext("directory") or "").strip()
            if sectionid_text.isdigit() and directory:
                section_dirs[int(sectionid_text)] = directory

        return h5p_activities, section_dirs

    def _parse_section_xml(self, directory: str) -> dict[str, object]:
        """Parse sections/<dir>/section.xml and return section metadata."""
        try:
            with tarfile.open(self._mbz_path, "r:gz") as archive:
                member = archive.extractfile(f"{directory}/section.xml")
                if member is None:
                    return {}
                root = ElementTree.fromstring(member.read())
        except (KeyError, Exception):
            return {}

        name = (root.findtext("name") or "").strip()
        if name in ("$@NULL@$", ""):
            name = ""
        number_text = (root.findtext("number") or "0").strip()
        sequence_str = root.findtext("sequence") or ""
        sequence = [
            int(s.strip())
            for s in sequence_str.split(",")
            if s.strip().lstrip("-").isdigit() and int(s.strip()) > 0
        ]
        return {
            "name": name,
            "number": int(number_text) if number_text.isdigit() else 0,
            "sequence": sequence,
        }

    def list_course_h5p_activities(self, course_id: int) -> list[MoodleH5PActivity]:
        h5p_activities_raw, section_dirs = self._parse_backup_xml()

        section_details: dict[int, dict[str, object]] = {}
        for sectionid, directory in section_dirs.items():
            section_details[sectionid] = self._parse_section_xml(directory)

        module_position: dict[int, int] = {}
        for details in section_details.values():
            for idx, moduleid in enumerate(details.get("sequence", [])):
                module_position[int(moduleid)] = idx

        self._activity_directories.clear()
        identifiers: set[str] = set()
        activities: list[MoodleH5PActivity] = []

        for raw in h5p_activities_raw:
            moduleid = int(raw["moduleid"])
            sectionid = int(raw["sectionid"])
            title = str(raw["title"])
            directory = str(raw["directory"])

            self._activity_directories[moduleid] = directory

            section = section_details.get(sectionid, {})
            section_name = str(section.get("name") or "")
            if not section_name:
                section_name = f"Abschnitt {sectionid}"

            identifier = self._make_stable_identifier(title, identifiers)

            activities.append(MoodleH5PActivity(
                identifier=identifier,
                title=title,
                course_id=course_id,
                activity_id=moduleid,
                instance_id=None,
                section_title=section_name,
                section_index=int(section.get("number", 0)),
                module_index=module_position.get(moduleid, 0),
                intro="",
                url="",
                visible=True,
            ))

        return activities

    def download_activity_question(
        self,
        course_slug: str,
        activity: MoodleH5PActivity,
    ) -> PythonQuestionBlock | None:
        activity_dir = self._activity_directories.get(activity.activity_id, "")
        if not activity_dir:
            activity_dir = self._backup_extractor.parse_backup_activity_directory(
                self._mbz_path, activity
            )
        if not activity_dir:
            return None

        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                archive_path = Path(temp_dir) / f"{activity.identifier}.h5p"
                if not self._backup_extractor.extract_h5p_package_from_backup_activity(
                    self._mbz_path, activity_dir, archive_path
                ):
                    return None

                with ZipFile(archive_path) as zf:
                    metadata_payload = json.loads(zf.read("h5p.json").decode("utf-8"))
                    content_payload = json.loads(zf.read("content/content.json").decode("utf-8"))

                if not isinstance(metadata_payload, dict) or not isinstance(content_payload, dict):
                    return None

                question = self._build_imported_question_from_h5p_package(
                    course_slug,
                    activity,
                    metadata_payload,
                    content_payload,
                )
                if question is not None:
                    question.source_package_path = self._write_source_package_sidecar(
                        question, archive_path
                    )
                return question
        except (BadZipFile, OSError, KeyError, json.JSONDecodeError):
            return None
