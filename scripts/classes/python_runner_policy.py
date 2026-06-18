from __future__ import annotations

import re


DEFAULT_PYTHON_RUNNER = "skulpt"
MINIWORLDS_PYTHON_RUNNER = "pyodide"
MINIWORLDS_PACKAGE = "miniworlds"
MINIWORLDS_EXTRA_PACKAGES: tuple[str, ...] = ()
GRAPHICS_IMPORT_RE = re.compile(r"^\s*(?:import|from)\s+(?:p5|turtle)\b", re.MULTILINE)
MINIWORLDS_IMPORT_RE = re.compile(r"^\s*(?:import|from)\s+miniworlds\b", re.MULTILINE)


def _package_name(pkg: str | dict[str, object]) -> str:
    """Return the normalised package name from a string or H5P package-object."""
    if isinstance(pkg, dict):
        return str(pkg.get("package") or pkg.get("name") or "").strip().lower()
    return str(pkg).strip().lower()


def contains_p5_or_turtle_import(source: str) -> bool:
    return bool(GRAPHICS_IMPORT_RE.search(source))


def contains_miniworlds_import(source: str) -> bool:
    return bool(MINIWORLDS_IMPORT_RE.search(source))


def contains_miniworlds_package(packages: list[str | dict[str, object]] | tuple[str | dict[str, object], ...]) -> bool:
    return any(_package_name(p) == MINIWORLDS_PACKAGE for p in packages)


def ensure_miniworlds_packages(
    packages: list[str] | tuple[str, ...],
    *,
    source: str = "",
) -> list[str | dict[str, object]]:
    """Return packages suitable for H5P PythonQuestion pyodideOptions.packages.

    miniworlds is emitted as ``{"package": "miniworlds", "remote": false}`` so that
    the PythonRunner loads it from the locally-bundled path instead of trying to
    fetch it from the Pyodide CDN. sqlite3 is no longer auto-added.
    """
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
        normalized.append({"package": MINIWORLDS_PACKAGE, "remote": False})
        seen.add(MINIWORLDS_PACKAGE)
    for package in MINIWORLDS_EXTRA_PACKAGES:
        if package not in seen:
            normalized.append(package)
            seen.add(package)
    return normalized


def packages_for_h5p_content(
    packages: list[str | dict[str, object]],
) -> list[str | dict[str, object]]:
    """Convert all ``"miniworlds"`` strings in *packages* to the H5P object form.

    H5P PythonQuestion requires miniworlds as
    ``{"package": "miniworlds", "remote": false}`` so that the runner uses the
    locally-bundled version instead of attempting to fetch it from the CDN.
    Deduplication is applied so that only one entry per package name remains.
    """
    result: list[str | dict[str, object]] = []
    seen: set[str] = set()
    for pkg in packages:
        name = _package_name(pkg)
        if name == MINIWORLDS_PACKAGE:
            if MINIWORLDS_PACKAGE not in seen:
                result.append({"package": MINIWORLDS_PACKAGE, "remote": False})
                seen.add(MINIWORLDS_PACKAGE)
        elif name and name not in seen:
            result.append(pkg)
            seen.add(name)
    return result


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
