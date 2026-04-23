"""Pure utilities shared across content-type implementations.

All functions here are free of I/O and have no dependency on the runtime
libraries directory.  They can be used in tests without any setup.
"""
from __future__ import annotations

import html
import json
import textwrap


# ---------------------------------------------------------------------------
# JSON helpers
# ---------------------------------------------------------------------------

def clone_json_value(value: object) -> object:
    """Deep-copy a JSON-serialisable value via round-trip serialisation."""
    return json.loads(json.dumps(value, ensure_ascii=False))


def diff_json_values(actual: object, default: object) -> object | None:
    """Return the parts of *actual* that differ from *default*.

    Returns ``None`` when *actual* equals *default* (nothing to store).
    For dicts only the changed keys are returned; lists are stored whole.
    """
    if isinstance(actual, dict) and isinstance(default, dict):
        diff: dict[str, object] = {}
        for key, value in actual.items():
            nested = diff_json_values(value, default[key]) if key in default else clone_json_value(value)
            if nested is not None:
                diff[key] = nested
        return diff or None

    if isinstance(actual, list) and isinstance(default, list):
        return None if actual == default else clone_json_value(actual)

    return None if actual == default else clone_json_value(actual)


def merge_json_values(default: object, override: object) -> object:
    """Deep-merge *override* on top of *default*.

    Dicts are merged recursively; all other types are replaced by *override*.
    """
    if isinstance(default, dict) and isinstance(override, dict):
        merged = {key: clone_json_value(value) for key, value in default.items()}
        for key, value in override.items():
            if key in merged:
                merged[key] = merge_json_values(merged[key], value)
            else:
                merged[key] = clone_json_value(value)
        return merged

    if isinstance(override, (dict, list)):
        return clone_json_value(override)
    return override


# ---------------------------------------------------------------------------
# Semantics-based compaction helpers (used by PythonQuestion)
# ---------------------------------------------------------------------------

def default_from_semantics_field(field: dict[str, object]) -> object:
    """Derive the default value for a single H5P semantics field descriptor."""
    field_type = str(field.get("type") or "")
    if field_type == "group":
        defaults: dict[str, object] = {}
        for child in field.get("fields", []) or []:
            if not isinstance(child, dict):
                continue
            child_name = str(child.get("name") or "").strip()
            if not child_name:
                continue
            defaults[child_name] = default_from_semantics_field(child)
        return defaults
    if field_type == "list":
        if "default" in field:
            return clone_json_value(field["default"])
        return []
    if "default" in field:
        return clone_json_value(field["default"])
    if field_type in {"text", "code", "select"}:
        return ""
    if field_type == "boolean":
        return False
    if field_type == "number":
        return 0
    return None


def default_object_from_semantics(fields: list[dict[str, object]]) -> dict[str, object]:
    """Build a default content object from a list of H5P semantics field descriptors."""
    defaults: dict[str, object] = {}
    for field in fields:
        name = str(field.get("name") or "").strip()
        if not name:
            continue
        defaults[name] = default_from_semantics_field(field)
    return defaults


def compact_by_semantics(value: object, field: dict[str, object]) -> object | None:
    """Return a compacted version of *value* for the given semantics *field*,
    or ``None`` if *value* equals the semantic default (i.e. can be omitted).
    """
    field_type = str(field.get("type") or "")

    if field_type == "group":
        if not isinstance(value, dict):
            return clone_json_value(value)
        children = {
            str(child.get("name") or "").strip(): child
            for child in field.get("fields", []) or []
            if isinstance(child, dict) and str(child.get("name") or "").strip()
        }
        compacted: dict[str, object] = {}
        for key, child_value in value.items():
            child_field = children.get(key)
            if child_field is None:
                compacted[key] = clone_json_value(child_value)
                continue
            child_compacted = compact_by_semantics(child_value, child_field)
            if child_compacted is not None:
                compacted[key] = child_compacted
        return compacted or None

    if field_type == "list":
        if not isinstance(value, list):
            return clone_json_value(value)
        item_field = field.get("field")
        compacted_items: list[object] = []
        for item in value:
            if isinstance(item_field, dict):
                compacted_item = compact_by_semantics(item, item_field)
            else:
                compacted_item = clone_json_value(item)
            if compacted_item in (None, {}, []):
                continue
            compacted_items.append(compacted_item)
        return compacted_items or None

    default_value = default_from_semantics_field(field)
    if value == default_value:
        return None
    if value in (None, "", [], {}) and default_value in (None, "", [], {}):
        return None
    return clone_json_value(value)


# ---------------------------------------------------------------------------
# MDX / JSX rendering helpers
# ---------------------------------------------------------------------------

def normalize_whitespace(value: str) -> str:
    """Dedent and strip a multiline string."""
    return textwrap.dedent(value).strip()


def unescape_display_value(value: object) -> object:
    """Recursively HTML-unescape strings inside a JSON-like structure."""
    if isinstance(value, dict):
        return {key: unescape_display_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [unescape_display_value(item) for item in value]
    if isinstance(value, str):
        return html.unescape(value)
    return value


def render_template_literal(value: str, *, indent: int = 0) -> str:
    """Render *value* as a JS template literal (backtick string)."""
    escaped = value.replace("\\", "\\\\").replace("`", "\\`").replace("${", "\\${")
    if "\n" in escaped:
        indented_lines = "\n".join(
            ((" " * indent) + line) if line else ""
            for line in escaped.split("\n")
        )
        separator = "" if escaped.endswith("\n") else "\n"
        return "`\n" + indented_lines + separator + (" " * indent) + "`"
    return f"`{escaped}`"


def render_jsx_value(value: object, *, indent: int = 0) -> str:
    """Serialise *value* as a JSX expression (JS object/array literal or string)."""
    if isinstance(value, dict):
        if not value:
            return "{}"
        child_indent = indent + 2
        lines = ["{"]
        items = list(value.items())
        for index, (key, item) in enumerate(items):
            suffix = "," if index < len(items) - 1 else ""
            rendered_item = render_jsx_value(item, indent=child_indent)
            if "\n" in rendered_item:
                rendered_lines = rendered_item.splitlines()
                lines.append(
                    " " * child_indent + json.dumps(key, ensure_ascii=False) + ": " + rendered_lines[0]
                )
                for rendered_line in rendered_lines[1:]:
                    lines.append(rendered_line)
                lines[-1] += suffix
            else:
                lines.append(
                    " " * child_indent + json.dumps(key, ensure_ascii=False) + ": " + rendered_item + suffix
                )
        lines.append(" " * indent + "}")
        return "\n".join(lines)

    if isinstance(value, list):
        if not value:
            return "[]"
        child_indent = indent + 2
        lines = ["["]
        for index, item in enumerate(value):
            suffix = "," if index < len(value) - 1 else ""
            rendered_item = render_jsx_value(item, indent=child_indent)
            rendered_lines = rendered_item.splitlines()
            lines.append(" " * child_indent + rendered_lines[0])
            for rendered_line in rendered_lines[1:]:
                lines.append(rendered_line)
            lines[-1] += suffix
        lines.append(" " * indent + "]")
        return "\n".join(lines)

    if isinstance(value, str):
        display_value = html.unescape(value)
        if "\n" in display_value or '"' in display_value:
            return render_template_literal(display_value, indent=indent)
        return json.dumps(display_value, ensure_ascii=False)

    return json.dumps(value, ensure_ascii=False)


def render_tag_attribute(name: str, value: str) -> str:
    """Render a JSX attribute as ``  name="value"`` or ``  name={`...`}``."""
    if "\n" in value or '"' in value:
        return f"  {name}={{{render_template_literal(value, indent=2)}}}"
    return f'  {name}="{html.escape(value, quote=True)}"'
