from __future__ import annotations

import re


def quote_path_segment(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._~-]", lambda match: f"%{ord(match.group(0)):02X}", value)


def build_runtime_content_id(course_slug: str, identifier: str) -> str:
    return f"{quote_path_segment(course_slug)}-{quote_path_segment(identifier)}"
