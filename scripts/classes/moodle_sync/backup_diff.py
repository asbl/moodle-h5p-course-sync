from __future__ import annotations

import json
import tarfile
from dataclasses import dataclass, field
from hashlib import sha1
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile


@dataclass(slots=True)
class MoodleBackupSectionSnapshot:
    section_id: int
    number: int
    title: str
    sequence: list[int] = field(default_factory=list)

    @property
    def key(self) -> str:
        return str(self.section_id)

    def signature(self) -> tuple[object, ...]:
        return (self.number, self.title, tuple(self.sequence))


@dataclass(slots=True)
class MoodleBackupActivitySnapshot:
    module_id: int
    title: str
    module_name: str
    section_id: int
    section_number: int
    section_title: str
    directory: str
    package_hash: str = ""
    package_filename: str = ""
    main_library: str = ""
    package_valid: bool = False
    runtime_message: str = ""

    @property
    def key(self) -> str:
        return f"{self.module_name}:{self.module_id}"

    def signature(self) -> tuple[object, ...]:
        return (
            self.title,
            self.module_name,
            self.section_number,
            self.section_title,
            self.package_hash,
            self.package_filename,
            self.main_library,
            self.package_valid,
        )


@dataclass(slots=True)
class MoodleBackupCourseSnapshot:
    path: Path
    sections: dict[str, MoodleBackupSectionSnapshot]
    activities: dict[str, MoodleBackupActivitySnapshot]


class MoodleBackupDiffAnalyzer:
    """Builds a human-readable diff from two Moodle .mbz course backups."""

    def load_snapshot(self, backup_path: Path) -> MoodleBackupCourseSnapshot:
        with tarfile.open(backup_path, "r:gz") as archive:
            backup_member = archive.extractfile("moodle_backup.xml")
            if backup_member is None:
                raise RuntimeError(f"MBZ enthaelt kein moodle_backup.xml: {backup_path}")
            root = ElementTree.fromstring(backup_member.read())
            file_records = self._parse_file_records(archive)

            section_dirs = self._parse_section_dirs(root)
            sections_by_id = {
                section_id: self._parse_section_snapshot(archive, section_id, directory)
                for section_id, directory in section_dirs.items()
            }

            activities: dict[str, MoodleBackupActivitySnapshot] = {}
            for activity_node in root.findall(".//activity"):
                module_name = (activity_node.findtext("modulename") or "").strip()
                module_id = self._read_int(activity_node.findtext("moduleid"), 0)
                if not module_name or module_id <= 0:
                    continue
                section_id = self._read_int(activity_node.findtext("sectionid"), 0)
                section = sections_by_id.get(section_id)
                activity = MoodleBackupActivitySnapshot(
                    module_id=module_id,
                    title=(activity_node.findtext("title") or "").strip(),
                    module_name=module_name,
                    section_id=section_id,
                    section_number=section.number if section else 0,
                    section_title=section.title if section else "",
                    directory=(activity_node.findtext("directory") or "").strip(),
                )
                self._attach_package_details(archive, activity, file_records)
                activities[activity.key] = activity

        sections = {section.key: section for section in sections_by_id.values()}
        return MoodleBackupCourseSnapshot(path=backup_path, sections=sections, activities=activities)

    def analyze(self, before_path: Path, after_path: Path) -> dict[str, object]:
        before = self.load_snapshot(before_path)
        after = self.load_snapshot(after_path)

        section_changes = self._diff_named_items(before.sections, after.sections)
        activity_changes = self._diff_named_items(before.activities, after.activities)
        runtime_checks = self._sample_runtime_checks(after)

        return {
            "before": str(before.path),
            "after": str(after.path),
            "sections": section_changes,
            "activities": activity_changes,
            "runtimeChecks": runtime_checks,
        }

    def format_report(self, analysis: dict[str, object]) -> str:
        lines = [
            "MBZ-Sync-Analyse",
            f"Vorher: {analysis['before']}",
            f"Nachher: {analysis['after']}",
            "",
        ]
        for label, key in [("Sektionen", "sections"), ("Aktivitaeten", "activities")]:
            changes = analysis[key]
            assert isinstance(changes, dict)
            lines.append(f"{label}:")
            for change_key, title in [
                ("created", "Neu erstellt"),
                ("changed", "Veraendert"),
                ("deleted", "Geloescht"),
                ("unchanged", "Unveraendert"),
            ]:
                items = changes.get(change_key, [])
                if not isinstance(items, list) or not items:
                    lines.append(f"- {title}: keine")
                    continue
                lines.append(f"- {title}:")
                for item in items:
                    if isinstance(item, dict):
                        lines.append(f"  - {self._format_change_item(item)}")
            lines.append("")

        lines.append("Stichproben-Laufcheck je Aktivitaetstyp:")
        runtime_checks = analysis.get("runtimeChecks", [])
        if not isinstance(runtime_checks, list) or not runtime_checks:
            lines.append("- keine Aktivitaeten gefunden")
        else:
            for item in runtime_checks:
                if not isinstance(item, dict):
                    continue
                status = "ok" if item.get("ok") else "fehler"
                lines.append(
                    f"- {item.get('type', 'unbekannt')}: {status} - "
                    f"{item.get('title', '')} ({item.get('message', '')})"
                )
        return "\n".join(lines)

    def _diff_named_items(self, before: dict[str, object], after: dict[str, object]) -> dict[str, list[dict[str, object]]]:
        before_keys = set(before)
        after_keys = set(after)
        created = [self._item_to_dict(after[key]) for key in sorted(after_keys - before_keys)]
        deleted = [self._item_to_dict(before[key]) for key in sorted(before_keys - after_keys)]
        changed = []
        unchanged = []
        for key in sorted(before_keys & after_keys):
            before_item = before[key]
            after_item = after[key]
            if before_item.signature() == after_item.signature():  # type: ignore[attr-defined]
                unchanged.append(self._item_to_dict(after_item))
            else:
                changed.append(
                    {
                        "key": key,
                        "before": self._item_to_dict(before_item),
                        "after": self._item_to_dict(after_item),
                    }
                )
        return {
            "created": created,
            "changed": changed,
            "deleted": deleted,
            "unchanged": unchanged,
        }

    def _sample_runtime_checks(self, snapshot: MoodleBackupCourseSnapshot) -> list[dict[str, object]]:
        samples: dict[str, MoodleBackupActivitySnapshot] = {}
        for activity in sorted(snapshot.activities.values(), key=lambda item: (item.main_library, item.module_id)):
            activity_type = activity.main_library or activity.module_name
            if activity_type not in samples:
                samples[activity_type] = activity
        return [
            {
                "type": activity_type,
                "moduleId": activity.module_id,
                "title": activity.title,
                "ok": activity.package_valid,
                "message": activity.runtime_message,
            }
            for activity_type, activity in sorted(samples.items())
        ]

    def _attach_package_details(
        self,
        archive: tarfile.TarFile,
        activity: MoodleBackupActivitySnapshot,
        file_records: dict[str, dict[str, str]],
    ) -> None:
        if not activity.directory:
            activity.runtime_message = "kein Aktivitaetsverzeichnis im Backup"
            return
        try:
            inforef_member = archive.extractfile(f"{activity.directory}/inforef.xml")
        except KeyError:
            inforef_member = None
        if inforef_member is None:
            activity.runtime_message = "kein inforef.xml gefunden"
            return

        inforef_root = ElementTree.fromstring(inforef_member.read())
        file_ids = [
            (file_node.findtext("id") or "").strip()
            for file_node in inforef_root.findall(".//fileref/file")
            if (file_node.findtext("id") or "").strip()
        ]
        package_record = next(
            (
                file_records[file_id]
                for file_id in file_ids
                if file_id in file_records
                and file_records[file_id].get("component") == "mod_h5pactivity"
                and file_records[file_id].get("filearea") == "package"
                and file_records[file_id].get("filename") not in {"", "."}
            ),
            None,
        )
        if package_record is None:
            activity.runtime_message = "kein H5P-Paket referenziert"
            return

        content_hash = package_record.get("contenthash", "")
        activity.package_filename = package_record.get("filename", "")
        package_bytes = self._read_hashed_file(archive, content_hash)
        if package_bytes is None:
            activity.runtime_message = "H5P-Paketdatei fehlt im files/-Bereich"
            return

        activity.package_hash = sha1(package_bytes).hexdigest()
        try:
            with ZipFile(BytesIO(package_bytes)) as package:
                metadata = json.loads(package.read("h5p.json").decode("utf-8"))
                package.read("content/content.json")
        except (BadZipFile, KeyError, OSError, json.JSONDecodeError, UnicodeDecodeError):
            activity.runtime_message = "H5P-Paket laesst sich nicht laden"
            return
        if not isinstance(metadata, dict):
            activity.runtime_message = "h5p.json ist kein Objekt"
            return
        activity.main_library = str(metadata.get("mainLibrary") or "").strip()
        activity.package_valid = bool(activity.main_library)
        activity.runtime_message = "h5p.json und content/content.json lesbar" if activity.package_valid else "mainLibrary fehlt"

    def _read_hashed_file(self, archive: tarfile.TarFile, content_hash: str) -> bytes | None:
        if not content_hash:
            return None
        for member_name in [
            f"files/{content_hash[:2]}/{content_hash[2:4]}/{content_hash}",
            f"files/{content_hash[:2]}/{content_hash}",
        ]:
            try:
                member = archive.extractfile(member_name)
            except KeyError:
                member = None
            if member is not None:
                return member.read()
        return None

    def _parse_file_records(self, archive: tarfile.TarFile) -> dict[str, dict[str, str]]:
        try:
            files_member = archive.extractfile("files.xml")
        except KeyError:
            files_member = None
        if files_member is None:
            return {}
        root = ElementTree.fromstring(files_member.read())
        records: dict[str, dict[str, str]] = {}
        for file_node in root.findall(".//file"):
            file_id = file_node.get("id") or ""
            if not file_id:
                continue
            records[file_id] = {
                "contenthash": (file_node.findtext("contenthash") or "").strip(),
                "filename": (file_node.findtext("filename") or "").strip(),
                "component": (file_node.findtext("component") or "").strip(),
                "filearea": (file_node.findtext("filearea") or "").strip(),
            }
        return records

    def _parse_section_dirs(self, root: ElementTree.Element) -> dict[int, str]:
        section_dirs: dict[int, str] = {}
        for section_node in root.findall(".//section"):
            section_id = self._read_int(section_node.findtext("sectionid"), 0)
            directory = (section_node.findtext("directory") or "").strip()
            if section_id > 0 and directory:
                section_dirs[section_id] = directory
        return section_dirs

    def _parse_section_snapshot(
        self,
        archive: tarfile.TarFile,
        section_id: int,
        directory: str,
    ) -> MoodleBackupSectionSnapshot:
        try:
            section_member = archive.extractfile(f"{directory}/section.xml")
        except KeyError:
            section_member = None
        if section_member is None:
            return MoodleBackupSectionSnapshot(section_id=section_id, number=0, title=f"Abschnitt {section_id}")
        root = ElementTree.fromstring(section_member.read())
        title = (root.findtext("name") or "").strip()
        if title in {"", "$@NULL@$"}:
            title = f"Abschnitt {section_id}"
        sequence = [
            self._read_int(part, 0)
            for part in (root.findtext("sequence") or "").split(",")
            if self._read_int(part, 0) > 0
        ]
        return MoodleBackupSectionSnapshot(
            section_id=section_id,
            number=self._read_int(root.findtext("number"), 0),
            title=title,
            sequence=sequence,
        )

    def _item_to_dict(self, item: object) -> dict[str, object]:
        if isinstance(item, MoodleBackupSectionSnapshot):
            return {
                "key": item.key,
                "sectionId": item.section_id,
                "number": item.number,
                "title": item.title,
                "sequence": item.sequence,
            }
        if isinstance(item, MoodleBackupActivitySnapshot):
            return {
                "key": item.key,
                "moduleId": item.module_id,
                "title": item.title,
                "moduleName": item.module_name,
                "section": item.section_title,
                "sectionNumber": item.section_number,
                "packageHash": item.package_hash,
                "packageFilename": item.package_filename,
                "mainLibrary": item.main_library,
                "packageValid": item.package_valid,
            }
        return {"value": str(item)}

    def _format_change_item(self, item: dict[str, object]) -> str:
        if "before" in item and "after" in item:
            before = item["before"]
            after = item["after"]
            if isinstance(before, dict) and isinstance(after, dict):
                return f"{after.get('title', item.get('key'))} [{item.get('key')}] vorher={before} nachher={after}"
        return f"{item.get('title', item.get('key', item))} [{item.get('key', '')}]"

    def _read_int(self, value: object, fallback: int) -> int:
        try:
            return int(str(value or "").strip())
        except (TypeError, ValueError):
            return fallback
