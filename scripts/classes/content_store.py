from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Protocol

import yaml
from yaml.representer import SafeRepresenter


class _BacktickLiteral(str):
    """Marker string type to force YAML literal block output."""


class _ContentYamlDumper(yaml.SafeDumper):
    pass


def _represent_backtick_literal(dumper: yaml.Dumper, value: _BacktickLiteral) -> yaml.ScalarNode:
    return dumper.represent_scalar("tag:yaml.org,2002:str", str(value), style="|")


def _represent_string_with_backtick_blocks(dumper: yaml.Dumper, value: str) -> yaml.ScalarNode:
    if len(value) >= 2 and value.startswith("`") and value.endswith("`"):
        return dumper.represent_scalar("tag:yaml.org,2002:str", value, style="|")
    return SafeRepresenter.represent_str(dumper, value)


_ContentYamlDumper.add_representer(_BacktickLiteral, _represent_backtick_literal)
_ContentYamlDumper.add_representer(str, _represent_string_with_backtick_blocks)


DEFAULT_CONTENT_ITEM_OPTIONS: dict[str, object] = {
    "showEditor": True,
    "enableImageUploads": False,
    "defaultImages": [{}],
    "enableSoundUploads": False,
    "sourceFiles": [{"visibleToLearner": True, "learnerEditable": True}],
    "allowAddingFiles": False,
    "editorMode": "code",
}

DEFAULT_EDITOR_OPTIONS: dict[str, object] = {
    "enableImageUploads": False,
    "defaultImages": [{}],
    "enableSoundUploads": False,
    "sourceFiles": [{"code": "", "visibleToLearner": True, "learnerEditable": True}],
    "allowAddingFiles": False,
    "editorMode": "code",
}

DEFAULT_BLOCKLY_CATEGORIES: dict[str, object] = {
    "variables": True,
    "logic": True,
    "loops": True,
    "math": True,
    "text": True,
    "lists": True,
    "functions": True,
}


class ContentFormatStrategy(Protocol):
    suffixes: tuple[str, ...]

    def load(self, path: Path) -> object: ...

    def dump(self, payload: object) -> str: ...


class JsonFormatStrategy:
    suffixes = (".json",)

    def load(self, path: Path) -> object:
        return json.loads(path.read_text(encoding="utf-8"))

    def dump(self, payload: object) -> str:
        return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


class YamlFormatStrategy:
    suffixes = (".yml", ".yaml")

    def load(self, path: Path) -> object:
        return yaml.safe_load(path.read_text(encoding="utf-8"))

    def dump(self, payload: object) -> str:
        return yaml.dump(
            payload,
            Dumper=_ContentYamlDumper,
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
        )


class ContentStore:
    """Repository-like storage abstraction for H5P content payloads.

    Applies a Strategy pattern so JSON/YAML formats can be loaded and dumped
    through one unified API.
    """

    def __init__(self) -> None:
        self._strategies: list[ContentFormatStrategy] = [YamlFormatStrategy(), JsonFormatStrategy()]

    def _strategy_for_suffix(self, suffix: str) -> ContentFormatStrategy:
        for strategy in self._strategies:
            if suffix.lower() in strategy.suffixes:
                return strategy
        raise ValueError(f"Unbekanntes Content-Format: {suffix}")

    def read_object(self, path: Path) -> object:
        strategy = self._strategy_for_suffix(path.suffix)
        return strategy.load(path)

    def read_yaml(self, path: Path) -> object:
        return self.read_object(path)

    def write_object(self, path: Path, payload: object) -> None:
        strategy = self._strategy_for_suffix(path.suffix)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.tmp")
        tmp.write_text(strategy.dump(payload), encoding="utf-8")
        os.replace(tmp, path)

    def write_yaml(self, path: Path, payload: object) -> None:
        self.write_object(path, payload)

    def read_h5p_content_payload(self, source_dir: Path) -> dict[str, object]:
        candidates = [
            source_dir / "content.yml",
            source_dir / "content.yaml",
            source_dir / "content.json",
            source_dir / "content" / "content.json",
        ]
        for candidate in candidates:
            if candidate.exists():
                payload = self.read_object(candidate)
                if not isinstance(payload, dict):
                    raise ValueError(f"H5P-Content '{candidate}' muss ein Objekt sein.")
                if candidate.suffix.lower() in {".yml", ".yaml"}:
                    payload = self._expand_compact_defaults(payload)
                    payload = self._unwrap_backtick_templates(payload)
                return payload
        raise FileNotFoundError(f"Kein H5P-Content in {source_dir} gefunden.")

    def write_h5p_content_files(self, target_dir: Path, payload: dict[str, object]) -> None:
        compact_payload = self._strip_redundant_defaults(payload)
        formatted_payload = self._wrap_long_text_fields(compact_payload)
        self.write_object(target_dir / "content.yml", formatted_payload)
        legacy_json_path = target_dir / "content.json"
        if legacy_json_path.exists():
            legacy_json_path.unlink()

    def _wrap_long_text_fields(self, payload: dict[str, object]) -> dict[str, object]:
        wrapped = deepcopy(payload)

        def visit(node: object, *, parent_key: str | None = None) -> object:
            if isinstance(node, dict):
                for key, value in list(node.items()):
                    node[key] = visit(value, parent_key=key)
                return node
            if isinstance(node, list):
                for index, item in enumerate(node):
                    node[index] = visit(item, parent_key=parent_key)
                return node
            if isinstance(node, str) and parent_key in {"text", "code"}:
                normalized = node.replace("\\r\\n", "\n").replace("\\n", "\n")
                if "\n" in normalized or len(normalized) >= 120:
                    return _BacktickLiteral(f"`{normalized}`")
                return normalized
            return node

        return visit(wrapped)  # type: ignore[return-value]

    def _unwrap_backtick_templates(self, payload: dict[str, object]) -> dict[str, object]:
        unwrapped = deepcopy(payload)

        def visit(node: object, *, parent_key: str | None = None) -> object:
            if isinstance(node, dict):
                for key, value in list(node.items()):
                    node[key] = visit(value, parent_key=key)
                return node
            if isinstance(node, list):
                for index, item in enumerate(node):
                    node[index] = visit(item, parent_key=parent_key)
                return node
            if isinstance(node, str) and parent_key in {"text", "code"}:
                if len(node) >= 2 and node.startswith("`") and node.endswith("`"):
                    return node[1:-1]
            return node

        return visit(unwrapped)  # type: ignore[return-value]

    def _strip_redundant_defaults(self, payload: dict[str, object]) -> dict[str, object]:
        compact = deepcopy(payload)
        contents = compact.get("contents")
        if isinstance(contents, list):
            for item in contents:
                if not isinstance(item, dict):
                    continue
                options = item.get("options")
                if isinstance(options, dict):
                    self._strip_default_values(options, DEFAULT_CONTENT_ITEM_OPTIONS)
                    if not options:
                        item.pop("options", None)
                blockly = item.get("blocklyCategories")
                if isinstance(blockly, dict):
                    self._strip_default_values(blockly, DEFAULT_BLOCKLY_CATEGORIES)
                    if not blockly:
                        item.pop("blocklyCategories", None)

        editor = compact.get("editorSettings")
        if isinstance(editor, dict):
            editor_options = editor.get("options")
            if isinstance(editor_options, dict):
                self._strip_default_values(editor_options, DEFAULT_EDITOR_OPTIONS)
                if not editor_options:
                    editor.pop("options", None)
            editor_blockly = editor.get("blocklyCategories")
            if isinstance(editor_blockly, dict):
                self._strip_default_values(editor_blockly, DEFAULT_BLOCKLY_CATEGORIES)
                if not editor_blockly:
                    editor.pop("blocklyCategories", None)

        return compact

    def _expand_compact_defaults(self, payload: dict[str, object]) -> dict[str, object]:
        expanded = deepcopy(payload)
        contents = expanded.get("contents")
        if isinstance(contents, list):
            for item in contents:
                if not isinstance(item, dict):
                    continue
                options = item.get("options")
                if not isinstance(options, dict):
                    options = {}
                    item["options"] = options
                self._apply_default_values(options, DEFAULT_CONTENT_ITEM_OPTIONS)

                blockly = item.get("blocklyCategories")
                if not isinstance(blockly, dict):
                    blockly = {}
                    item["blocklyCategories"] = blockly
                self._apply_default_values(blockly, DEFAULT_BLOCKLY_CATEGORIES)

        editor = expanded.get("editorSettings")
        if isinstance(editor, dict):
            editor_options = editor.get("options")
            if not isinstance(editor_options, dict):
                editor_options = {}
                editor["options"] = editor_options
            self._apply_default_values(editor_options, DEFAULT_EDITOR_OPTIONS)

            editor_blockly = editor.get("blocklyCategories")
            if not isinstance(editor_blockly, dict):
                editor_blockly = {}
                editor["blocklyCategories"] = editor_blockly
            self._apply_default_values(editor_blockly, DEFAULT_BLOCKLY_CATEGORIES)

        return expanded

    def _strip_default_values(self, target: dict[str, object], defaults: dict[str, object]) -> None:
        for key, default_value in defaults.items():
            if key in target and target[key] == default_value:
                target.pop(key, None)

    def _apply_default_values(self, target: dict[str, object], defaults: dict[str, object]) -> None:
        for key, default_value in defaults.items():
            target.setdefault(key, deepcopy(default_value))
