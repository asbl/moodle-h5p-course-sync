from __future__ import annotations

import shlex
import shutil
import sys
from pathlib import Path

from invoke import task


ROOT_DIR = Path(__file__).resolve().parent
PYTHON = sys.executable
COURSE_SYNC = ROOT_DIR / "scripts" / "main.py"


def _quote(value: str | Path) -> str:
    return shlex.quote(str(value))


def _run_python(ctx, *args: str) -> None:
    command = " ".join([_quote(PYTHON), *(_quote(arg) for arg in args)])
    ctx.run(command)


def _remove_path(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


@task
def test(ctx) -> None:
    """Run the test suite."""
    _run_python(ctx, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py")


@task(optional=["course"])
def sync(ctx, course: str = "python-2026") -> None:
    """Generate H5P output for a course."""
    _run_python(ctx, str(COURSE_SYNC), "sync", course)


@task(optional=["base_url", "token"])
def import_moodle(ctx, course: str, remote_course_id: int, base_url: str = "", token: str = "") -> None:
    """Import a Moodle course into the local MDX structure."""
    args = [str(COURSE_SYNC), "import-moodle", course, str(remote_course_id)]
    if base_url:
        args.extend(["--base-url", base_url])
    if token:
        args.extend(["--token", token])
    _run_python(ctx, *args)


@task(name="moodle-ping", optional=["base_url", "token"])
def moodle_ping(ctx, base_url: str = "", token: str = "") -> None:
    """Verify that the configured Moodle webservice connection works."""
    args = [str(COURSE_SYNC), "moodle-ping"]
    if base_url:
        args.extend(["--base-url", base_url])
    if token:
        args.extend(["--token", token])
    _run_python(ctx, *args)


@task(optional=["course"])
def status(ctx, course: str = "python-2026") -> None:
    """Show the local Moodle sync status for a course."""
    _run_python(ctx, str(COURSE_SYNC), "status", course)


@task(optional=["port"])
def serve(ctx, port: int = 8765) -> None:
    """Start the local preview server."""
    print(f"Lokale Vorschau: http://127.0.0.1:{port}")
    _run_python(ctx, str(COURSE_SYNC), "serve", "--port", str(port))


@task
def clean(ctx) -> None:
    """Remove generated course output and temporary local folders."""
    for target in [ROOT_DIR / "content", ROOT_DIR / "temp", ROOT_DIR / "uploads"]:
        _remove_path(target)

    for cache_dir in ROOT_DIR.glob("**/__pycache__"):
        _remove_path(cache_dir)

    courses_dir = ROOT_DIR / "courses"
    for h5p_dir in courses_dir.glob("*/h5p"):
        _remove_path(h5p_dir)


@task(name="clean-runtime")
def clean_runtime(ctx) -> None:
    """Remove the bootstrapped local H5P runtime."""
    _remove_path(ROOT_DIR / ".h5p-runtime")


@task
def smoke(ctx, course: str = "python-2026") -> None:
    """Run a quick local verification of tests and sync."""
    test(ctx)
    sync(ctx, course=course)