"""H5P.JavaQuestion content type."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

from ..models import SourceFile, TestCase
from ._helpers import (
    clone_json_value,
    normalize_whitespace,
    render_jsx_value,
    render_tag_attribute,
    unescape_display_value,
)
from .base import H5PContentType


JAVA_H5P_DEPENDENCIES = [
    {"machineName": "H5P.JavaQuestion", "majorVersion": 1, "minorVersion": 1},
    {"machineName": "H5P.LibCodeTools", "majorVersion": 6, "minorVersion": 90},
    {"machineName": "H5P.CodeQuestion", "majorVersion": 6, "minorVersion": 90},
    {"machineName": "H5P.Question", "majorVersion": 1, "minorVersion": 5},
    {"machineName": "FontAwesome", "majorVersion": 4, "minorVersion": 5},
    {"machineName": "H5P.JoubelUI", "majorVersion": 1, "minorVersion": 3},
    {"machineName": "H5P.FontIcons", "majorVersion": 1, "minorVersion": 0},
    {"machineName": "H5P.Transition", "majorVersion": 1, "minorVersion": 0},
    {"machineName": "H5P.Components", "majorVersion": 1, "minorVersion": 0},
    {"machineName": "jQuery.ui", "majorVersion": 1, "minorVersion": 10},
]


@dataclass(slots=True)
class JavaQuestion(H5PContentType):
    """H5P.JavaQuestion — Java IDE activity with TeaVM browser compilation."""

    MACHINE_NAME: ClassVar[str] = "H5P.JavaQuestion"

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
    pre_code: str = ""
    post_code: str = ""
    main_class_name: str = "Main"
    grading_method: str = "please_choose"
    show_console: bool = True
    allow_adding_files: bool = False
    source_files: list[SourceFile] = field(default_factory=list)
    test_cases: list[TestCase] = field(default_factory=list)

    course_dir: Path | None = None
    course_slug: str = ""

    @classmethod
    def from_block(cls, block: object) -> JavaQuestion:
        return cls(
            identifier=getattr(block, "identifier"),
            title=getattr(block, "title"),
            instructions=getattr(block, "instructions"),
            preview_url=getattr(block, "preview_url", ""),
            package_url=getattr(block, "package_url", ""),
            h5p_metadata=getattr(block, "h5p_metadata", None),
            h5p_content=getattr(block, "h5p_content", None),
            h5p_metadata_path=getattr(block, "h5p_metadata_path", ""),
            h5p_content_path=getattr(block, "h5p_content_path", ""),
            source_package_path=getattr(block, "source_package_path", ""),
            h5p_subdir=getattr(block, "h5p_subdir", ""),
            starter_code=getattr(block, "starter_code", ""),
            solution_code=getattr(block, "solution_code", ""),
            pre_code=getattr(block, "pre_code", ""),
            post_code=getattr(block, "post_code", ""),
            grading_method=getattr(block, "grading_method", "please_choose"),
            show_console=getattr(block, "show_console", True),
            allow_adding_files=getattr(block, "allow_adding_files", False),
            source_files=list(getattr(block, "source_files", [])),
            test_cases=list(getattr(block, "test_cases", [])),
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
            "preCode": self.pre_code,
            "postCode": self.post_code,
            "mainClassName": self.main_class_name,
            "gradingMethod": self.grading_method,
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
            "<JavaQuestion",
            render_tag_attribute("identifier", self.identifier),
            render_tag_attribute("title", self.title),
            render_tag_attribute("instructions", self.instructions),
        ]
        if self.grading_method != "please_choose":
            lines.append(render_tag_attribute("gradingMethod", self.grading_method))
        if not self.show_console:
            lines.append('  showConsole="false"')
        lines.extend(["/>", ""])

        for role, body in [
            ("pre", self.pre_code),
            ("starter", self.starter_code),
            ("solution", self.solution_code),
            ("post", self.post_code),
        ]:
            if not body:
                continue
            lines += [
                f"```java question:{self.identifier} {role}",
                body,
                "```",
                "",
            ]

        return "\n".join(lines)

    def build_content_json(self) -> dict[str, object]:
        """Build content/content.json for a from-scratch Java H5P package."""
        grading_method = self.grading_method
        if self.test_cases and grading_method == "please_choose":
            grading_method = "ioTestCases"

        default_starter = (
            f"public class {self.main_class_name} {{\n"
            "    public static void main(String[] args) {\n"
            '        System.out.println("Hello, World!");\n'
            "    }\n"
            "}"
        )

        return {
            "contentType": "ide_only",
            "advancedOptions": {
                "showConsole": self.show_console,
                "enableSaveLoadButtons": True,
                "externalLibraryUrls": "",
            },
            "editorSettings": {
                "mainClassName": self.main_class_name,
                "startingCode": self.starter_code or default_starter,
                "preCode": self.pre_code,
                "postCode": self.post_code,
                "stdin": "",
                "instructions": self.instructions,
            },
            "gradingSettings": {
                "gradingMethod": grading_method,
                "expectedOutput": self.solution_code,
                "trimOutput": True,
                "solution": self.solution_code,
            },
            "l10n": {
                "checkAnswer": "Überprüfen",
                "run": "Ausführen",
                "stop": "Stopp",
                "failedText": "Noch nicht korrekt",
                "successText": "Korrekt!",
                "testCase": "Testfall",
                "hidden": "[versteckt]",
            },
            "contents": [{"type": "text", "showEditor": False}],
        }

    def build_metadata_json(self, library_json: dict[str, object]) -> dict[str, object]:
        """Build h5p.json for this JavaQuestion package."""
        return {
            "title": self.title,
            "language": "und",
            "defaultLanguage": "de-wp",
            "mainLibrary": self.MACHINE_NAME,
            "embedTypes": ["iframe"],
            "license": "U",
            "preloadedDependencies": JAVA_H5P_DEPENDENCIES,
        }
