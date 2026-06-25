from __future__ import annotations

import argparse
import json
import re
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
        available_courses = list_course_summaries(courses_dir)
        hint = ""
        if available_courses:
            hint = " Verfuegbare Kurse: " + ", ".join(item["slug"] for item in available_courses) + "."
        else:
            hint = " Lege mit 'course-sync new-course <name>' einen neuen Kurs an."
        raise FileNotFoundError(f"Kurs '{course}' wurde nicht gefunden.{hint}")
    return course_dir


def derive_course_title(slug: str) -> str:
    words = [word for word in re.split(r"[-_\s]+", slug.strip()) if word]
    return " ".join(word[:1].upper() + word[1:] for word in words) or slug


def list_course_summaries(courses_dir: Path) -> list[dict[str, object]]:
    if not courses_dir.exists():
        return []

    summaries: list[dict[str, object]] = []
    for course_dir in sorted(path for path in courses_dir.iterdir() if path.is_dir()):
        index_path = course_dir / "index.mdx"
        if not index_path.exists():
            continue
        source = index_path.read_text(encoding="utf-8")
        title = derive_course_title(course_dir.name)
        first_heading = re.search(r"^#\s+(.+)$", source, flags=re.MULTILINE)
        if first_heading is not None:
            title = first_heading.group(1).strip()
        chapter_count = len(re.findall(r"<Chapter\b", source))
        summaries.append({"slug": course_dir.name, "title": title, "chapters": chapter_count})
    return summaries


def create_new_course(courses_dir: Path, slug: str, *, title: str = "", language: str = "de", force: bool = False) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", slug):
        raise ValueError("Kursname darf nur Buchstaben, Zahlen, '-' und '_' enthalten und muss damit beginnen.")

    course_dir = courses_dir / slug
    if course_dir.exists() and not force:
        raise FileExistsError(f"Kurs '{slug}' existiert bereits. Nutze --force nur fuer bewusst vorbereitete leere Ordner.")
    if course_dir.exists() and any(course_dir.iterdir()):
        raise FileExistsError(f"Kurs '{slug}' existiert bereits und ist nicht leer.")

    title = title.strip() or derive_course_title(slug)
    language = (language.strip() or "de").lower()
    chapter_slug = "001-einstieg" if language.startswith("de") else "001-getting-started"
    chapter_title = "Einstieg" if language.startswith("de") else "Getting Started"
    question_title = "Erste Python-Aufgabe" if language.startswith("de") else "First Python Exercise"
    instructions = (
        "Aendere den Text in der print-Anweisung und fuehre das Programm aus."
        if language.startswith("de")
        else "Change the text in the print statement and run the program."
    )

    chapters_dir = course_dir / "chapters"
    h5p_dir = course_dir / "h5p" / chapter_slug / "hello-world"
    h5p_dir.mkdir(parents=True, exist_ok=True)
    chapters_dir.mkdir(parents=True, exist_ok=True)

    (course_dir / "index.mdx").write_text(
        f"# {title}\n\n<Chapter src=\"./chapters/{chapter_slug}.mdx\" title=\"{chapter_title}\" />\n",
        encoding="utf-8",
    )
    (chapters_dir / f"{chapter_slug}.mdx").write_text(
        "\n".join(
            [
                f"## {chapter_title}",
                "",
                "<PythonQuestion",
                '  identifier="hello-world"',
                f'  title="{question_title}"',
                f'  instructions="{instructions}"',
                "/>",
                "",
                "```python question:hello-world starter",
                'print("Hello, world!")',
                "```",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (h5p_dir / "content.mdx").write_text(
        "\n".join(
            [
                "<Instructions>",
                instructions,
                "</Instructions>",
                "",
                "```python editor:startingCode",
                'print("Hello, world!")',
                "```",
                "",
                "```yaml grading",
                "gradingMethod: please_choose",
                "dueDateGroup:",
                "  enableDueDate: false",
                "  duedate: 01.01.1970",
                "testCases: []",
                "```",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (h5p_dir / "settings.yml").write_text(
        "contentType: ide_only\npythonRunner: skulpt\npyodideOptions:\n  packages: []\n",
        encoding="utf-8",
    )
    (h5p_dir / "h5p.json").write_text(
        json.dumps(
            {
                "title": question_title,
                "language": language,
                "defaultLanguage": language,
                "mainLibrary": "H5P.PythonQuestion",
                "embedTypes": ["div"],
                "license": "U",
                "preloadedDependencies": [
                    {"machineName": "H5P.PythonQuestion", "majorVersion": 6, "minorVersion": 90},
                    {"machineName": "H5P.CodeQuestion", "majorVersion": 6, "minorVersion": 90},
                    {"machineName": "H5P.LibCodeTools", "majorVersion": 6, "minorVersion": 90},
                ],
                "majorVersion": 6,
                "minorVersion": 90,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
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
        [Path, str, str | None, str | None, str | None, str | None, Path | None, bool, int, str | None, bool],
        list[UploadResultLike],
    ]
    | None = None,
    update_h5p_libraries_from_github: Callable[[str | None], list[dict[str, str]]] | None = None,
    import_moodle_from_mbz: Callable[[str, Path, int, str], Path] | None = None,
    export_static_site: Callable[[Path, Path | None], list[Path]] | None = None,
) -> None:
    try:
        if args.command == "list-courses":
            courses = list_course_summaries(courses_dir)
            if not courses:
                print("Keine Kurse gefunden. Lege mit 'course-sync new-course <name>' einen Kurs an.")
                return
            for course in courses:
                if args.verbose:
                    print(f"{course['slug']}\t{course['title']}\t{course['chapters']} Kapitel")
                else:
                    print(course["slug"])
            return

        if args.command == "new-course":
            course_dir = create_new_course(
                courses_dir,
                args.course,
                title=args.title or "",
                language=args.language,
                force=args.force,
            )
            print(course_dir.relative_to(root_dir) if course_dir.is_relative_to(root_dir) else course_dir)
            print(f"Naechste Schritte: course-sync sync {args.course} && course-sync serve")
            return

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

        if args.command == "export-site":
            if export_static_site is None:
                raise RuntimeError("Static-Site-Export ist nicht konfiguriert.")
            course_dir = resolve_course_dir(args.course, courses_dir) if args.course else None
            output_dir = Path(args.output).expanduser()
            exported_paths = export_static_site(output_dir, course_dir)
            for path in exported_paths:
                print(path.relative_to(root_dir) if path.is_relative_to(root_dir) else path)
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

        if args.command == "import-mbz":
            if import_moodle_from_mbz is None:
                raise RuntimeError("import-mbz ist nicht konfiguriert.")
            mbz_path = Path(args.mbz_path).expanduser()
            if not mbz_path.exists():
                raise FileNotFoundError(f"MBZ-Datei nicht gefunden: {mbz_path}")
            course_dir = import_moodle_from_mbz(
                args.course,
                mbz_path,
                args.remote_course_id,
                args.base_url,
            )
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
                getattr(args, "verify_mbz_sync", False),
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
    except (FileExistsError, FileNotFoundError, RuntimeError, ValueError) as error:
        parser.exit(1, f"Fehler: {error}\n")

    parser.error("Unbekanntes Kommando.")
