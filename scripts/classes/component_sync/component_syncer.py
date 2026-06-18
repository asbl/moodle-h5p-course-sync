from __future__ import annotations

import json
import textwrap
from typing import Callable

from scripts.classes.models import PythonQuestionBlock
from scripts.classes.python_runner_policy import ensure_miniworlds_packages, packages_for_h5p_content
from scripts.classes.content_types import PythonQuestion as PythonQuestionContentType
from scripts.classes.content_types import block_to_content_type
from scripts.classes.content_types._helpers import (
    clone_json_value,
    default_object_from_semantics,
    escape_h5p_value,
    merge_json_values,
)


class ComponentSyncer:
    """Sync component representation between MDX and H5P payloads."""

    def __init__(
        self,
        *,
        python_question_machine_name: str,
        load_python_question_semantics: Callable[[], list[dict[str, object]]],
        load_h5p_payload_from_source_package: Callable[[PythonQuestionBlock], tuple[dict[str, object], dict[str, object]] | None],
        build_h5p_metadata: Callable[[PythonQuestionBlock], dict],
    ) -> None:
        self._python_question_machine_name = python_question_machine_name
        self._load_python_question_semantics = load_python_question_semantics
        self._load_h5p_payload_from_source_package = load_h5p_payload_from_source_package
        self._build_h5p_metadata = build_h5p_metadata

    def _build_default_python_question_content(self, question: PythonQuestionBlock) -> dict[str, object]:
        defaults = default_object_from_semantics(self._load_python_question_semantics())
        defaults["pythonRunner"] = question.runner
        defaults.setdefault("advancedOptions", {})
        if isinstance(defaults["advancedOptions"], dict):
            defaults["advancedOptions"]["showConsole"] = question.show_console
        defaults.setdefault("pyodideOptions", {})
        if isinstance(defaults["pyodideOptions"], dict):
            raw_pkgs = ensure_miniworlds_packages(question.packages)
            defaults["pyodideOptions"]["packages"] = packages_for_h5p_content(raw_pkgs)
        defaults.setdefault("editorSettings", {})
        if isinstance(defaults["editorSettings"], dict):
            defaults["editorSettings"]["instructions"] = question.instructions
            defaults.setdefault("editorSettings", {}).setdefault("options", {})
            options = defaults["editorSettings"].get("options")
            if isinstance(options, dict):
                options["allowAddingFiles"] = question.allow_adding_files
        defaults.setdefault("gradingSettings", {})
        if isinstance(defaults["gradingSettings"], dict):
            defaults["gradingSettings"]["gradingMethod"] = question.grading_method
        return defaults

    def _build_default_imported_h5p_metadata(self, question: PythonQuestionBlock) -> dict[str, object]:
        try:
            metadata = self._build_h5p_metadata(question)
        except (ValueError, FileNotFoundError):
            return {}
        metadata.pop("title", None)
        metadata.pop("mainLibrary", None)
        return metadata

    def _build_default_imported_h5p_content(self, question: PythonQuestionBlock) -> dict[str, object]:
        if question.main_library != self._python_question_machine_name:
            return {}
        return self._build_default_python_question_content(question)

    def compute_question_hash(self, question: PythonQuestionBlock) -> str:
        return block_to_content_type(question).compute_hash()

    def build_h5p_content(self, question: PythonQuestionBlock) -> dict:
        return block_to_content_type(question).build_content_json()

    def build_editable_h5p_payload(self, question: PythonQuestionBlock) -> dict[str, object]:
        if question.h5p_metadata is None or question.h5p_content is None:
            return {}
        content_type = block_to_content_type(question)
        if question.main_library == self._python_question_machine_name:
            return content_type.build_editable_payload(semantics=self._load_python_question_semantics())
        source_payload = self._load_h5p_payload_from_source_package(question)
        return content_type.build_editable_payload(source_payload=source_payload)

    def render_imported_question_mdx(self, question: PythonQuestionBlock) -> list[str]:
        content_type = block_to_content_type(question)
        if question.main_library == self._python_question_machine_name:
            rendered = content_type.render_mdx_tag(semantics=self._load_python_question_semantics())
        else:
            source_payload = self._load_h5p_payload_from_source_package(question)
            rendered = content_type.render_mdx_tag(source_payload=source_payload)
        return rendered.split("\n")

    def apply_editable_h5p_payload(self, question: PythonQuestionBlock, payload: dict[str, object]) -> None:
        escaped_payload = escape_h5p_value(payload)
        if (
            question.main_library == self._python_question_machine_name
            and "metadata" not in escaped_payload
            and "content" not in escaped_payload
        ):
            metadata = self._build_h5p_metadata(question)
            metadata["title"] = question.title
            metadata["mainLibrary"] = question.main_library
            content = merge_json_values(self._build_default_python_question_content(question), escaped_payload)
            if not isinstance(content, dict):
                raise ValueError("Der H5P-Block konnte nicht in ein gueltiges H5P-Objekt umgewandelt werden.")
            question.h5p_metadata = metadata
            question.h5p_content = content
            return

        source_payload = self._load_h5p_payload_from_source_package(question)
        if source_payload is not None:
            source_metadata, source_content = source_payload
            metadata_override = escaped_payload.get("metadata", {})
            content_override = escaped_payload.get("content", {})
            if metadata_override is None:
                metadata_override = {}
            if content_override is None:
                content_override = {}
            if not isinstance(metadata_override, dict):
                raise ValueError("Der H5P-Block erwartet fuer 'metadata' ein JSON-Objekt.")
            if not isinstance(content_override, dict):
                raise ValueError("Der H5P-Block erwartet fuer 'content' ein JSON-Objekt.")

            metadata_base = clone_json_value(source_metadata)
            content_base = clone_json_value(source_content)
            if not isinstance(metadata_base, dict) or not isinstance(content_base, dict):
                raise ValueError("Das source.h5p enthaelt keine gueltigen H5P-Daten.")

            metadata_base.pop("title", None)
            metadata_base.pop("mainLibrary", None)
            metadata = merge_json_values(metadata_base, metadata_override)
            content = merge_json_values(content_base, content_override)
            if not isinstance(metadata, dict) or not isinstance(content, dict):
                raise ValueError("Der H5P-Block konnte nicht in ein gueltiges H5P-Objekt umgewandelt werden.")

            metadata["title"] = question.title
            metadata["mainLibrary"] = question.main_library
            question.h5p_metadata = metadata
            question.h5p_content = content
            return

        metadata_override = escaped_payload.get("metadata", {})
        content_override = escaped_payload.get("content", {})
        if metadata_override is None:
            metadata_override = {}
        if content_override is None:
            content_override = {}
        if not isinstance(metadata_override, dict):
            raise ValueError("Der H5P-Block erwartet fuer 'metadata' ein JSON-Objekt.")
        if not isinstance(content_override, dict):
            raise ValueError("Der H5P-Block erwartet fuer 'content' ein JSON-Objekt.")

        metadata = merge_json_values(self._build_default_imported_h5p_metadata(question), metadata_override)
        content = merge_json_values(self._build_default_imported_h5p_content(question), content_override)
        if not isinstance(metadata, dict) or not isinstance(content, dict):
            raise ValueError("Der H5P-Block konnte nicht in ein gueltiges H5P-Objekt umgewandelt werden.")

        metadata["title"] = question.title
        metadata["mainLibrary"] = question.main_library
        if question.main_library == self._python_question_machine_name:
            content.setdefault("pythonRunner", question.runner)

        question.h5p_metadata = metadata
        question.h5p_content = content

    def normalize_template_literal(self, content: str) -> str:
        if content.startswith("\n"):
            content = content[1:]
        content = textwrap.dedent(content)
        content = content.replace("\\`", "`").replace("\\${", "${")
        content = content.replace("\\n", "\n").replace("\\r", "\r").replace("\\t", "\t")
        content = content.replace("\\\\", "\\")
        return content

    def jsx_expression_to_json(self, expression: str) -> str:
        result: list[str] = []
        index = 0
        in_string = False
        in_template = False
        escaped = False
        template_buffer: list[str] = []
        while index < len(expression):
            char = expression[index]
            if in_template:
                if escaped:
                    template_buffer.append("\\" + char)
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == "`":
                    result.append(json.dumps(self.normalize_template_literal("".join(template_buffer)), ensure_ascii=False))
                    template_buffer = []
                    in_template = False
                else:
                    template_buffer.append(char)
                index += 1
                continue
            if in_string:
                result.append(char)
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                index += 1
                continue
            if char == '"':
                in_string = True
                result.append(char)
                index += 1
                continue
            if char == "`":
                in_template = True
                template_buffer = []
                index += 1
                continue
            result.append(char)
            index += 1
        if in_template:
            raise ValueError("Unvollstaendiger Template-String im PythonQuestion-Tag.")
        return "".join(result)

    def parse_jsx_expression(self, expression: str) -> object:
        return json.loads(self.jsx_expression_to_json(expression))
