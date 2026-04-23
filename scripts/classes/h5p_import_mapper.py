from __future__ import annotations

import html
from typing import Callable

from scripts.classes.models import MoodleH5PActivity, SourceFile


class H5PImportMapper:
    """Maps imported H5P payloads to normalized course-sync structures."""

    def __init__(self, *, compact_text: Callable[[str], str], normalize_whitespace: Callable[[str], str]) -> None:
        self._compact_text = compact_text
        self._normalize_whitespace = normalize_whitespace

    def extract_packages(self, content_payload: dict[str, object]) -> list[str]:
        packages: list[str] = []
        pyodide_options = content_payload.get("pyodideOptions", {})
        if not isinstance(pyodide_options, dict):
            return packages

        for entry in pyodide_options.get("packages", []) or []:
            if isinstance(entry, dict):
                package_name = str(entry.get("package") or entry.get("name") or "").strip()
            else:
                package_name = str(entry).strip()
            if package_name and package_name not in packages:
                packages.append(package_name)
        return packages

    def summarize_instructions(self, activity: MoodleH5PActivity, content_payload: dict[str, object]) -> str:
        editor_settings = content_payload.get("editorSettings", {})
        if isinstance(editor_settings, dict):
            editor_instructions = self._compact_text(str(editor_settings.get("instructions") or ""))
            if editor_instructions:
                return editor_instructions

        content_fragments: list[str] = []
        for entry in content_payload.get("contents", []) or []:
            if not isinstance(entry, dict):
                continue
            text = self._compact_text(str(entry.get("text") or ""))
            if text:
                content_fragments.append(text)

        if content_fragments:
            return " ".join(content_fragments)
        if activity.intro:
            return self._compact_text(activity.intro)
        return f"Importiert aus Moodle: {activity.title}"

    def extract_editor_instructions(self, content_payload: dict[str, object]) -> str:
        editor_settings = content_payload.get("editorSettings", {})
        if not isinstance(editor_settings, dict):
            return ""
        raw_instructions = html.unescape(str(editor_settings.get("instructions") or ""))
        return self._normalize_whitespace(raw_instructions)

    def extract_test_case_values(self, raw_values: object, *, field_name: str) -> list[str]:
        if not isinstance(raw_values, list):
            return []

        values: list[str] = []
        for entry in raw_values:
            if isinstance(entry, dict):
                raw_value = entry.get(field_name)
                if raw_value is None:
                    continue
                values.append(str(raw_value))
                continue
            values.append(str(entry))
        return values

    def extract_source_files(self, editor_options: dict[str, object]) -> list[SourceFile]:
        source_files: list[SourceFile] = []
        raw_files = editor_options.get("sourceFiles", [])
        if not isinstance(raw_files, list):
            return source_files

        for index, entry in enumerate(raw_files, start=1):
            if not isinstance(entry, dict):
                continue

            code = self._normalize_whitespace(html.unescape(str(entry.get("code") or "")))
            file_name = str(entry.get("fileName") or "").strip()
            if not file_name:
                if not code:
                    continue
                file_name = f"source-{index}.py"

            source_files.append(
                SourceFile(
                    file_name=file_name,
                    code=code,
                    visible_to_learner=bool(entry.get("visibleToLearner", True)),
                    learner_editable=bool(entry.get("learnerEditable", True)),
                )
            )

        return source_files
