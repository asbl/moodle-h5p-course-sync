from __future__ import annotations

import argparse
from pathlib import Path
from typing import Callable, Protocol


class SyncQuestionLike(Protocol):
    package_path: Path


class UploadResultLike(Protocol):
    identifier: str
    title: str
    action: str


def resolve_course_dir(course: str, courses_dir: Path) -> Path:
    course_dir = courses_dir / course
    if not course_dir.exists():
        raise FileNotFoundError(f"Kurs '{course}' wurde nicht gefunden.")
    return course_dir


def run_cli_command(
    args: argparse.Namespace,
    *,
    parser: argparse.ArgumentParser,
    root_dir: Path,
    courses_dir: Path,
    sync_course: Callable[[Path], list[SyncQuestionLike]],
    build_preview_runtime: Callable[[Path | None], list[SyncQuestionLike]],
    serve_preview: Callable[[int], None],
    resolve_moodle_client: Callable[[str | None, str | None], object],
    import_moodle_course: Callable[[str, int, object], Path],
    push_moodle_course: Callable[[Path, int, object], None],
    sync_metadata_path: Callable[[Path], Path],
    build_moodle_ping_report: Callable[[object], dict[str, object]],
    print_moodle_ping_report: Callable[[dict[str, object]], None],
    build_course_status: Callable[[Path], dict[str, object]],
    print_course_status: Callable[[dict[str, object]], None],
    export_chapter: Callable[[Path, str, Path | None], list[Path]] | None = None,
    upload_moodle_chapter: Callable[
        [Path, str, str | None, str | None, str | None, str | None, Path | None, bool, int, str | None],
        list[UploadResultLike],
    ]
    | None = None,
    update_h5p_libraries_from_github: Callable[[str | None], list[dict[str, str]]] | None = None,
) -> None:
    try:
        if args.command == "sync":
            course_dir = resolve_course_dir(args.course, courses_dir)
            questions = sync_course(course_dir)
            for question in questions:
                print(question.package_path.relative_to(root_dir))
            return

        if args.command == "build":
            course_dir = resolve_course_dir(args.course, courses_dir) if args.course else None
            questions = build_preview_runtime(course_dir)
            for question in questions:
                print(question.package_path.relative_to(root_dir))
            return

        if args.command == "update-h5p-libraries":
            if update_h5p_libraries_from_github is None:
                raise RuntimeError("Update-H5P-Libraries ist nicht konfiguriert.")
            updated_libraries = update_h5p_libraries_from_github(args.tag)
            for item in updated_libraries:
                path = Path(str(item["path"]))
                display_path = path.relative_to(root_dir) if path.is_relative_to(root_dir) else path
                print(f"{item['machineName']}: {item['asset']} ({item['release']}) -> {display_path}")
            return

        if args.command == "export-chapter":
            if export_chapter is None:
                raise RuntimeError("Export-Chapter ist nicht konfiguriert.")
            course_dir = resolve_course_dir(args.course, courses_dir)
            output_dir = Path(args.output).expanduser() if args.output else None
            exported_paths = export_chapter(course_dir, args.chapter, output_dir)
            for path in exported_paths:
                print(path.relative_to(root_dir) if path.is_relative_to(root_dir) else path)
            return

        if args.command == "serve":
            serve_preview(args.port)
            return

        if args.command == "import-moodle":
            client = resolve_moodle_client(args.base_url, args.token)
            course_dir = import_moodle_course(args.course, args.remote_course_id, client)
            print(course_dir.relative_to(root_dir))
            print(sync_metadata_path(course_dir).relative_to(root_dir))
            return

        if args.command == "push-moodle":
            course_dir = resolve_course_dir(args.course, courses_dir)
            client = resolve_moodle_client(args.base_url, args.token)
            push_moodle_course(course_dir, args.remote_course_id, client)
            return

        if args.command == "upload-chapter-moodle":
            if upload_moodle_chapter is None:
                raise RuntimeError("Moodle-Playwright-Upload ist nicht konfiguriert.")
            course_dir = resolve_course_dir(args.course, courses_dir)
            storage_state = Path(args.storage_state).expanduser() if args.storage_state else None
            results = upload_moodle_chapter(
                course_dir,
                args.chapter,
                args.course_url,
                args.section,
                args.username,
                args.password,
                storage_state,
                args.headless,
                args.timeout,
                args.target,
            )
            for result in results:
                print(f"{result.action}: {result.identifier} ({result.title})")
            return

        if args.command == "moodle-ping":
            client = resolve_moodle_client(args.base_url, args.token)
            print_moodle_ping_report(build_moodle_ping_report(client))
            return

        if args.command == "status":
            course_dir = resolve_course_dir(args.course, courses_dir)
            print_course_status(build_course_status(course_dir))
            return
    except (FileNotFoundError, RuntimeError, ValueError) as error:
        parser.exit(1, f"Fehler: {error}\n")

    parser.error("Unbekanntes Kommando.")
