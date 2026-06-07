from __future__ import annotations

import json
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from invoke import task


ROOT_DIR = Path(__file__).resolve().parent
PYTHON = sys.executable
COURSE_SYNC = ROOT_DIR / "scripts" / "main.py"


def _quote(value: str | Path) -> str:
    return shlex.quote(str(value))


def _run_python(ctx, *args: str, pty: bool = False) -> None:
    del ctx, pty
    command_args = list(args)
    if command_args and command_args[0] == str(COURSE_SYNC):
        command_args = ["-m", "scripts.main", *command_args[1:]]
    subprocess.run([PYTHON, *command_args], check=True, cwd=ROOT_DIR)


def _invoke_binary(project_dir: Path) -> str:
    local_invoke = project_dir / ".venv" / "bin" / "invoke"
    if local_invoke.exists():
        return str(local_invoke)
    return shutil.which("invoke") or shutil.which("inv") or "invoke"


def _remove_path(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def _course_chapters(course: str) -> list[str]:
    index_path = ROOT_DIR / "courses" / course / "index.mdx"
    if not index_path.exists():
        raise FileNotFoundError(f"Course index not found: {index_path}")

    source = index_path.read_text(encoding="utf-8")
    chapters = [
        Path(match).stem
        for match in re.findall(r"<Chapter\b[^>]*\bsrc=[\"']\.\/chapters\/([^\"']+\.mdx)[\"']", source)
    ]
    if not chapters:
        raise RuntimeError(f"No chapters found in {index_path}")
    return chapters


def _ensure_course_sync_metadata(course: str, remote_course_id: int, moodle_base_url: str) -> None:
    metadata_path = ROOT_DIR / "courses" / course / ".course-sync.json"
    if metadata_path.exists():
        return

    metadata_path.write_text(
        json.dumps(
            {
                "version": 1,
                "course": course,
                "remoteCourseId": remote_course_id,
                "moodleBaseUrl": moodle_base_url.rstrip("/"),
                "entries": [],
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )


@task
def test(ctx) -> None:
    """Run the test suite."""
    _run_python(ctx, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py")


@task(name="pre-push-check")
def pre_push_check(ctx) -> None:
    """Run all checks that must pass before pushing."""
    test(ctx)


@task(name="install-git-hooks")
def install_git_hooks(ctx) -> None:
    """Configure Git to use the versioned hooks in .githooks/."""
    del ctx
    subprocess.run(["git", "config", "core.hooksPath", ".githooks"], check=True, cwd=ROOT_DIR)
    print("Git hooks aktiviert: .githooks")


@task(name="list-courses", optional=["verbose"])
def list_courses(ctx, verbose: bool = False) -> None:
    """List local courses."""
    args = [str(COURSE_SYNC), "list-courses"]
    if verbose:
        args.append("--verbose")
    _run_python(ctx, *args)


@task(name="new-course", optional=["title", "language", "force"])
def new_course(ctx, course: str, title: str = "", language: str = "de", force: bool = False) -> None:
    """Create a minimal new course scaffold."""
    args = [str(COURSE_SYNC), "new-course", course]
    if title:
        args.extend(["--title", title])
    if language != "de":
        args.extend(["--language", language])
    if force:
        args.append("--force")
    _run_python(ctx, *args)


@task(optional=["course"])
def sync(ctx, course: str = "python-2026") -> None:
    """Generate H5P output for a course."""
    _run_python(ctx, str(COURSE_SYNC), "sync", course)


@task(optional=["course"])
def build(ctx, course: str = "") -> None:
    """Batch-prepare H5P output and preview runtime."""
    args = [str(COURSE_SYNC), "build"]
    if course:
        args.append(course)
    _run_python(ctx, *args)


@task(name="update-h5p-libraries", optional=["tag"])
def update_h5p_libraries(ctx, tag: str = "") -> None:
    """Download the latest custom H5P libraries from GitHub into libraries/."""
    args = [str(COURSE_SYNC), "update-h5p-libraries"]
    if tag:
        args.extend(["--tag", tag])
    _run_python(ctx, *args)


@task(optional=["course", "output"])
def export_chapter(ctx, chapter: str, course: str = "python-2026", output: str = "") -> None:
    """Copy one chapter's built H5P packages into an upload folder."""
    args = [str(COURSE_SYNC), "export-chapter", course, chapter]
    if output:
        args.extend(["--output", output])
    _run_python(ctx, *args)


@task(optional=["course", "course_url", "target", "section", "username", "password", "storage_state", "headless", "timeout"])
def upload_chapter_moodle(
    ctx,
    chapter: str,
    course: str = "python-2026",
    course_url: str = "",
    target: str = "",
    section: str = "",
    username: str = "",
    password: str = "",
    storage_state: str = "",
    headless: bool = False,
    timeout: int = 30000,
) -> None:
    """Upload or update one chapter's H5P packages in Moodle via Playwright."""
    args = [str(COURSE_SYNC), "upload-chapter-moodle", course, chapter]
    if course_url:
        args.extend(["--course-url", course_url])
    if target:
        args.extend(["--target", target])
    if section:
        args.extend(["--section", section])
    if username:
        args.extend(["--username", username])
    if password:
        args.extend(["--password", password])
    if storage_state:
        args.extend(["--storage-state", storage_state])
    if headless:
        args.append("--headless")
    if timeout != 30000:
        args.extend(["--timeout", str(timeout)])
    _run_python(ctx, *args, pty=not headless)


@task(
    name="sync-h5p-demo-courses-moodle",
    optional=["german_course", "english_course", "german_course_url", "english_course_url", "timeout"],
)
def sync_h5p_demo_courses_moodle(
    ctx,
    german_course: str = "h5p-demo",
    english_course: str = "h5p-demo-en",
    german_course_url: str = "",
    english_course_url: str = "",
    headless: bool = False,
    timeout: int = 30000,
) -> None:
    """Build and upload both H5P demo courses to their configured Moodle courses."""
    _ensure_course_sync_metadata(german_course, 2, "https://www.opencoding.de")
    _ensure_course_sync_metadata(english_course, 9, "https://www.opencoding.de")

    _run_python(ctx, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py")
    for course in [german_course, english_course]:
        _run_python(ctx, str(COURSE_SYNC), "build", course)

    for course, course_url in [(german_course, german_course_url), (english_course, english_course_url)]:
        for chapter in _course_chapters(course):
            args = [str(COURSE_SYNC), "upload-chapter-moodle", course, chapter]
            if course_url:
                args.extend(["--course-url", course_url])
            if headless:
                args.append("--headless")
            if timeout != 30000:
                args.extend(["--timeout", str(timeout)])
            _run_python(ctx, *args, pty=not headless)


@task(optional=["base_url", "token"])
def import_moodle(ctx, course: str, remote_course_id: int, base_url: str = "", token: str = "") -> None:
    """Import a Moodle course into the local MDX structure."""
    args = [str(COURSE_SYNC), "import-moodle", course, str(remote_course_id)]
    if base_url:
        args.extend(["--base-url", base_url])
    if token:
        args.extend(["--token", token])
    _run_python(ctx, *args)


@task(name="import-mbz", optional=["remote_course_id", "base_url"])
def import_mbz(ctx, course: str, mbz_path: str, remote_course_id: int = 0, base_url: str = "") -> None:
    """Import a Moodle course from a local .mbz backup file (no API credentials needed)."""
    args = [str(COURSE_SYNC), "import-mbz", course, mbz_path]
    if remote_course_id:
        args.extend(["--remote-course-id", str(remote_course_id)])
    if base_url:
        args.extend(["--base-url", base_url])
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
    for build_dir in courses_dir.glob("*/build"):
        _remove_path(build_dir)


@task(name="clean-runtime")
def clean_runtime(ctx) -> None:
    """Remove the bootstrapped local H5P runtime."""
    _remove_path(ROOT_DIR / ".h5p-runtime")


@task
def smoke(ctx, course: str = "python-2026") -> None:
    """Run a quick local verification of tests and sync."""
    test(ctx)
    sync(ctx, course=course)


@task(
    name="release-questions-workflow",
    optional=["h5p_dev_dir", "course", "english_course", "tag", "release_target"],
)
def release_questions_workflow(
    ctx,
    h5p_dev_dir: str = "../h5p-dev",
    course: str = "h5p-demo",
    english_course: str = "h5p-demo-en",
    tag: str = "",
    release_target: str = "all",
    dry_run: bool = False,
    skip_release: bool = False,
) -> None:
    """Run the Questions release/update/demo verification workflow."""
    del ctx
    h5p_dev_path = (ROOT_DIR / h5p_dev_dir).resolve()
    if not h5p_dev_path.exists():
        raise FileNotFoundError(f"h5p-dev directory not found: {h5p_dev_path}")

    h5p_invoke = _invoke_binary(h5p_dev_path)

    subprocess.run([h5p_invoke, "pack-all"], check=True, cwd=h5p_dev_path)

    if not skip_release:
        release_args = [h5p_invoke, "deploy.release"]
        if release_target == "all":
            release_args.append("--all")
        elif release_target:
            release_args.extend(["--target", release_target])
        if dry_run:
            release_args.append("--dry-run")
        subprocess.run(release_args, check=True, cwd=h5p_dev_path)

    update_args = [PYTHON, "-m", "scripts.main", "update-h5p-libraries"]
    if tag:
        update_args.extend(["--tag", tag])
    subprocess.run(update_args, check=True, cwd=ROOT_DIR)

    subprocess.run([PYTHON, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"], check=True, cwd=ROOT_DIR)
    for demo_course in dict.fromkeys([course, english_course]):
        if demo_course:
            subprocess.run([PYTHON, "-m", "scripts.main", "build", demo_course], check=True, cwd=ROOT_DIR)
