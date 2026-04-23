"""H5P.QuestionSet content type (imported quizzes)."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from ._helpers import clone_json_value, diff_json_values, render_jsx_value, render_tag_attribute, unescape_display_value
from .base import H5PContentType


@dataclass(slots=True)
class QuestionSet(H5PContentType):
    """H5P.QuestionSet — an imported multi-question quiz from Moodle.

    These questions are stored opaquely: the full H5P metadata and content
    are kept verbatim, and only the diff against the original source package
    is written into the MDX ``h5p={…}`` attribute.
    """

    MACHINE_NAME: ClassVar[str] = "H5P.QuestionSet"

    identifier: str
    title: str
    instructions: str

    preview_url: str = ""
    package_url: str = ""
    h5p_metadata: dict[str, object] | None = None
    h5p_content: dict[str, object] | None = None
    source_package_path: str = ""
    course_dir: Path | None = None
    course_slug: str = ""

    # ------------------------------------------------------------------
    # H5PContentType interface
    # ------------------------------------------------------------------

    @classmethod
    def from_block(cls, block: object) -> QuestionSet:
        """Create a ``QuestionSet`` from a ``PythonQuestionBlock``-compatible object."""
        return cls(
            identifier=getattr(block, "identifier"),
            title=getattr(block, "title"),
            instructions=getattr(block, "instructions", ""),
            preview_url=getattr(block, "preview_url", ""),
            package_url=getattr(block, "package_url", ""),
            h5p_metadata=getattr(block, "h5p_metadata", None),
            h5p_content=getattr(block, "h5p_content", None),
            source_package_path=getattr(block, "source_package_path", ""),
            course_dir=getattr(block, "course_dir", None),
            course_slug=getattr(block, "course_slug", ""),
        )

    def compute_hash(self) -> str:
        """Return a SHA-256 hex-digest for change detection."""
        payload = {
            "identifier": self.identifier,
            "title": self.title,
            "instructions": self.instructions,
            "mainLibrary": self.MACHINE_NAME,
            "rawPackage": False,
            "h5pMetadata": self.h5p_metadata,
            "h5pContent": self.h5p_content,
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()

    def build_editable_payload(
        self,
        *,
        semantics: list[dict[str, object]] | None = None,  # unused, accepted for uniform signature
        source_payload: tuple[dict[str, object], dict[str, object]] | None = None,
    ) -> dict[str, object]:
        """Return the minimal diff payload for MDX storage.

        Pass *source_payload* (the original ``(h5p_metadata, content_json)``
        tuple from the source ``.h5p`` archive) to store only the changed
        keys.  Without it the full metadata/content is returned minus the
        ``title`` and ``mainLibrary`` fields that are always re-derived from
        the MDX attributes.
        """
        if self.h5p_metadata is None or self.h5p_content is None:
            return {}

        metadata = clone_json_value(self.h5p_metadata)
        content = clone_json_value(self.h5p_content)
        if not isinstance(metadata, dict) or not isinstance(content, dict):
            return {}

        metadata.pop("title", None)
        metadata.pop("mainLibrary", None)

        if source_payload is not None:
            source_meta, source_content = source_payload
            source_meta_copy = clone_json_value(source_meta)
            if isinstance(source_meta_copy, dict):
                source_meta_copy.pop("title", None)
                source_meta_copy.pop("mainLibrary", None)

            metadata_diff = diff_json_values(metadata, source_meta_copy)
            content_diff = diff_json_values(content, source_content)
        else:
            metadata_diff = metadata if metadata else None
            content_diff = content if content else None

        result: dict[str, object] = {}
        if isinstance(metadata_diff, dict) and metadata_diff:
            result["metadata"] = metadata_diff
        if isinstance(content_diff, dict) and content_diff:
            result["content"] = content_diff
        elif isinstance(content_diff, list):
            result["content"] = content_diff
        return result

    def render_mdx_tag(
        self,
        *,
        semantics: list[dict[str, object]] | None = None,  # unused
        source_payload: tuple[dict[str, object], dict[str, object]] | None = None,
    ) -> str:
        """Return the ``<PythonQuestion h5pLibrary="H5P.QuestionSet" … />`` tag."""
        # Stub when source package present
        if self.source_package_path:
            return "\n".join([
                "<PythonQuestion",
                render_tag_attribute("identifier", self.identifier),
                "/>",
                "",
            ])

        payload_lines: list[str] = []
        if self.h5p_metadata is not None and self.h5p_content is not None:
            editable = self.build_editable_payload(source_payload=source_payload)
            if editable:
                payload_lines = render_jsx_value(
                    unescape_display_value(editable), indent=2
                ).splitlines()

        lines = [
            "<PythonQuestion",
            render_tag_attribute("identifier", self.identifier),
            render_tag_attribute("title", self.title),
            render_tag_attribute("instructions", self.instructions),
            f'  h5pLibrary="{self.MACHINE_NAME}"',
        ]
        if payload_lines:
            if len(payload_lines) == 1:
                lines.append("  h5p={" + payload_lines[0] + "}")
            else:
                lines.append("  h5p={" + payload_lines[0])
                for pl in payload_lines[1:-1]:
                    lines.append(f"  {pl}")
                lines.append("  }}")
        lines.extend(["/>", ""])
        return "\n".join(lines)
