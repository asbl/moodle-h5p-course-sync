from __future__ import annotations

import html
import re
import textwrap
import unicodedata


class TextOperations:
    """Provides shared text normalization and identifier helpers."""

    def __init__(self, *, html_tag_re: re.Pattern[str], whitespace_re: re.Pattern[str]) -> None:
        self._html_tag_re = html_tag_re
        self._whitespace_re = whitespace_re

    def normalize_whitespace(self, value: str) -> str:
        return textwrap.dedent(value).strip()

    def strip_html(self, value: str) -> str:
        return html.unescape(self._html_tag_re.sub(" ", value)).strip()

    def compact_text(self, value: str) -> str:
        return self._whitespace_re.sub(" ", html.unescape(value)).strip()

    def slugify_identifier(self, value: str) -> str:
        normalized = value.strip().lower()
        for source, target in {
            "ä": "ae",
            "ö": "oe",
            "ü": "ue",
            "ß": "ss",
        }.items():
            normalized = normalized.replace(source, target)
        normalized = unicodedata.normalize("NFKD", normalized).encode("ascii", "ignore").decode("ascii")
        slug = re.sub(r"[^a-z0-9]+", "-", normalized).strip("-")
        return slug or "h5p"

    def make_stable_identifier(self, title: str, existing_identifiers: set[str]) -> str:
        base = self.slugify_identifier(title)
        identifier = base
        suffix = 2
        while identifier in existing_identifiers:
            identifier = f"{base}-{suffix}"
            suffix += 1
        existing_identifiers.add(identifier)
        return identifier
