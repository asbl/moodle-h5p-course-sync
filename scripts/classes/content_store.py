from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Protocol

import yaml


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
        return yaml.safe_dump(payload, allow_unicode=True, sort_keys=False, default_flow_style=False)


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
                return payload
        raise FileNotFoundError(f"Kein H5P-Content in {source_dir} gefunden.")

    def write_h5p_content_files(self, target_dir: Path, payload: dict[str, object]) -> None:
        self.write_object(target_dir / "content.yml", payload)
        legacy_json_path = target_dir / "content.json"
        if legacy_json_path.exists():
            legacy_json_path.unlink()
