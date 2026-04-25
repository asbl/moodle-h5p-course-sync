from __future__ import annotations

import argparse


def build_arg_parser(default_port: int) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Synchronisiert PythonQuestion-Bloecke aus MDX nach H5P und stellt eine Browser-Vorschau bereit."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

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

    push_parser = subparsers.add_parser(
        "push-moodle", help="Versucht, einen lokalen Kurs als H5P-Aktivitaeten in einen Moodle-Kurs zu pushen."
    )
    push_parser.add_argument("course", help="Lokaler Kursordner unter courses/, zum Beispiel python-2026")
    push_parser.add_argument("remote_course_id", type=int, help="Remote Moodle Course ID")
    push_parser.add_argument("--base-url", help="Moodle-Basis-URL. Faellt sonst auf MOODLE_BASE_URL zurueck.")
    push_parser.add_argument("--token", help="Moodle-Token. Faellt sonst auf MOODLE_TOKEN zurueck.")

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
