from __future__ import annotations

import re


DEFAULT_PYTHON_RUNNER = "skulpt"
MINIWORLDS_PYTHON_RUNNER = "pyodide"
MINIWORLDS_PACKAGE = "miniworlds"
MINIWORLDS_EXTRA_PACKAGES = ("sqlite3",)
GRAPHICS_IMPORT_RE = re.compile(r"^\s*(?:import|from)\s+(?:p5|turtle)\b", re.MULTILINE)
MINIWORLDS_IMPORT_RE = re.compile(r"^\s*(?:import|from)\s+miniworlds\b", re.MULTILINE)


def contains_p5_or_turtle_import(source: str) -> bool:
    return bool(GRAPHICS_IMPORT_RE.search(source))


def contains_miniworlds_import(source: str) -> bool:
    return bool(MINIWORLDS_IMPORT_RE.search(source))


def contains_miniworlds_package(packages: list[str] | tuple[str, ...]) -> bool:
    return any(package.strip().lower() == MINIWORLDS_PACKAGE for package in packages)


def ensure_miniworlds_packages(
    packages: list[str] | tuple[str, ...],
    *,
    source: str = "",
) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for package in packages:
        name = str(package).strip()
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        normalized.append(name)
        seen.add(key)

    if not (contains_miniworlds_package(normalized) or contains_miniworlds_import(source)):
        return normalized

    if MINIWORLDS_PACKAGE not in seen:
        normalized.append(MINIWORLDS_PACKAGE)
        seen.add(MINIWORLDS_PACKAGE)
    for package in MINIWORLDS_EXTRA_PACKAGES:
        if package not in seen:
            normalized.append(package)
            seen.add(package)
    return normalized


def resolve_python_runner(
    runner: object = "",
    *,
    packages: list[str] | tuple[str, ...] = (),
    source: str = "",
) -> str:
    explicit_runner = str(runner or "").strip()
    if explicit_runner:
        return explicit_runner
    if contains_miniworlds_package(packages) or contains_miniworlds_import(source):
        return MINIWORLDS_PYTHON_RUNNER
    return DEFAULT_PYTHON_RUNNER


def validate_graphics_runner(*, runner: str, source: str, location: str) -> None:
    if not contains_p5_or_turtle_import(source):
        return
    if runner.strip() == "skulpt":
        return
    raise ValueError(
        f"{location}: Inhalte mit p5 oder turtle muessen pythonRunner: skulpt verwenden "
        f"(gefunden: {runner or 'leer'})."
    )
