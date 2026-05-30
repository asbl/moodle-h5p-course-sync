"""H5P.AutomataQuestion content type."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from ._helpers import (
    clone_json_value,
    render_tag_attribute,
)
from .base import H5PContentType


AUTOMATA_H5P_DEPENDENCIES = [
    {"machineName": "H5P.AutomataQuestion", "majorVersion": 1, "minorVersion": 0},
    {"machineName": "H5P.LibCodeTools", "majorVersion": 6, "minorVersion": 90},
    {"machineName": "H5P.CodeQuestion", "majorVersion": 6, "minorVersion": 90},
    {"machineName": "H5P.LibAutomataTools", "majorVersion": 1, "minorVersion": 0},
    {"machineName": "H5P.Question", "majorVersion": 1, "minorVersion": 5},
    {"machineName": "FontAwesome", "majorVersion": 4, "minorVersion": 5},
    {"machineName": "H5P.JoubelUI", "majorVersion": 1, "minorVersion": 3},
    {"machineName": "H5P.FontIcons", "majorVersion": 1, "minorVersion": 0},
    {"machineName": "H5P.Transition", "majorVersion": 1, "minorVersion": 0},
    {"machineName": "H5P.Components", "majorVersion": 1, "minorVersion": 0},
    {"machineName": "jQuery.ui", "majorVersion": 1, "minorVersion": 10},
]


@dataclass(slots=True)
class AutomataQuestion(H5PContentType):
    """H5P.AutomataQuestion — automaton/regex IDE activity."""

    MACHINE_NAME: ClassVar[str] = "H5P.AutomataQuestion"

    identifier: str
    title: str
    instructions: str

    preview_url: str = ""
    package_url: str = ""
    h5p_metadata: dict[str, object] | None = None
    h5p_content: dict[str, object] | None = None
    h5p_metadata_path: str = ""
    h5p_content_path: str = ""
    source_package_path: str = ""
    h5p_subdir: str = ""

    starter_code: str = ""
    solution_code: str = ""
    grading_method: str = "please_choose"
    automaton_type: str = "DFA"
    show_console: bool = False

    course_dir: Path | None = None
    course_slug: str = ""

    @classmethod
    def from_block(cls, block: object) -> AutomataQuestion:
        h5p_content = getattr(block, "h5p_content", None)
        automaton_type = "DFA"
        if isinstance(h5p_content, dict):
            editor_settings = h5p_content.get("editorSettings", {})
            if isinstance(editor_settings, dict):
                automaton_type = str(editor_settings.get("automatonType") or "DFA")
        return cls(
            identifier=getattr(block, "identifier"),
            title=getattr(block, "title"),
            instructions=getattr(block, "instructions"),
            preview_url=getattr(block, "preview_url", ""),
            package_url=getattr(block, "package_url", ""),
            h5p_metadata=getattr(block, "h5p_metadata", None),
            h5p_content=h5p_content,
            h5p_metadata_path=getattr(block, "h5p_metadata_path", ""),
            h5p_content_path=getattr(block, "h5p_content_path", ""),
            source_package_path=getattr(block, "source_package_path", ""),
            h5p_subdir=getattr(block, "h5p_subdir", ""),
            starter_code=getattr(block, "starter_code", ""),
            solution_code=getattr(block, "solution_code", ""),
            grading_method=getattr(block, "grading_method", "please_choose"),
            automaton_type=getattr(block, "automaton_type", automaton_type),
            show_console=getattr(block, "show_console", False),
            course_dir=getattr(block, "course_dir", None),
            course_slug=getattr(block, "course_slug", ""),
        )

    def compute_hash(self) -> str:
        payload = {
            "identifier": self.identifier,
            "title": self.title,
            "instructions": self.instructions,
            "mainLibrary": self.MACHINE_NAME,
            "starterCode": self.starter_code,
            "solutionCode": self.solution_code,
            "gradingMethod": self.grading_method,
            "automatonType": self.automaton_type,
            "showConsole": self.show_console,
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()

    def build_editable_payload(
        self,
        *,
        semantics: list[dict[str, object]] | None = None,
        source_payload: tuple[dict[str, object], dict[str, object]] | None = None,
    ) -> dict[str, object]:
        if self.h5p_metadata is None or self.h5p_content is None:
            return {}
        content = clone_json_value(self.h5p_content)
        if not isinstance(content, dict):
            return {}
        return content

    def render_mdx_tag(
        self,
        *,
        semantics: list[dict[str, object]] | None = None,
        source_payload: tuple[dict[str, object], dict[str, object]] | None = None,
    ) -> str:
        lines = [
            "<AutomataQuestion",
            render_tag_attribute("identifier", self.identifier),
            render_tag_attribute("title", self.title),
            render_tag_attribute("instructions", self.instructions),
        ]
        if self.automaton_type and self.automaton_type != "DFA":
            lines.append(render_tag_attribute("automatonType", self.automaton_type))
        if self.grading_method != "please_choose":
            lines.append(render_tag_attribute("gradingMethod", self.grading_method))
        if self.show_console:
            lines.append('  showConsole="true"')
        lines.extend(["/>", ""])

        for role, body in [
            ("starter", self.starter_code),
            ("solution", self.solution_code),
        ]:
            if not body:
                continue
            lines += [
                f"```automata question:{self.identifier} {role}",
                body,
                "```",
                "",
            ]

        return "\n".join(lines)

    def build_content_json(self) -> dict[str, object]:
        grading_method = self.grading_method
        if grading_method == "please_choose":
            grading_method = "byAutomaton"

        return {
            "contentType": "ide_only",
            "editorSettings": {
                "startingCode": self.starter_code,
                "automatonType": self.automaton_type,
                "instructions": self.instructions,
            },
            "gradingSettings": {
                "gradingMethod": grading_method,
                "solutionAutomaton": self.solution_code,
            },
            "advancedOptions": {
                "showConsole": self.show_console,
            },
        }

    def build_metadata_json(self, library_json: dict[str, object]) -> dict[str, object]:
        return {
            "title": self.title,
            "language": "und",
            "defaultLanguage": "de-wp",
            "mainLibrary": self.MACHINE_NAME,
            "embedTypes": ["iframe"],
            "license": "U",
            "preloadedDependencies": AUTOMATA_H5P_DEPENDENCIES,
        }
