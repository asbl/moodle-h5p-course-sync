from __future__ import annotations

import argparse


def build_arg_parser(default_port: int) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Synchronisiert PythonQuestion-Bloecke aus MDX nach H5P und stellt eine Browser-Vorschau bereit."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_courses_parser = subparsers.add_parser(
        "list-courses",
        help="Listet alle lokalen Kursordner unter courses/.",
    )
    list_courses_parser.add_argument(
        "--verbose",
        action="store_true",
        help="Zeigt zusaetzlich Titel und Anzahl der Kapitel.",
    )

    new_course_parser = subparsers.add_parser(
        "new-course",
        help="Legt einen neuen minimalen Kurs mit Beispielkapitel an.",
    )
    new_course_parser.add_argument("course", help="Neuer Kursordner unter courses/, zum Beispiel info-2026")
    new_course_parser.add_argument(
        "--title",
        help="Anzeigename des Kurses. Standard: aus dem Kursordner abgeleitet.",
    )
    new_course_parser.add_argument(
        "--language",
        default="de",
        help="Sprache fuer H5P-Metadaten, z.B. de oder en. Standard: de.",
    )
    new_course_parser.add_argument(
        "--force",
        action="store_true",
        help="Erlaubt das Anlegen in einem bereits vorhandenen leeren Kursordner.",
    )

    sync_parser = subparsers.add_parser("sync", help="Erzeugt H5P-Dateien aus einer Kurs-MDX.")
    sync_parser.add_argument("course", help="Kursordner unter courses/, zum Beispiel python-2026")

    build_parser = subparsers.add_parser(
        "build",
        help="Bereitet H5P-Dateien und Preview-Runtime im Batch vor.",
    )
    build_parser.add_argument(
        "course",
        nargs="?",
        help="Optionaler Kursordner unter courses/. Ohne Angabe werden alle Kurse vorbereitet.",
    )

    update_libraries_parser = subparsers.add_parser(
        "update-h5p-libraries",
        help="Laedt die neuesten H5P-Libraries aus dem GitHub-Release und aktualisiert libraries/.",
    )
    update_libraries_parser.add_argument(
        "--tag",
        help="Optionaler GitHub-Release-Tag. Ohne Angabe wird das neueste Release verwendet.",
    )

    export_parser = subparsers.add_parser(
        "export-chapter",
        help="Kopiert die gebauten H5P-Pakete eines Kapitels in einen Upload-Ordner.",
    )
    export_parser.add_argument("course", help="Kursordner unter courses/, zum Beispiel python-2026")
    export_parser.add_argument("chapter", help="Kapitel-Slug, zum Beispiel 012-miniworlds")
    export_parser.add_argument(
        "--output",
        help="Optionaler Zielordner. Standard: courses/<kurs>/exports/<kapitel>/",
    )

    serve_parser = subparsers.add_parser("serve", help="Startet die lokale Browser-Vorschau.")
    serve_parser.add_argument(
        "--port",
        type=int,
        default=default_port,
        help=f"Port fuer den Preview-Server. Standard: {default_port}",
    )

    import_parser = subparsers.add_parser(
        "import-moodle", help="Importiert einen vorhandenen Moodle-Kurs als lokale MDX-Struktur."
    )
    import_parser.add_argument("course", help="Lokaler Kursordner unter courses/, zum Beispiel python-2026")
    import_parser.add_argument("remote_course_id", type=int, help="Remote Moodle Course ID")
    import_parser.add_argument("--base-url", help="Moodle-Basis-URL. Faellt sonst auf MOODLE_BASE_URL zurueck.")
    import_parser.add_argument("--token", help="Moodle-Token. Faellt sonst auf MOODLE_TOKEN zurueck.")

    import_mbz_parser = subparsers.add_parser(
        "import-mbz",
        help="Importiert einen Moodle-Kurs aus einer lokalen .mbz-Sicherungsdatei ohne API-Zugangsdaten.",
    )
    import_mbz_parser.add_argument("course", help="Lokaler Kursordner unter courses/, zum Beispiel h5p-demo")
    import_mbz_parser.add_argument("mbz_path", help="Pfad zur .mbz-Sicherungsdatei")
    import_mbz_parser.add_argument(
        "--remote-course-id",
        type=int,
        default=0,
        help="Remote Moodle Course ID fuer die Sync-Metadaten (optional).",
    )
    import_mbz_parser.add_argument(
        "--base-url",
        default="",
        help="Moodle-Basis-URL fuer die Sync-Metadaten (optional).",
    )

    push_parser = subparsers.add_parser(
        "push-moodle", help="Versucht, einen lokalen Kurs als H5P-Aktivitaeten in einen Moodle-Kurs zu pushen."
    )
    push_parser.add_argument("course", help="Lokaler Kursordner unter courses/, zum Beispiel python-2026")
    push_parser.add_argument("remote_course_id", type=int, help="Remote Moodle Course ID")
    push_parser.add_argument("--base-url", help="Moodle-Basis-URL. Faellt sonst auf MOODLE_BASE_URL zurueck.")
    push_parser.add_argument("--token", help="Moodle-Token. Faellt sonst auf MOODLE_TOKEN zurueck.")

    upload_parser = subparsers.add_parser(
        "upload-chapter-moodle",
        help="Laedt alle H5P-Pakete eines Kapitels per Playwright in eine Moodle-Section hoch.",
    )
    upload_parser.add_argument("course", help="Lokaler Kursordner unter courses/, zum Beispiel python-2026")
    upload_parser.add_argument("chapter", help="Kapitel-Slug, zum Beispiel 012-miniworlds")
    upload_parser.add_argument(
        "--course-url",
        help="Moodle-Kurs-URL. Standard: moodleBaseUrl + remoteCourseId aus .course-sync.json.",
    )
    upload_parser.add_argument(
        "--target",
        help="Benanntes Moodle-Ziel aus .env, z.B. staging oder schule2.",
    )
    upload_parser.add_argument(
        "--section",
        help="Moodle-Section-Titel. Standard: Chapter-title aus index.mdx oder Kapitel-Slug.",
    )
    upload_parser.add_argument("--username", help="Moodle-Login. Faellt sonst auf MOODLE_USERNAME zurueck.")
    upload_parser.add_argument("--password", help="Moodle-Passwort. Faellt sonst auf MOODLE_PASSWORD zurueck.")
    upload_parser.add_argument(
        "--storage-state",
        help="Playwright-Login-Statusdatei. Standard: courses/<kurs>/.moodle-storage-state.json.",
    )
    upload_parser.add_argument("--headless", action="store_true", help="Browser ohne sichtbares Fenster starten.")
    upload_parser.add_argument(
        "--timeout",
        type=int,
        default=30_000,
        help="Playwright-Timeout in Millisekunden. Standard: 30000.",
    )
    upload_parser.add_argument(
        "--verify-mbz-sync",
        action="store_true",
        help="Laedt vor und nach dem Upload ein .mbz-Kursbackup herunter und gibt eine detaillierte Diff-Analyse aus.",
    )

    ping_parser = subparsers.add_parser(
        "moodle-ping", help="Prueft, ob die konfigurierte Moodle-Webservice-Verbindung funktioniert."
    )
    ping_parser.add_argument("--base-url", help="Moodle-Basis-URL. Faellt sonst auf MOODLE_BASE_URL zurueck.")
    ping_parser.add_argument("--token", help="Moodle-Token. Faellt sonst auf MOODLE_TOKEN zurueck.")

    status_parser = subparsers.add_parser(
        "status", help="Zeigt den lokalen Sync-Status eines importierten Moodle-Kurses."
    )
    status_parser.add_argument("course", help="Lokaler Kursordner unter courses/, zum Beispiel python-2026")

    return parser


def print_course_status(status: dict[str, object]) -> None:
    counts = status["counts"]
    print(f"Kurs: {status['course']} (remote course id: {status['remoteCourseId']})")
    print(f"Moodle: {status['moodleBaseUrl']}")
    print(
        "Status: "
        f"tracked={counts['tracked']}, "
        f"modified-local={counts['modified-local']}, "
        f"local-only={counts['local-only']}, "
        f"remote-only={counts['remote-only']}"
    )
    for item in status["items"]:
        print(f"- {item['identifier']}: {item['status']} (remote activity id: {item['remoteActivityId']})")


def print_moodle_ping_report(report: dict[str, object]) -> None:
    print(f"Moodle erreichbar: {report['baseUrl']}")
    print(f"Site: {report['siteName']} ({report['siteUrl']})")
    print(f"Benutzer: {report['fullName']} ({report['userName']}, id={report['userId']})")
    print(
        "Import-API: "
        + ("verfugbar" if report["supportsCourseImport"] else "fehlt: core_course_get_contents nicht freigegeben")
    )
    print(
        "Push-API: "
        + (
            "verfugbar"
            if report["supportsCoursePush"]
            else "nicht verfuegbar: " + "; ".join(str(item) for item in report.get("pushBlockers", []))
        )
    )
