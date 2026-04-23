from __future__ import annotations

import html
import shutil
import tarfile
import tempfile
from pathlib import Path
from re import Pattern
from urllib.parse import unquote
from xml.etree import ElementTree

from scripts.classes.models import MoodleH5PActivity


class MoodleBackupExtractor:
    """Extracts H5P package archives from Moodle course backups."""

    def __init__(
        self,
        *,
        mbz_link_re: Pattern[str],
        fetch_text,
        download_file,
        ensure_directory,
    ) -> None:
        self._mbz_link_re = mbz_link_re
        self._fetch_text = fetch_text
        self._download_file = download_file
        self._ensure_directory = ensure_directory

    def discover_course_backup_url(self, base_url: str, course_id: int, activity_url: str = "") -> str:
        search_urls = []
        if activity_url:
            search_urls.append(activity_url)
        search_urls.append(f"{base_url.rstrip('/')}/course/view.php?id={course_id}")

        for url in search_urls:
            try:
                page_html = self._fetch_text(url)
            except OSError:
                continue
            match = self._mbz_link_re.search(page_html)
            if match:
                return html.unescape(match.group(0))
        return ""

    def parse_backup_activity_directory(self, backup_path: Path, activity: MoodleH5PActivity) -> str:
        with tarfile.open(backup_path, "r:gz") as archive:
            backup_xml = archive.extractfile("moodle_backup.xml")
            if backup_xml is None:
                return ""
            root = ElementTree.fromstring(backup_xml.read())

        for activity_node in root.findall(".//activity"):
            title = (activity_node.findtext("title") or "").strip()
            modulename = (activity_node.findtext("modulename") or "").strip()
            directory = (activity_node.findtext("directory") or "").strip()
            if modulename != "h5pactivity":
                continue
            if title == activity.title and directory:
                return directory
        return ""

    def extract_backup_file_records(self, backup_path: Path, file_ids: set[str]) -> dict[str, dict[str, str]]:
        if not file_ids:
            return {}

        with tarfile.open(backup_path, "r:gz") as archive:
            files_xml = archive.extractfile("files.xml")
            if files_xml is None:
                return {}
            root = ElementTree.fromstring(files_xml.read())

        records: dict[str, dict[str, str]] = {}
        for file_node in root.findall(".//file"):
            file_id = file_node.get("id") or ""
            if file_id not in file_ids:
                continue
            records[file_id] = {
                "contenthash": (file_node.findtext("contenthash") or "").strip(),
                "filename": (file_node.findtext("filename") or "").strip(),
                "component": (file_node.findtext("component") or "").strip(),
                "filearea": (file_node.findtext("filearea") or "").strip(),
            }
        return records

    def extract_h5p_package_from_backup_activity(self, backup_path: Path, activity_dir: str, destination: Path) -> bool:
        with tarfile.open(backup_path, "r:gz") as archive:
            inforef_member = archive.extractfile(f"{activity_dir}/inforef.xml")
            if inforef_member is None:
                return False
            inforef_root = ElementTree.fromstring(inforef_member.read())
            file_ids = {
                (file_node.findtext("id") or "").strip()
                for file_node in inforef_root.findall(".//fileref/file")
                if (file_node.findtext("id") or "").strip()
            }

        file_records = self.extract_backup_file_records(backup_path, file_ids)
        package_record = next(
            (
                record
                for record in file_records.values()
                if record.get("component") == "mod_h5pactivity"
                and record.get("filearea") == "package"
                and record.get("filename") not in {"", "."}
                and record.get("contenthash")
            ),
            None,
        )
        if package_record is None:
            return False

        content_hash = package_record["contenthash"]
        with tarfile.open(backup_path, "r:gz") as archive:
            source_member = None
            for tar_member_name in [
                f"files/{content_hash[:2]}/{content_hash[2:4]}/{content_hash}",
                f"files/{content_hash[:2]}/{content_hash}",
            ]:
                try:
                    source_member = archive.extractfile(tar_member_name)
                except KeyError:
                    source_member = None
                if source_member is not None:
                    break
            if source_member is None:
                return False
            self._ensure_directory(destination.parent)
            with destination.open("wb") as target:
                shutil.copyfileobj(source_member, target)
        return True

    def extract_h5p_package_from_course_backup(self, base_url: str, activity: MoodleH5PActivity, destination: Path) -> bool:
        backup_url = self.discover_course_backup_url(base_url, activity.course_id, activity.url)
        if not backup_url:
            return False

        with tempfile.TemporaryDirectory() as temp_dir:
            backup_path = Path(temp_dir) / f"course-{activity.course_id}.mbz"
            self._download_file(backup_url, backup_path)
            activity_dir = self.parse_backup_activity_directory(backup_path, activity)
            if not activity_dir:
                return False
            return self.extract_h5p_package_from_backup_activity(backup_path, activity_dir, destination)
