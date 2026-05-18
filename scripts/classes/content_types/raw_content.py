"""Catch-all content type for any unrecognised H5P library."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from ._helpers import clone_json_value, diff_json_values, render_jsx_value, render_tag_attribute, unescape_display_value
from .base import H5PContentType


@dataclass(slots=True)
class RawH5PContent(H5PContentType):
    """Fallback for H5P content types that are not explicitly supported.

    The package is downloaded verbatim from Moodle and stored without
    field-level extraction.  :meth:`for_machine_name` returns this class
    for any machine name that is not registered by another content type.

    This is also the right base to start from when adding support for a
    new H5P type: create a dedicated subclass, set ``MACHINE_NAME``, and
    override the three abstract methods.  The new type is then automatically
    preferred over ``RawH5PContent`` for its machine name.
    """

    # No MACHINE_NAME — acts as the default fallback via for_machine_name()
    MACHINE_NAME: ClassVar[str] = ""

    identifier: str
    title: str
    main_library: str  # the actual H5P machine name (e.g. "H5P.Blanks")
    instructions: str = ""

    preview_url: str = ""
    package_url: str = ""
    raw_package: bool = True
    h5p_metadata: dict[str, object] | None = None
    h5p_content: dict[str, object] | None = None
    source_package_path: str = ""
    h5p_subdir: str = ""
    course_dir: Path | None = None
    course_slug: str = ""

    # ------------------------------------------------------------------
    # H5PContentType interface
    # ------------------------------------------------------------------

    @classmethod
    def from_block(cls, block: object) -> RawH5PContent:
        """Create a ``RawH5PContent`` from a ``PythonQuestionBlock``-compatible object."""
        return cls(
            identifier=getattr(block, "identifier"),
            title=getattr(block, "title"),
            main_library=getattr(block, "main_library", ""),
            instructions=getattr(block, "instructions", ""),
            preview_url=getattr(block, "preview_url", ""),
            package_url=getattr(block, "package_url", ""),
            raw_package=getattr(block, "raw_package", True),
            h5p_metadata=getattr(block, "h5p_metadata", None),
            h5p_content=getattr(block, "h5p_content", None),
            source_package_path=getattr(block, "source_package_path", ""),
            h5p_subdir=getattr(block, "h5p_subdir", ""),
            course_dir=getattr(block, "course_dir", None),
            course_slug=getattr(block, "course_slug", ""),
        )

    def compute_hash(self) -> str:
        """Return a SHA-256 hex-digest for change detection."""
        payload = {
            "identifier": self.identifier,
            "title": self.title,
            "mainLibrary": self.main_library,
            "rawPackage": self.raw_package,
            "packageUrl": self.package_url,
            "h5pMetadata": self.h5p_metadata,
            "h5pContent": self.h5p_content,
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()

    def build_editable_payload(
        self,
        *,
        semantics: list[dict[str, object]] | None = None,  # unused
        source_payload: tuple[dict[str, object], dict[str, object]] | None = None,
    ) -> dict[str, object]:
        """Return the minimal diff payload for MDX storage.

        Pass *source_payload* to store only the changed keys relative to the
        original source ``.h5p`` archive.  Without it the full
        metadata/content is returned (minus ``title`` and ``mainLibrary``).
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
        """Return the ``<PythonQuestion h5pLibrary="…" rawPackage="true" />`` tag."""
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
        ]
        if self.instructions:
            lines.append(render_tag_attribute("instructions", self.instructions))
        lines.append(f'  h5pLibrary="{html_escape(self.main_library)}"')
        if self.raw_package:
            lines.append('  rawPackage="true"')
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


def html_escape(value: str) -> str:
    import html
    return html.escape(value, quote=True)
