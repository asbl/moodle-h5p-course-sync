from __future__ import annotations

import html
import json
import mimetypes
import os
import re
from copy import deepcopy
from io import StringIO
from pathlib import Path
from typing import Protocol

from ruamel.yaml import YAML
from ruamel.yaml.scalarstring import LiteralScalarString

from .python_runner_policy import (
    contains_miniworlds_import,
    contains_miniworlds_package,
    ensure_miniworlds_packages,
    resolve_python_runner,
    validate_graphics_runner,
)


DEFAULT_CONTENT_ITEM_OPTIONS: dict[str, object] = {
    "showEditor": True,
    "enableImageUploads": False,
    "defaultImages": [],
    "enableSoundUploads": False,
    "sourceFiles": [],
    "allowAddingFiles": False,
    "editorMode": "code",
}

DEFAULT_EDITOR_OPTIONS: dict[str, object] = {
    "enableImageUploads": False,
    "defaultImages": [],
    "enableSoundUploads": False,
    "sourceFiles": [],
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

DEFAULT_ADVANCED_OPTIONS: dict[str, object] = {
    "showConsole": True,
    "disableOutputPopups": False,
    "enableSaveLoadButtons": True,
    "execLimit": 0,
    "blocklyCdnUrl": "",
    "codeMirrorCdnUrl": "",
    "markdownCdnUrl": "",
    "fontAwesomeCdnUrl": "",
    "sweetAlertCdnUrl": "",
    "jsZipCdnUrl": "",
    "p5CdnUrl": "",
    "skulptCdnUrl": "",
    "sqlJsUrl": "",
}

DEFAULT_PYODIDE_OPTIONS: dict[str, object] = {
    "pyodideCdnUrl": "",
}
SPLIT_DEFAULTS_MARKER = "__course_sync_split_defaults"

LITERAL_TEXT_FIELDS = {"text", "instructions"}
LITERAL_CODE_FIELDS = {"code", "startingCode", "preCode", "postCode", "targetCode"}
LITERAL_FIELDS = LITERAL_TEXT_FIELDS | LITERAL_CODE_FIELDS


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

    def __init__(self) -> None:
        self._yaml_reader = YAML(typ="safe")
        self._yaml_writer = YAML()
        self._yaml_writer.default_flow_style = False
        self._yaml_writer.allow_unicode = True
        self._yaml_writer.sort_base_mapping_type_on_output = False

    def load(self, path: Path) -> object:
        return self._yaml_reader.load(path.read_text(encoding="utf-8"))

    def load_text(self, source: str) -> object:
        return self._yaml_reader.load(source)

    def dump(self, payload: object) -> str:
        stream = StringIO()
        self._yaml_writer.dump(self._to_literal_scalars(payload), stream)
        text = stream.getvalue()
        if not text.endswith("\n"):
            text += "\n"
        return text

    def _to_literal_scalars(self, node: object) -> object:
        if isinstance(node, dict):
            return {key: self._to_literal_scalars(value) for key, value in node.items()}
        if isinstance(node, list):
            return [self._to_literal_scalars(item) for item in node]
        if isinstance(node, str) and len(node) >= 2 and node.startswith("`") and node.endswith("`"):
            return LiteralScalarString(node)
        return node


class ContentStore:
    """Repository-like storage abstraction for H5P content payloads.

    Applies a Strategy pattern so JSON/YAML formats can be loaded and dumped
    through one unified API.
    """

    _IMAGE_RE = re.compile(r"^\s*!\[(?P<alt>[^\]]*)\]\((?P<path>[^)]+)\)\s*$")

    def __init__(self) -> None:
        self._yaml = YamlFormatStrategy()
        self._strategies: list[ContentFormatStrategy] = [self._yaml, JsonFormatStrategy()]

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
        mdx_path = source_dir / "content.mdx"
        if mdx_path.exists():
            payload = self._read_h5p_content_mdx(source_dir, mdx_path)
            payload = self._expand_compact_defaults(payload, source_dir=source_dir)
            self._validate_python_question_payload(source_dir, payload)
            return payload

        candidates = [
            source_dir / "settings.yml",
            source_dir / "settings.yaml",
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
                    payload = self._expand_compact_defaults(payload, source_dir=candidate.parent)
                    payload = self._unwrap_backtick_templates(payload)
                self._validate_python_question_payload(candidate, payload)
                return payload
        raise FileNotFoundError(f"Kein H5P-Content in {source_dir} gefunden.")

    def write_h5p_content_files(self, target_dir: Path, payload: dict[str, object]) -> None:
        if self._should_write_split_content_files(payload):
            settings_payload, mdx_source = self._split_python_question_payload_for_mdx(payload)
            self.write_object(target_dir / "settings.yml", settings_payload)
            self._write_text(target_dir / "content.mdx", mdx_source)
            for legacy_path in [
                target_dir / "content.yml",
                target_dir / "content.yaml",
                target_dir / "content.json",
            ]:
                if legacy_path.exists():
                    legacy_path.unlink()
            return

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
            if isinstance(node, str) and parent_key in LITERAL_FIELDS:
                normalized = html.unescape(node).replace("\r\n", "\n").replace("\r", "\n").replace("\\n", "\n")
                if parent_key in LITERAL_CODE_FIELDS or "\n" in normalized or len(normalized) >= 120:
                    return f"`{normalized}`"
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
            if isinstance(node, str) and parent_key in LITERAL_FIELDS:
                if len(node) >= 2 and node.startswith("`") and node.endswith("`"):
                    return html.unescape(node[1:-1])
                return html.unescape(node)
            return node

        return visit(unwrapped)  # type: ignore[return-value]

    def _strip_redundant_defaults(self, payload: dict[str, object]) -> dict[str, object]:
        compact = deepcopy(payload)
        uses_python_defaults = self._uses_python_question_defaults(compact)
        contents = compact.get("contents")
        if isinstance(contents, list):
            for item in contents:
                if not isinstance(item, dict):
                    continue
                if not self._is_code_content_item(item):
                    item.pop("options", None)
                    item.pop("blocklyCategories", None)
                    continue
                self._strip_option_group(item, "options", DEFAULT_CONTENT_ITEM_OPTIONS)
                self._strip_option_group(item, "blocklyCategories", DEFAULT_BLOCKLY_CATEGORIES)

        editor = compact.get("editorSettings")
        if isinstance(editor, dict):
            self._strip_option_group(editor, "options", DEFAULT_EDITOR_OPTIONS)
            self._strip_option_group(editor, "blocklyCategories", DEFAULT_BLOCKLY_CATEGORIES)

        if uses_python_defaults:
            self._strip_option_group(compact, "advancedOptions", DEFAULT_ADVANCED_OPTIONS)
            self._strip_option_group(compact, "pyodideOptions", DEFAULT_PYODIDE_OPTIONS)

        return compact

    def _expand_compact_defaults(self, payload: dict[str, object], *, source_dir: Path | None = None) -> dict[str, object]:
        expanded = deepcopy(payload)
        uses_python_defaults = self._uses_python_question_defaults(expanded)
        contents = expanded.get("contents")
        if isinstance(contents, list):
            for item in contents:
                if not isinstance(item, dict):
                    continue
                if not self._is_code_content_item(item):
                    item.pop("options", None)
                    item.pop("blocklyCategories", None)
                    continue
                self._apply_option_group(item, "options", DEFAULT_CONTENT_ITEM_OPTIONS)
                self._apply_option_group(item, "blocklyCategories", DEFAULT_BLOCKLY_CATEGORIES)

        editor = expanded.get("editorSettings")
        if isinstance(editor, dict):
            self._apply_option_group(editor, "options", DEFAULT_EDITOR_OPTIONS)
            self._apply_option_group(editor, "blocklyCategories", DEFAULT_BLOCKLY_CATEGORIES)

        if uses_python_defaults:
            payload_source = "\n".join(self._iter_payload_strings(expanded))
            if self._is_python_question_payload(expanded):
                expanded["pythonRunner"] = resolve_python_runner(
                    expanded.get("pythonRunner", ""),
                    packages=self._extract_pyodide_packages(expanded),
                    source=payload_source,
                )
            self._apply_option_group(expanded, "advancedOptions", DEFAULT_ADVANCED_OPTIONS)
            self._apply_option_group(expanded, "pyodideOptions", DEFAULT_PYODIDE_OPTIONS)
            packages = ensure_miniworlds_packages(
                self._extract_pyodide_packages(expanded),
                source=payload_source,
            )
            pyodide_options = expanded.get("pyodideOptions")
            if isinstance(pyodide_options, dict):
                pyodide_options["packages"] = packages
            self._apply_miniworlds_editor_defaults(expanded, payload_source, source_dir=source_dir)

        expanded.pop(SPLIT_DEFAULTS_MARKER, None)
        return expanded

    def _is_code_content_item(self, item: dict[str, object]) -> bool:
        return str(item.get("type") or "") == "code"

    def _apply_miniworlds_editor_defaults(
        self,
        payload: dict[str, object],
        payload_source: str,
        *,
        source_dir: Path | None = None,
    ) -> None:
        if not (
            contains_miniworlds_package(self._extract_pyodide_packages(payload))
            or contains_miniworlds_import(payload_source)
        ):
            return

        default_images = self._collect_default_images(payload, source_dir=source_dir)
        contents = payload.get("contents")
        if isinstance(contents, list):
            for item in contents:
                if not isinstance(item, dict) or str(item.get("type") or "") != "code":
                    continue
                options = item.get("options")
                if not isinstance(options, dict):
                    options = {}
                    item["options"] = options
                self._apply_miniworlds_options(options, default_images)

        editor = payload.get("editorSettings")
        if isinstance(editor, dict):
            options = editor.get("options")
            if not isinstance(options, dict):
                options = {}
                editor["options"] = options
            self._apply_miniworlds_options(options, default_images)

    def _apply_miniworlds_options(self, options: dict[str, object], default_images: list[dict[str, object]]) -> None:
        options["enableImageUploads"] = True
        options["allowAddingFiles"] = True
        if options.get("sourceFiles") == DEFAULT_EDITOR_OPTIONS["sourceFiles"] or options.get("sourceFiles") == DEFAULT_CONTENT_ITEM_OPTIONS["sourceFiles"]:
            options["sourceFiles"] = []
        else:
            options.setdefault("sourceFiles", [])
        if default_images and self._is_empty_default_images(options.get("defaultImages")):
            options["defaultImages"] = deepcopy(default_images)

    def _is_empty_default_images(self, value: object) -> bool:
        return value in (None, [], [{}])

    def _collect_default_images(self, payload: dict[str, object], *, source_dir: Path | None = None) -> list[dict[str, object]]:
        contents = payload.get("contents")
        images: list[dict[str, object]] = []
        seen: set[str] = set()
        if isinstance(contents, list):
            for item in contents:
                if not isinstance(item, dict) or item.get("type") != "image":
                    continue
                image = item.get("image")
                if isinstance(image, dict):
                    self._append_default_image(images, seen, str(image.get("path") or ""), image=image)

        if source_dir is not None:
            images_dir = source_dir / "images"
            if images_dir.exists():
                for image_path in sorted(path for path in images_dir.iterdir() if path.is_file()):
                    self._append_default_image(images, seen, f"images/{image_path.name}")
        return images

    def _append_default_image(
        self,
        images: list[dict[str, object]],
        seen: set[str],
        image_path: str,
        *,
        image: dict[str, object] | None = None,
    ) -> None:
        normalized_path = image_path.strip()
        if not normalized_path or normalized_path in seen:
            return
        seen.add(normalized_path)
        image_payload = deepcopy(image) if image is not None else {
            "path": normalized_path,
            "mime": mimetypes.guess_type(normalized_path)[0] or "application/octet-stream",
            "copyright": {"license": "U"},
        }
        images.append(
            {
                "image": image_payload,
                "fileName": Path(normalized_path).name,
            }
        )

    def _is_python_question_payload(self, payload: dict[str, object]) -> bool:
        # A payload is a Python question when it has an explicit pythonRunner field.
        # contentType alone is not sufficient since JavaQuestion also uses ide_only/text_only.
        runner = payload.get("pythonRunner")
        return isinstance(runner, str) and bool(runner.strip())

    def _uses_python_question_defaults(self, payload: dict[str, object]) -> bool:
        return (
            self._is_python_question_payload(payload)
            or isinstance(payload.get("pyodideOptions"), dict)
            or payload.get(SPLIT_DEFAULTS_MARKER) is True
        )

    def _should_write_split_content_files(self, payload: dict[str, object]) -> bool:
        content_type = str(payload.get("contentType") or "")
        if content_type not in {"ide_only", "text_only"}:
            return False
        return any(
            key in payload
            for key in [
                "pythonRunner",
                "editorSettings",
                "gradingSettings",
                "advancedOptions",
                "pyodideOptions",
            ]
        )

    def _extract_pyodide_packages(self, payload: dict[str, object]) -> list[str]:
        pyodide_options = payload.get("pyodideOptions")
        if not isinstance(pyodide_options, dict):
            return []
        packages = pyodide_options.get("packages")
        if not isinstance(packages, list):
            return []

        names: list[str] = []
        for package in packages:
            if isinstance(package, str):
                names.append(package)
            elif isinstance(package, dict):
                value = package.get("package")
                if isinstance(value, str):
                    names.append(value)
        return names

    def _validate_python_question_payload(self, location: Path, payload: dict[str, object]) -> None:
        if not self._is_python_question_payload(payload):
            return
        runner = str(payload.get("pythonRunner") or "")
        validate_graphics_runner(
            runner=runner,
            source="\n".join(self._iter_payload_strings(payload)),
            location=location.as_posix(),
        )

    def _iter_payload_strings(self, node: object) -> list[str]:
        if isinstance(node, str):
            return [node]
        if isinstance(node, dict):
            values: list[str] = []
            for value in node.values():
                values.extend(self._iter_payload_strings(value))
            return values
        if isinstance(node, list):
            values = []
            for item in node:
                values.extend(self._iter_payload_strings(item))
            return values
        return []

    def _strip_default_values(self, target: dict[str, object], defaults: dict[str, object]) -> None:
        for key, default_value in defaults.items():
            if key in target and target[key] == default_value:
                target.pop(key, None)

    def _apply_default_values(self, target: dict[str, object], defaults: dict[str, object]) -> None:
        for key, default_value in defaults.items():
            target.setdefault(key, deepcopy(default_value))

    def _apply_option_group(self, container: dict[str, object], key: str, defaults: dict[str, object]) -> None:
        group = container.get(key)
        if not isinstance(group, dict):
            group = {}
            container[key] = group
        self._apply_default_values(group, defaults)

    def _strip_option_group(self, container: dict[str, object], key: str, defaults: dict[str, object]) -> None:
        group = container.get(key)
        if isinstance(group, dict):
            self._strip_default_values(group, defaults)
            if not group:
                container.pop(key, None)

    def _write_text(self, path: Path, value: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.tmp")
        tmp.write_text(value, encoding="utf-8")
        os.replace(tmp, path)

    def _read_h5p_content_mdx(self, source_dir: Path, mdx_path: Path) -> dict[str, object]:
        settings_payload: dict[str, object] = {}
        for candidate in [source_dir / "settings.yml", source_dir / "settings.yaml", source_dir / "content.yml"]:
            if not candidate.exists():
                continue
            loaded = self.read_object(candidate)
            if not isinstance(loaded, dict):
                raise ValueError(f"H5P-Settings '{candidate}' muessen ein Objekt sein.")
            settings_payload = loaded
            break

        content_payload = deepcopy(settings_payload)
        mdx_payload = self._parse_content_mdx(mdx_path.read_text(encoding="utf-8"))
        content_payload["contents"] = mdx_payload["contents"]
        if self._should_use_python_defaults_for_split_source(source_dir):
            content_payload[SPLIT_DEFAULTS_MARKER] = True
        if mdx_payload["editorSettings"]:
            editor = content_payload.get("editorSettings")
            if not isinstance(editor, dict):
                editor = {}
                content_payload["editorSettings"] = editor
            editor.update(mdx_payload["editorSettings"])
        if mdx_payload["gradingSettings"]:
            grading = content_payload.get("gradingSettings")
            if not isinstance(grading, dict):
                grading = {}
                content_payload["gradingSettings"] = grading
            grading.update(mdx_payload["gradingSettings"])
        return self._unwrap_backtick_templates(content_payload)

    def _should_use_python_defaults_for_split_source(self, source_dir: Path) -> bool:
        h5p_json = source_dir / "h5p.json"
        if not h5p_json.exists():
            return True
        try:
            payload = self.read_object(h5p_json)
        except (FileNotFoundError, ValueError, json.JSONDecodeError):
            return False
        return isinstance(payload, dict) and payload.get("mainLibrary") == "H5P.PythonQuestion"

    def _split_python_question_payload_for_mdx(self, payload: dict[str, object]) -> tuple[dict[str, object], str]:
        compact_payload = self._strip_redundant_defaults(payload)
        unwrapped = self._unwrap_backtick_templates(compact_payload)
        settings_payload = deepcopy(unwrapped)
        contents = settings_payload.pop("contents", [])
        editor_settings = settings_payload.get("editorSettings")
        grading_settings = settings_payload.get("gradingSettings")

        content_type = settings_payload.get("contentType")
        if content_type == "ide_only":
            settings_payload.pop("editorSettings", None)
            settings_payload.pop("gradingSettings", None)
        else:
            if content_type == "text_only":
                settings_payload.pop("editorSettings", None)
                settings_payload.pop("gradingSettings", None)
            editor_settings = {}
            grading_settings = {}

        mdx_source = self._format_content_mdx(
            contents if isinstance(contents, list) else [],
            editor_settings if isinstance(editor_settings, dict) else {},
            grading_settings if isinstance(grading_settings, dict) else {},
        )
        return settings_payload, mdx_source

    def _format_content_mdx(
        self,
        contents: list[object],
        editor_settings: dict[str, object],
        grading_settings: dict[str, object],
    ) -> str:
        parts: list[str] = []
        for item in contents:
            if not isinstance(item, dict):
                continue
            item_type = str(item.get("type") or "text")
            if item_type == "code":
                code = str(item.get("code") or "").rstrip()
                options = item.get("options")
                info = "python"
                if isinstance(options, dict) and options.get("showEditor") is False:
                    info = "python noeditor"
                parts.append(f"```{info}\n{code}\n```")
                continue
            if item_type == "image":
                image = item.get("image")
                if isinstance(image, dict):
                    image_path = str(image.get("path") or "").strip()
                    if image_path:
                        parts.append(f"![Bild]({image_path})")
                continue
            text = str(item.get("text") or "").strip()
            code = str(item.get("code") or "").rstrip()
            if text:
                parts.append(text)
            if code:
                parts.append(f"```python\n{code}\n```")

        if editor_settings:
            instructions = str(editor_settings.get("instructions") or "").strip()
            if instructions:
                parts.append(f"<Instructions>\n{instructions}\n</Instructions>")
            for key in ["preCode", "startingCode", "postCode"]:
                value = str(editor_settings.get(key) or "").rstrip()
                if value:
                    parts.append(f"```python editor:{key}\n{value}\n```")
            options = {
                key: value
                for key, value in editor_settings.items()
                if key not in {"instructions", "preCode", "startingCode", "postCode"}
            }
            if options:
                parts.append("```yaml editor\n" + self._dump_yaml_fragment(options) + "```")

        if grading_settings:
            target_code = str(grading_settings.get("targetCode") or "").rstrip()
            if target_code:
                parts.append(f"```python grading:targetCode\n{target_code}\n```")
            options = {key: value for key, value in grading_settings.items() if key != "targetCode"}
            if options:
                parts.append("```yaml grading\n" + self._dump_yaml_fragment(options) + "```")

        return "\n\n".join(parts).rstrip() + "\n"

    def _dump_yaml_fragment(self, payload: object) -> str:
        text = self._yaml.dump(payload)
        return text if text.endswith("\n") else text + "\n"

    def _parse_content_mdx(self, source: str) -> dict[str, object]:
        editor_settings: dict[str, object] = {}
        grading_settings: dict[str, object] = {}

        def pull_instructions(match: re.Match[str]) -> str:
            editor_settings["instructions"] = match.group("body").strip()
            return "\n\n"

        source = re.sub(
            r"<Instructions>\s*(?P<body>.*?)\s*</Instructions>",
            pull_instructions,
            source,
            flags=re.DOTALL | re.IGNORECASE,
        )

        contents: list[dict[str, object]] = []
        cursor = 0
        fence_re = re.compile(r"```(?P<info>[^\n`]*)\n(?P<body>.*?)```", re.DOTALL)
        for match in fence_re.finditer(source):
            text = source[cursor:match.start()].strip()
            if text:
                self._append_text_or_image_items(contents, text)

            info = match.group("info").strip()
            body = match.group("body")
            handled = self._handle_content_mdx_fence(info, body, editor_settings, grading_settings)
            if not handled:
                item: dict[str, object] = {"type": "code", "code": body}
                options = self._content_item_options_from_fence(info)
                if options:
                    item["options"] = options
                contents.append(item)
            cursor = match.end()

        tail = source[cursor:].strip()
        if tail:
            self._append_text_or_image_items(contents, tail)

        return {
            "contents": contents,
            "editorSettings": editor_settings,
            "gradingSettings": grading_settings,
        }

    def _append_text_or_image_items(self, contents: list[dict[str, object]], text: str) -> None:
        pending: list[str] = []
        for line in text.splitlines():
            match = self._IMAGE_RE.match(line)
            if match:
                if pending and "\n".join(pending).strip():
                    contents.append({"type": "text", "text": "\n".join(pending).strip()})
                pending = []
                image_path = match.group("path").strip()
                mime = mimetypes.guess_type(image_path)[0] or "application/octet-stream"
                contents.append(
                    {
                        "type": "image",
                        "image": {
                            "path": image_path,
                            "mime": mime,
                            "copyright": {"license": "U"},
                        },
                    }
                )
                continue
            pending.append(line)
        if pending and "\n".join(pending).strip():
            contents.append({"type": "text", "text": "\n".join(pending).strip()})

    def _handle_content_mdx_fence(
        self,
        info: str,
        body: str,
        editor_settings: dict[str, object],
        grading_settings: dict[str, object],
    ) -> bool:
        tokens = info.split()
        if not tokens:
            return False
        roles = tokens[1:] if tokens[0] in {"python", "py", "json", "yaml", "yml"} else tokens
        for role in roles:
            if role.startswith("editor:"):
                editor_settings[role.split(":", 1)[1]] = body
                return True
            if role == "editor":
                editor_settings.update(self._load_yaml_fragment(body, "editor"))
                return True
            if role.startswith("grading:"):
                grading_settings[role.split(":", 1)[1]] = body
                return True
            if role == "grading":
                grading_settings.update(self._load_yaml_fragment(body, "grading"))
                return True
        return False

    def _content_item_options_from_fence(self, info: str) -> dict[str, object]:
        tokens = {token.strip().lower() for token in info.split() if token.strip()}
        if tokens & {"noeditor", "no-editor", "no_editor", "readonly", "read-only", "output", "console"}:
            return {"showEditor": False}
        if tokens and not tokens & {"python", "py", "json", "yaml", "yml"}:
            if tokens & {"text", "txt", "plain", "plaintext"}:
                return {"showEditor": False}
        return {}

    def _load_yaml_fragment(self, source: str, description: str) -> dict[str, object]:
        payload = self._yaml.load_text(source)
        if not isinstance(payload, dict):
            raise ValueError(f"content.mdx {description}-Block muss ein YAML-Objekt sein.")
        return payload
