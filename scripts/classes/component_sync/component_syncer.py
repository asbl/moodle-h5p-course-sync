from __future__ import annotations

from typing import Callable

from scripts.classes.models import PythonQuestionBlock
from scripts.classes.content_types import PythonQuestion as PythonQuestionContentType
from scripts.classes.content_types import block_to_content_type


class ComponentSyncer:
    """Sync component representation between MDX and H5P payloads."""

    def __init__(
        self,
        *,
        python_question_machine_name: str,
        load_python_question_semantics: Callable[[], list[dict[str, object]]],
        load_h5p_payload_from_source_package: Callable[[PythonQuestionBlock], tuple[dict[str, object], dict[str, object]] | None],
        clone_json_value: Callable[[object], object],
        escape_h5p_value: Callable[[object], object],
        merge_json_values: Callable[[object, object], object],
        build_h5p_metadata: Callable[[PythonQuestionBlock], dict],
        build_default_python_question_content: Callable[[PythonQuestionBlock], dict[str, object]],
        build_default_imported_h5p_metadata: Callable[[PythonQuestionBlock], dict[str, object]],
        build_default_imported_h5p_content: Callable[[PythonQuestionBlock], dict[str, object]],
    ) -> None:
        self._python_question_machine_name = python_question_machine_name
        self._load_python_question_semantics = load_python_question_semantics
        self._load_h5p_payload_from_source_package = load_h5p_payload_from_source_package
        self._clone_json_value = clone_json_value
        self._escape_h5p_value = escape_h5p_value
        self._merge_json_values = merge_json_values
        self._build_h5p_metadata = build_h5p_metadata
        self._build_default_python_question_content = build_default_python_question_content
        self._build_default_imported_h5p_metadata = build_default_imported_h5p_metadata
        self._build_default_imported_h5p_content = build_default_imported_h5p_content

    def compute_question_hash(self, question: PythonQuestionBlock) -> str:
        return block_to_content_type(question).compute_hash()

    def build_h5p_content(self, question: PythonQuestionBlock) -> dict:
        return PythonQuestionContentType.from_block(question).build_content_json()

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
        escaped_payload = self._escape_h5p_value(payload)
        if (
            question.main_library == self._python_question_machine_name
            and "metadata" not in escaped_payload
            and "content" not in escaped_payload
        ):
            metadata = self._build_h5p_metadata(question)
            metadata["title"] = question.title
            metadata["mainLibrary"] = question.main_library
            content = self._merge_json_values(self._build_default_python_question_content(question), escaped_payload)
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

            metadata_base = self._clone_json_value(source_metadata)
            content_base = self._clone_json_value(source_content)
            if not isinstance(metadata_base, dict) or not isinstance(content_base, dict):
                raise ValueError("Das source.h5p enthaelt keine gueltigen H5P-Daten.")

            metadata_base.pop("title", None)
            metadata_base.pop("mainLibrary", None)
            metadata = self._merge_json_values(metadata_base, metadata_override)
            content = self._merge_json_values(content_base, content_override)
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

        metadata = self._merge_json_values(self._build_default_imported_h5p_metadata(question), metadata_override)
        content = self._merge_json_values(self._build_default_imported_h5p_content(question), content_override)
        if not isinstance(metadata, dict) or not isinstance(content, dict):
            raise ValueError("Der H5P-Block konnte nicht in ein gueltiges H5P-Objekt umgewandelt werden.")

        metadata["title"] = question.title
        metadata["mainLibrary"] = question.main_library
        if question.main_library == self._python_question_machine_name:
            content.setdefault("pythonRunner", question.runner)

        question.h5p_metadata = metadata
        question.h5p_content = content
