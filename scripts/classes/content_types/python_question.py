"""H5P.PythonQuestion content type."""
from __future__ import annotations

import hashlib
import html
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, ClassVar

from scripts.classes.h5p_runtime_manager.runtime_ids import build_runtime_content_id
from scripts.classes.python_runner_policy import (
    DEFAULT_PYTHON_RUNNER,
    contains_miniworlds_import,
    contains_miniworlds_package,
    ensure_miniworlds_packages,
)

from ..models import SourceFile, TestCase
from ._helpers import (
    clone_json_value,
    compact_by_semantics,
    default_object_from_semantics,
    normalize_whitespace,
    render_jsx_value,
    render_tag_attribute,
    unescape_display_value,
)
from .base import H5PContentType


@dataclass(slots=True)
class PythonQuestion(H5PContentType):
    """H5P.PythonQuestion — the custom Python IDE activity.

    Represents a question that lets learners write and run Python code directly
    in the browser.  Instances are created either from an MDX course file (via
    ``parse_tag_attributes``) or by importing an H5P package from Moodle.

    The class provides all operations needed to round-trip the content:

    * :meth:`compute_hash` — change detection / cache invalidation
    * :meth:`build_content_json` — build ``content/content.json`` for a scratch package
    * :meth:`build_metadata_json` — build ``h5p.json`` given a parsed ``library.json``
    * :meth:`build_editable_payload` — compact diff stored in the MDX ``h5p={…}`` attribute
    * :meth:`render_mdx_tag` — render the ``<PythonQuestion … />`` JSX tag + code fences
    """

    MACHINE_NAME: ClassVar[str] = "H5P.PythonQuestion"
    PLACEHOLDER_TEMPLATE: ClassVar[str] = "[[[PYTHON_QUESTION:{identifier}]]]"

    # Required identity fields
    identifier: str
    title: str
    instructions: str

    # Optional location / package metadata
    preview_url: str = ""
    package_url: str = ""
    h5p_metadata: dict[str, object] | None = None
    h5p_content: dict[str, object] | None = None
    h5p_metadata_path: str = ""
    h5p_content_path: str = ""
    source_package_path: str = ""
    h5p_subdir: str = ""

    # Python IDE parameters
    runner: str = DEFAULT_PYTHON_RUNNER
    packages: list[str] = field(default_factory=list)
    starter_code: str = ""
    solution_code: str = ""
    pre_code: str = ""
    post_code: str = ""
    grading_method: str = "please_choose"
    show_console: bool = True
    allow_adding_files: bool = False
    source_files: list[SourceFile] = field(default_factory=list)
    test_cases: list[TestCase] = field(default_factory=list)

    # Course location
    course_dir: Path | None = None
    course_slug: str = ""

    # ------------------------------------------------------------------
    # Extra path property not provided by H5PContentsMixin
    # ------------------------------------------------------------------

    @property
    def runtime_content_id(self) -> str:
        return build_runtime_content_id(self.course_slug, self.identifier)

    # ------------------------------------------------------------------
    # H5PContentType interface
    # ------------------------------------------------------------------

    @classmethod
    def from_block(cls, block: object) -> PythonQuestion:
        """Create a ``PythonQuestion`` from a ``PythonQuestionBlock``-compatible object."""
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
            runner=getattr(block, "runner", DEFAULT_PYTHON_RUNNER),
            packages=list(getattr(block, "packages", [])),
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
        """Return a SHA-256 hex-digest over all content-relevant fields."""
        payload = {
            "identifier": self.identifier,
            "title": self.title,
            "instructions": self.instructions,
            "mainLibrary": self.MACHINE_NAME,
            "rawPackage": False,
            "h5pMetadata": self.h5p_metadata,
            "h5pContent": self.h5p_content,
            "runner": self.runner,
            "packages": self.packages,
            "starterCode": self.starter_code,
            "solutionCode": self.solution_code,
            "preCode": self.pre_code,
            "postCode": self.post_code,
            "gradingMethod": self.grading_method,
            "showConsole": self.show_console,
            "allowAddingFiles": self.allow_adding_files,
            "sourceFiles": [
                {
                    "fileName": sf.file_name,
                    "code": sf.code,
                    "visibleToLearner": sf.visible_to_learner,
                    "learnerEditable": sf.learner_editable,
                }
                for sf in self.source_files
            ],
            "testCases": [
                {"hidden": tc.hidden, "inputs": tc.inputs, "outputs": tc.outputs}
                for tc in self.test_cases
            ],
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()

    def build_editable_payload(
        self,
        *,
        semantics: list[dict[str, object]] | None = None,
        source_payload: tuple[dict[str, object], dict[str, object]] | None = None,  # unused, accepted for uniform signature
    ) -> dict[str, object]:
        """Return the compact payload to store in the MDX ``h5p={…}`` attribute.

        When *semantics* is the parsed ``semantics.json`` list for
        ``H5P.PythonQuestion``, the result is stripped of all default values.
        Without *semantics* only the well-known top-level keys are pruned.
        """
        if self.h5p_metadata is None or self.h5p_content is None:
            return {}

        content = clone_json_value(self.h5p_content)
        if not isinstance(content, dict):
            return {}

        defaults = self._build_default_content(semantics)
        semantic_fields: dict[str, dict[str, object]] = {}
        if semantics:
            semantic_fields = {
                str(f.get("name") or "").strip(): f
                for f in semantics
                if str(f.get("name") or "").strip()
            }

        compacted: dict[str, object] = {}
        for key, value in content.items():
            # Prune known fields that are already captured in dedicated MDX attrs
            if key == "pythonRunner" and value == self.runner:
                continue
            if key == "editorSettings" and isinstance(value, dict):
                value = clone_json_value(value)
                if isinstance(value, dict) and normalize_whitespace(
                    html.unescape(str(value.get("instructions") or ""))
                ) == self.instructions:
                    value.pop("instructions", None)
            if key == "advancedOptions" and isinstance(value, dict):
                value = clone_json_value(value)
                if isinstance(value, dict) and value.get("showConsole") == self.show_console:
                    value.pop("showConsole", None)
            if key == "pyodideOptions" and isinstance(value, dict):
                value = clone_json_value(value)
                if isinstance(value, dict) and value.get("packages") == defaults.get(
                    "pyodideOptions", {}
                ).get("packages"):
                    value.pop("packages", None)
            if key == "gradingSettings" and isinstance(value, dict):
                value = clone_json_value(value)
                if isinstance(value, dict) and value.get("gradingMethod") == self.grading_method:
                    value.pop("gradingMethod", None)

            # Semantics-based compaction for remaining keys
            sem_field = semantic_fields.get(key)
            if sem_field is None:
                # No semantics available for this key — keep as-is
                compacted[key] = clone_json_value(value)
                continue
            compacted_value = compact_by_semantics(value, sem_field)
            if compacted_value is not None:
                compacted[key] = compacted_value

        return compacted

    def render_mdx_tag(
        self,
        *,
        semantics: list[dict[str, object]] | None = None,
        source_payload: tuple[dict[str, object], dict[str, object]] | None = None,  # unused
    ) -> str:
        """Return the complete MDX fragment: ``<PythonQuestion … />`` + code fences."""
        lines: list[str] = []

        # Stub variant (source package present — content is in the sidecar)
        if self.source_package_path:
            lines = [
                "<PythonQuestion",
                render_tag_attribute("identifier", self.identifier),
                "/>",
                "",
            ]
            return "\n".join(lines)

        # Compact h5p payload for imported questions
        payload_lines: list[str] = []
        if self.h5p_metadata is not None and self.h5p_content is not None:
            payload_lines = render_jsx_value(
                unescape_display_value(self.build_editable_payload(semantics=semantics)), indent=2
            ).splitlines()

        lines = [
            "<PythonQuestion",
            render_tag_attribute("identifier", self.identifier),
            render_tag_attribute("title", self.title),
            render_tag_attribute("instructions", self.instructions),
        ]
        lines.append(render_tag_attribute("runner", self.runner))
        if self.packages:
            lines.append(render_tag_attribute("packages", ", ".join(self.packages)))
        if self.grading_method != "please_choose":
            lines.append(render_tag_attribute("gradingMethod", self.grading_method))
        if not self.show_console:
            lines.append('  showConsole="false"')
        if self.allow_adding_files:
            lines.append('  allowAddingFiles="true"')
        if payload_lines:
            if len(payload_lines) == 1:
                lines.append("  h5p={" + payload_lines[0] + "}")
            else:
                lines.append("  h5p={" + payload_lines[0])
                for pl in payload_lines[1:-1]:
                    lines.append(f"  {pl}")
                lines.append("  }}")

        lines.extend(["/>", ""])

        if payload_lines:
            return "\n".join(lines)

        # Code fence blocks (scratch / IDE-only questions)
        for role, body in [
            ("pre", self.pre_code),
            ("starter", self.starter_code),
            ("solution", self.solution_code),
            ("post", self.post_code),
        ]:
            if not body:
                continue
            lines += [
                f"```python question:{self.identifier} {role}",
                body,
                "```",
                "",
            ]

        for sf in self.source_files:
            if not sf.code:
                continue
            tokens = [f"file:{sf.file_name}"]
            if not sf.visible_to_learner:
                tokens.append("hidden-file")
            if not sf.learner_editable:
                tokens.append("readonly-file")
            lang = "python" if sf.file_name.endswith(".py") else "text"
            lines += [
                f"```{lang} question:{self.identifier} {' '.join(tokens)}",
                sf.code,
                "```",
                "",
            ]

        for tc in self.test_cases:
            tc_payload = {"hidden": tc.hidden, "inputs": tc.inputs, "outputs": tc.outputs}
            lines += [
                f"```json question:{self.identifier} testcase",
                json.dumps(tc_payload, ensure_ascii=False, indent=2),
                "```",
                "",
            ]

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Scratch-package builders
    # ------------------------------------------------------------------

    def build_content_json(self) -> dict[str, object]:
        """Build the ``content/content.json`` dict for a from-scratch H5P package.

        This is used when no imported package is available and the question
        is assembled entirely from the MDX source.
        """
        grading_method = self.grading_method
        if self.test_cases and grading_method == "please_choose":
            grading_method = "ioTestCases"
        source = "\n".join([self.starter_code, self.solution_code, self.pre_code, self.post_code])
        packages = ensure_miniworlds_packages(
            self.packages,
            source=source,
        )
        uses_miniworlds = contains_miniworlds_package(packages) or contains_miniworlds_import(source)
        source_files = [
            {
                "fileName": sf.file_name,
                "code": sf.code,
                "visibleToLearner": sf.visible_to_learner,
                "learnerEditable": sf.learner_editable,
            }
            for sf in self.source_files
        ]

        return {
            "contentType": "ide_only",
            "pythonRunner": self.runner,
            "advancedOptions": {
                "showConsole": self.show_console,
                "disableOutputPopups": False,
                "enableSaveLoadButtons": True,
                "execLimit": 0,
            },
            "pyodideOptions": {
                "pyodideCdnUrl": "",
                "packages": packages,
            },
            "contents": [],
            "editorSettings": {
                "instructions": self.instructions,
                "preCode": self.pre_code,
                "startingCode": self.starter_code,
                "postCode": self.post_code,
                "options": {
                    "enableImageUploads": uses_miniworlds,
                    "enableSoundUploads": False,
                    "sourceFiles": source_files,
                    "allowAddingFiles": self.allow_adding_files or uses_miniworlds,
                    "editorMode": "code",
                },
            },
            "gradingSettings": {
                "gradingMethod": grading_method,
                "dueDateGroup": {
                    "enableDueDate": False,
                    "duedate": "01.01.1970",
                },
                "testCases": [
                    {
                        "hidden": tc.hidden,
                        "inputs": list(tc.inputs),
                        "outputs": list(tc.outputs),
                    }
                    for tc in self.test_cases
                ],
                "targetCode": self.solution_code,
            },
        }

    def build_metadata_json(self, library_json: dict[str, object]) -> dict[str, object]:
        """Build ``h5p.json`` from an already-parsed ``library.json`` dict.

        The caller is responsible for reading the file; this method contains no
        I/O so it is straightforward to test.
        """
        preloaded_dependencies = [
            {
                "machineName": library_json["machineName"],
                "majorVersion": library_json["majorVersion"],
                "minorVersion": library_json["minorVersion"],
            }
        ]
        preloaded_dependencies.extend(
            {
                "machineName": dep["machineName"],
                "majorVersion": dep["majorVersion"],
                "minorVersion": dep["minorVersion"],
            }
            for dep in library_json.get("preloadedDependencies", [])
        )
        return {
            "title": self.title,
            "language": "de",
            "defaultLanguage": "de",
            "mainLibrary": self.MACHINE_NAME,
            "embedTypes": ["div"],
            "license": "U",
            "preloadedDependencies": preloaded_dependencies,
            "majorVersion": library_json["majorVersion"],
            "minorVersion": library_json["minorVersion"],
        }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_default_content(
        self,
        semantics: list[dict[str, object]] | None,
    ) -> dict[str, object]:
        """Return the default content object (for compaction baseline)."""
        if semantics:
            defaults = default_object_from_semantics(semantics)
        else:
            defaults = {}

        defaults["pythonRunner"] = self.runner
        defaults.setdefault("advancedOptions", {})
        if isinstance(defaults["advancedOptions"], dict):
            defaults["advancedOptions"]["showConsole"] = self.show_console

        defaults.setdefault("pyodideOptions", {})
        if isinstance(defaults["pyodideOptions"], dict):
            defaults["pyodideOptions"]["packages"] = ensure_miniworlds_packages(self.packages)

        defaults.setdefault("editorSettings", {})
        if isinstance(defaults["editorSettings"], dict):
            defaults["editorSettings"]["instructions"] = self.instructions
            defaults["editorSettings"].setdefault("options", {})
            opts = defaults["editorSettings"].get("options")
            if isinstance(opts, dict):
                opts["allowAddingFiles"] = self.allow_adding_files

        defaults.setdefault("gradingSettings", {})
        if isinstance(defaults["gradingSettings"], dict):
            defaults["gradingSettings"]["gradingMethod"] = self.grading_method

        return defaults


def _loader_for(callable_or_none: Callable[[], list[dict[str, object]]] | None) -> list[dict[str, object]] | None:
    """Invoke *callable_or_none* if provided, otherwise return ``None``."""
    return callable_or_none() if callable_or_none is not None else None
