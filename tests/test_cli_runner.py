from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts.classes.cli.app_cli import build_arg_parser
from scripts.classes.cli.runner import create_new_course, list_course_summaries, resolve_course_dir, run_cli_command


@dataclass(slots=True)
class DummyQuestion:
    package_path: Path


@dataclass(slots=True)
class DummyUploadResult:
    identifier: str
    title: str
    action: str


class DummyParser:
    def __init__(self) -> None:
        self.exit_calls: list[tuple[int, str]] = []
        self.error_calls: list[str] = []

    def exit(self, status: int, message: str) -> None:
        self.exit_calls.append((status, message))
        raise RuntimeError("parser-exit")

    def error(self, message: str) -> None:
        self.error_calls.append(message)
        raise RuntimeError("parser-error")


class CliRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root_dir = Path(self.tmp_dir.name)
        self.courses_dir = self.root_dir / "courses"
        self.courses_dir.mkdir(parents=True)
        self.course_dir = self.courses_dir / "python-2026"
        self.course_dir.mkdir()

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_resolve_course_dir_returns_existing_directory(self) -> None:
        resolved = resolve_course_dir("python-2026", self.courses_dir)
        self.assertEqual(resolved, self.course_dir)

    def test_resolve_course_dir_raises_for_missing_course(self) -> None:
        with self.assertRaises(FileNotFoundError):
            resolve_course_dir("missing", self.courses_dir)

    def test_run_cli_command_sync_prints_relative_package_paths(self) -> None:
        args = SimpleNamespace(command="sync", course="python-2026")
        parser = DummyParser()
        package_path = self.root_dir / "courses" / "python-2026" / "build" / "h5p" / "q1.h5p"

        with patch("builtins.print") as print_mock:
            run_cli_command(
                args,
                parser=parser,
                root_dir=self.root_dir,
                courses_dir=self.courses_dir,
                sync_course=lambda _course_dir: [DummyQuestion(package_path=package_path)],
                build_preview_runtime=lambda _course_dir: [],
                serve_preview=lambda _port: None,
                resolve_moodle_client=lambda _base_url, _token: object(),
                import_moodle_course=lambda _course, _remote_id, _client: self.course_dir,
                push_moodle_course=lambda _course_dir, _remote_id, _client: None,
                sync_metadata_path=lambda _course_dir: self.course_dir / "sync-metadata.json",
                build_moodle_ping_report=lambda _client: {},
                print_moodle_ping_report=lambda _report: None,
                build_course_status=lambda _course_dir: {},
                print_course_status=lambda _status: None,
            )

        print_mock.assert_called_once_with(Path("courses/python-2026/build/h5p/q1.h5p"))

    def test_run_cli_command_build_prints_relative_package_paths(self) -> None:
        args = SimpleNamespace(command="build", course="python-2026")
        parser = DummyParser()
        package_path = self.root_dir / "courses" / "python-2026" / "build" / "h5p" / "q1.h5p"

        with patch("builtins.print") as print_mock:
            run_cli_command(
                args,
                parser=parser,
                root_dir=self.root_dir,
                courses_dir=self.courses_dir,
                sync_course=lambda _course_dir: [],
                build_preview_runtime=lambda course_dir: [DummyQuestion(package_path=package_path)] if course_dir == self.course_dir else [],
                serve_preview=lambda _port: None,
                resolve_moodle_client=lambda _base_url, _token: object(),
                import_moodle_course=lambda _course, _remote_id, _client: self.course_dir,
                push_moodle_course=lambda _course_dir, _remote_id, _client: None,
                sync_metadata_path=lambda _course_dir: self.course_dir / "sync-metadata.json",
                build_moodle_ping_report=lambda _client: {},
                print_moodle_ping_report=lambda _report: None,
                build_course_status=lambda _course_dir: {},
                print_course_status=lambda _status: None,
            )

        print_mock.assert_called_once_with(Path("courses/python-2026/build/h5p/q1.h5p"))

    def test_run_cli_command_build_without_course_prepares_all_courses(self) -> None:
        args = SimpleNamespace(command="build", course=None)
        parser = DummyParser()
        captured: list[Path | None] = []

        run_cli_command(
            args,
            parser=parser,
            root_dir=self.root_dir,
            courses_dir=self.courses_dir,
            sync_course=lambda _course_dir: [],
            build_preview_runtime=lambda course_dir: captured.append(course_dir) or [],
            serve_preview=lambda _port: None,
            resolve_moodle_client=lambda _base_url, _token: object(),
            import_moodle_course=lambda _course, _remote_id, _client: self.course_dir,
            push_moodle_course=lambda _course_dir, _remote_id, _client: None,
            sync_metadata_path=lambda _course_dir: self.course_dir / "sync-metadata.json",
            build_moodle_ping_report=lambda _client: {},
            print_moodle_ping_report=lambda _report: None,
            build_course_status=lambda _course_dir: {},
            print_course_status=lambda _status: None,
        )

        self.assertEqual(captured, [None])

    def test_run_cli_command_export_site_prints_exported_paths(self) -> None:
        args = SimpleNamespace(command="export-site", course="python-2026", output=str(self.root_dir / "public"))
        parser = DummyParser()
        exported_path = self.root_dir / "public" / "index.html"
        captured: list[tuple[Path, Path | None]] = []

        with patch("builtins.print") as print_mock:
            run_cli_command(
                args,
                parser=parser,
                root_dir=self.root_dir,
                courses_dir=self.courses_dir,
                sync_course=lambda _course_dir: [],
                build_preview_runtime=lambda _course_dir: [],
                serve_preview=lambda _port: None,
                resolve_moodle_client=lambda _base_url, _token: object(),
                import_moodle_course=lambda _course, _remote_id, _client: self.course_dir,
                push_moodle_course=lambda _course_dir, _remote_id, _client: None,
                sync_metadata_path=lambda _course_dir: self.course_dir / "sync-metadata.json",
                build_moodle_ping_report=lambda _client: {},
                print_moodle_ping_report=lambda _report: None,
                build_course_status=lambda _course_dir: {},
                print_course_status=lambda _status: None,
                export_static_site=lambda output_dir, course_dir: captured.append((output_dir, course_dir)) or [exported_path],
            )

        self.assertEqual(captured, [(self.root_dir / "public", self.course_dir)])
        print_mock.assert_called_once_with(Path("public/index.html"))

    def test_run_cli_command_export_chapter_prints_exported_package_paths(self) -> None:
        args = SimpleNamespace(command="export-chapter", course="python-2026", chapter="012-miniworlds", output=None)
        parser = DummyParser()
        package_path = self.root_dir / "courses" / "python-2026" / "exports" / "012-miniworlds" / "miniworlds-tutorial.h5p"

        with patch("builtins.print") as print_mock:
            run_cli_command(
                args,
                parser=parser,
                root_dir=self.root_dir,
                courses_dir=self.courses_dir,
                sync_course=lambda _course_dir: [],
                build_preview_runtime=lambda _course_dir: [],
                serve_preview=lambda _port: None,
                resolve_moodle_client=lambda _base_url, _token: object(),
                import_moodle_course=lambda _course, _remote_id, _client: self.course_dir,
                push_moodle_course=lambda _course_dir, _remote_id, _client: None,
                sync_metadata_path=lambda _course_dir: self.course_dir / "sync-metadata.json",
                build_moodle_ping_report=lambda _client: {},
                print_moodle_ping_report=lambda _report: None,
                build_course_status=lambda _course_dir: {},
                print_course_status=lambda _status: None,
                export_chapter=lambda course_dir, chapter, output_dir: [package_path],
            )

        print_mock.assert_called_once_with(Path("courses/python-2026/exports/012-miniworlds/miniworlds-tutorial.h5p"))

    def test_run_cli_command_upload_chapter_moodle_prints_upload_results(self) -> None:
        args = SimpleNamespace(
            command="upload-chapter-moodle",
            course="python-2026",
            chapter="012-miniworlds",
            course_url=None,
            section=None,
            username=None,
            password=None,
            storage_state=None,
            headless=False,
            timeout=30000,
            target=None,
        )
        parser = DummyParser()

        with patch("builtins.print") as print_mock:
            run_cli_command(
                args,
                parser=parser,
                root_dir=self.root_dir,
                courses_dir=self.courses_dir,
                sync_course=lambda _course_dir: [],
                build_preview_runtime=lambda _course_dir: [],
                serve_preview=lambda _port: None,
                resolve_moodle_client=lambda _base_url, _token: object(),
                import_moodle_course=lambda _course, _remote_id, _client: self.course_dir,
                push_moodle_course=lambda _course_dir, _remote_id, _client: None,
                sync_metadata_path=lambda _course_dir: self.course_dir / "sync-metadata.json",
                build_moodle_ping_report=lambda _client: {},
                print_moodle_ping_report=lambda _report: None,
                build_course_status=lambda _course_dir: {},
                print_course_status=lambda _status: None,
                upload_moodle_chapter=lambda *args: [
                    DummyUploadResult("miniworlds-tutorial", "miniworlds-tutorial", "updated")
                ],
            )

        print_mock.assert_called_once_with("updated: miniworlds-tutorial (miniworlds-tutorial)")

    def test_run_cli_command_upload_course_moodle_prints_upload_and_cleanup_results(self) -> None:
        @dataclass(slots=True)
        class DummyCleanupResult:
            title: str
            section_number: int
            action: str

        args = SimpleNamespace(
            command="upload-course-moodle",
            course="python-2026",
            course_url=None,
            username=None,
            password=None,
            storage_state=None,
            headless=False,
            timeout=30000,
            target=None,
            verify_mbz_sync=False,
            keep_extra_sections=False,
        )
        parser = DummyParser()

        with patch("builtins.print") as print_mock:
            run_cli_command(
                args,
                parser=parser,
                root_dir=self.root_dir,
                courses_dir=self.courses_dir,
                sync_course=lambda _course_dir: [],
                build_preview_runtime=lambda _course_dir: [],
                serve_preview=lambda _port: None,
                resolve_moodle_client=lambda _base_url, _token: object(),
                import_moodle_course=lambda _course, _remote_id, _client: self.course_dir,
                push_moodle_course=lambda _course_dir, _remote_id, _client: None,
                sync_metadata_path=lambda _course_dir: self.course_dir / "sync-metadata.json",
                build_moodle_ping_report=lambda _client: {},
                print_moodle_ping_report=lambda _report: None,
                build_course_status=lambda _course_dir: {},
                print_course_status=lambda _status: None,
                upload_moodle_course=lambda *args: [
                    DummyUploadResult("intro", "Intro", "updated"),
                    DummyCleanupResult("Alt", 4, "deleted"),
                ],
            )

        self.assertEqual(
            [call.args[0] for call in print_mock.call_args_list],
            ["updated: intro (Intro)", "deleted: section 4 (Alt)"],
        )

    def test_run_cli_command_audit_prints_report(self) -> None:
        args = SimpleNamespace(command="audit", course="python-2026")
        parser = DummyParser()

        with patch("builtins.print") as print_mock:
            run_cli_command(
                args,
                parser=parser,
                root_dir=self.root_dir,
                courses_dir=self.courses_dir,
                sync_course=lambda _course_dir: [],
                build_preview_runtime=lambda _course_dir: [],
                serve_preview=lambda _port: None,
                resolve_moodle_client=lambda _base_url, _token: object(),
                import_moodle_course=lambda _course, _remote_id, _client: self.course_dir,
                push_moodle_course=lambda _course_dir, _remote_id, _client: None,
                sync_metadata_path=lambda _course_dir: self.course_dir / "sync-metadata.json",
                build_moodle_ping_report=lambda _client: {},
                print_moodle_ping_report=lambda _report: None,
                build_course_status=lambda _course_dir: {},
                print_course_status=lambda _status: None,
                audit_course=lambda _course_dir: {"errors": 0, "warnings": 0, "checks": 3, "issues": []},
            )

        print_mock.assert_called_once_with("Audit: errors=0, warnings=0, checks=3")

    def test_run_cli_command_verify_moodle_prints_results(self) -> None:
        args = SimpleNamespace(
            command="verify-moodle",
            course="python-2026",
            course_url=None,
            username=None,
            password=None,
            storage_state=None,
            headless=True,
            timeout=30000,
            target=None,
        )
        parser = DummyParser()

        with patch("builtins.print") as print_mock:
            run_cli_command(
                args,
                parser=parser,
                root_dir=self.root_dir,
                courses_dir=self.courses_dir,
                sync_course=lambda _course_dir: [],
                build_preview_runtime=lambda _course_dir: [],
                serve_preview=lambda _port: None,
                resolve_moodle_client=lambda _base_url, _token: object(),
                import_moodle_course=lambda _course, _remote_id, _client: self.course_dir,
                push_moodle_course=lambda _course_dir, _remote_id, _client: None,
                sync_metadata_path=lambda _course_dir: self.course_dir / "sync-metadata.json",
                build_moodle_ping_report=lambda _client: {},
                print_moodle_ping_report=lambda _report: None,
                build_course_status=lambda _course_dir: {},
                print_course_status=lambda _status: None,
                verify_moodle_course=lambda *args: [
                    {"identifier": "intro", "activityId": 123, "ok": True, "message": "matched=Intro"}
                ],
            )

        self.assertEqual(print_mock.call_args_list[0].args[0], "Remote-Verifikation: ok=1, failed=0")
        self.assertIn("ok: intro", print_mock.call_args_list[1].args[0])

    def test_run_cli_command_publish_runs_audit_upload_verify_and_status(self) -> None:
        args = SimpleNamespace(
            command="publish",
            course="python-2026",
            course_url=None,
            username=None,
            password=None,
            storage_state=None,
            headless=True,
            timeout=30000,
            target=None,
            keep_extra_sections=False,
        )
        parser = DummyParser()
        calls: list[str] = []

        with patch("builtins.print"):
            run_cli_command(
                args,
                parser=parser,
                root_dir=self.root_dir,
                courses_dir=self.courses_dir,
                sync_course=lambda _course_dir: [],
                build_preview_runtime=lambda _course_dir: [],
                serve_preview=lambda _port: None,
                resolve_moodle_client=lambda _base_url, _token: object(),
                import_moodle_course=lambda _course, _remote_id, _client: self.course_dir,
                push_moodle_course=lambda _course_dir, _remote_id, _client: None,
                sync_metadata_path=lambda _course_dir: self.course_dir / "sync-metadata.json",
                build_moodle_ping_report=lambda _client: {},
                print_moodle_ping_report=lambda _report: None,
                build_course_status=lambda _course_dir: {"course": "python-2026"},
                print_course_status=lambda _status: calls.append("status"),
                audit_course=lambda _course_dir: calls.append("audit") or {"errors": 0, "warnings": 0, "checks": 1, "issues": []},
                upload_moodle_course=lambda *args: calls.append("upload") or [
                    DummyUploadResult("intro", "Intro", "updated")
                ],
                verify_moodle_course=lambda *args: calls.append("verify") or [
                    {"identifier": "intro", "activityId": 123, "ok": True, "message": ""}
                ],
            )

        self.assertEqual(calls, ["audit", "upload", "verify", "status"])

    def test_run_cli_command_serve_calls_serve_preview_with_port(self) -> None:
        args = SimpleNamespace(command="serve", port=8810)
        parser = DummyParser()
        called: list[int] = []

        run_cli_command(
            args,
            parser=parser,
            root_dir=self.root_dir,
            courses_dir=self.courses_dir,
            sync_course=lambda _course_dir: [],
            build_preview_runtime=lambda _course_dir: [],
            serve_preview=lambda port: called.append(port),
            resolve_moodle_client=lambda _base_url, _token: object(),
            import_moodle_course=lambda _course, _remote_id, _client: self.course_dir,
            push_moodle_course=lambda _course_dir, _remote_id, _client: None,
            sync_metadata_path=lambda _course_dir: self.course_dir / "sync-metadata.json",
            build_moodle_ping_report=lambda _client: {},
            print_moodle_ping_report=lambda _report: None,
            build_course_status=lambda _course_dir: {},
            print_course_status=lambda _status: None,
        )

        self.assertEqual(called, [8810])

    def test_run_cli_command_import_moodle_prints_course_and_metadata_paths(self) -> None:
        args = SimpleNamespace(
            command="import-moodle",
            course="python-2026",
            remote_course_id=42,
            base_url="https://moodle.example",
            token="abc",
        )
        parser = DummyParser()
        imported_course_dir = self.course_dir
        metadata_path = imported_course_dir / "sync-metadata.json"

        with patch("builtins.print") as print_mock:
            run_cli_command(
                args,
                parser=parser,
                root_dir=self.root_dir,
                courses_dir=self.courses_dir,
                sync_course=lambda _course_dir: [],
                build_preview_runtime=lambda _course_dir: [],
                serve_preview=lambda _port: None,
                resolve_moodle_client=lambda _base_url, _token: object(),
                import_moodle_course=lambda _course, _remote_id, _client: imported_course_dir,
                push_moodle_course=lambda _course_dir, _remote_id, _client: None,
                sync_metadata_path=lambda _course_dir: metadata_path,
                build_moodle_ping_report=lambda _client: {},
                print_moodle_ping_report=lambda _report: None,
                build_course_status=lambda _course_dir: {},
                print_course_status=lambda _status: None,
            )

        self.assertEqual(print_mock.call_count, 2)
        first_call = print_mock.call_args_list[0].args[0]
        second_call = print_mock.call_args_list[1].args[0]
        self.assertEqual(first_call, Path("courses/python-2026"))
        self.assertEqual(second_call, Path("courses/python-2026/sync-metadata.json"))

    def test_run_cli_command_moodle_ping_builds_and_prints_report(self) -> None:
        args = SimpleNamespace(command="moodle-ping", base_url=None, token=None)
        parser = DummyParser()
        calls: list[str] = []

        client = object()
        report = {"ok": True}

        run_cli_command(
            args,
            parser=parser,
            root_dir=self.root_dir,
            courses_dir=self.courses_dir,
            sync_course=lambda _course_dir: [],
            build_preview_runtime=lambda _course_dir: [],
            serve_preview=lambda _port: None,
            resolve_moodle_client=lambda _base_url, _token: client,
            import_moodle_course=lambda _course, _remote_id, _client: self.course_dir,
            push_moodle_course=lambda _course_dir, _remote_id, _client: None,
            sync_metadata_path=lambda _course_dir: self.course_dir / "sync-metadata.json",
            build_moodle_ping_report=lambda _client: report,
            print_moodle_ping_report=lambda _report: calls.append("printed"),
            build_course_status=lambda _course_dir: {},
            print_course_status=lambda _status: None,
        )

        self.assertEqual(calls, ["printed"])

    def test_run_cli_command_push_moodle_calls_push_callback(self) -> None:
        args = SimpleNamespace(
            command="push-moodle",
            course="python-2026",
            remote_course_id=7,
            base_url="https://moodle.example",
            token="abc",
        )
        parser = DummyParser()
        client = object()
        calls: list[tuple[Path, int, object]] = []

        run_cli_command(
            args,
            parser=parser,
            root_dir=self.root_dir,
            courses_dir=self.courses_dir,
            sync_course=lambda _course_dir: [],
            build_preview_runtime=lambda _course_dir: [],
            serve_preview=lambda _port: None,
            resolve_moodle_client=lambda _base_url, _token: client,
            import_moodle_course=lambda _course, _remote_id, _client: self.course_dir,
            push_moodle_course=lambda course_dir, remote_id, resolved_client: calls.append(
                (course_dir, remote_id, resolved_client)
            ),
            sync_metadata_path=lambda _course_dir: self.course_dir / "sync-metadata.json",
            build_moodle_ping_report=lambda _client: {},
            print_moodle_ping_report=lambda _report: None,
            build_course_status=lambda _course_dir: {},
            print_course_status=lambda _status: None,
        )

        self.assertEqual(calls, [(self.course_dir, 7, client)])

    def test_run_cli_command_status_prints_status_for_course(self) -> None:
        args = SimpleNamespace(command="status", course="python-2026")
        parser = DummyParser()
        captured: list[dict[str, object]] = []
        status_payload = {"course": "python-2026", "questions": 1}

        run_cli_command(
            args,
            parser=parser,
            root_dir=self.root_dir,
            courses_dir=self.courses_dir,
            sync_course=lambda _course_dir: [],
            build_preview_runtime=lambda _course_dir: [],
            serve_preview=lambda _port: None,
            resolve_moodle_client=lambda _base_url, _token: object(),
            import_moodle_course=lambda _course, _remote_id, _client: self.course_dir,
            push_moodle_course=lambda _course_dir, _remote_id, _client: None,
            sync_metadata_path=lambda _course_dir: self.course_dir / "sync-metadata.json",
            build_moodle_ping_report=lambda _client: {},
            print_moodle_ping_report=lambda _report: None,
            build_course_status=lambda _course_dir: status_payload,
            print_course_status=lambda status: captured.append(status),
        )

        self.assertEqual(captured, [status_payload])

    def test_run_cli_command_calls_parser_exit_for_handled_exceptions(self) -> None:
        args = SimpleNamespace(command="sync", course="python-2026")
        parser = DummyParser()

        with self.assertRaisesRegex(RuntimeError, "parser-exit"):
            run_cli_command(
                args,
                parser=parser,
                root_dir=self.root_dir,
                courses_dir=self.courses_dir,
                sync_course=lambda _course_dir: (_ for _ in ()).throw(FileNotFoundError("not found")),
                build_preview_runtime=lambda _course_dir: [],
                serve_preview=lambda _port: None,
                resolve_moodle_client=lambda _base_url, _token: object(),
                import_moodle_course=lambda _course, _remote_id, _client: self.course_dir,
                push_moodle_course=lambda _course_dir, _remote_id, _client: None,
                sync_metadata_path=lambda _course_dir: self.course_dir / "sync-metadata.json",
                build_moodle_ping_report=lambda _client: {},
                print_moodle_ping_report=lambda _report: None,
                build_course_status=lambda _course_dir: {},
                print_course_status=lambda _status: None,
            )

        self.assertEqual(len(parser.exit_calls), 1)
        status, message = parser.exit_calls[0]
        self.assertEqual(status, 1)
        self.assertIn("Fehler:", message)

    def test_run_cli_command_calls_parser_error_for_unknown_command(self) -> None:
        args = SimpleNamespace(command="unknown")
        parser = DummyParser()

        with self.assertRaisesRegex(RuntimeError, "parser-error"):
            run_cli_command(
                args,
                parser=parser,
                root_dir=self.root_dir,
                courses_dir=self.courses_dir,
                sync_course=lambda _course_dir: [],
                build_preview_runtime=lambda _course_dir: [],
                serve_preview=lambda _port: None,
                resolve_moodle_client=lambda _base_url, _token: object(),
                import_moodle_course=lambda _course, _remote_id, _client: self.course_dir,
                push_moodle_course=lambda _course_dir, _remote_id, _client: None,
                sync_metadata_path=lambda _course_dir: self.course_dir / "sync-metadata.json",
                build_moodle_ping_report=lambda _client: {},
                print_moodle_ping_report=lambda _report: None,
                build_course_status=lambda _course_dir: {},
                print_course_status=lambda _status: None,
            )

        self.assertEqual(parser.error_calls, ["Unbekanntes Kommando."])


def _unused(*args, **kwargs):
    raise AssertionError("Unexpected callback call")


class CliRunnerOnboardingTests(unittest.TestCase):
    def test_create_new_course_writes_minimal_course_structure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            courses_dir = Path(temp_dir) / "courses"
            course_dir = create_new_course(courses_dir, "info-2026", title="Informatik 2026", language="en")

            self.assertEqual(courses_dir / "info-2026", course_dir)
            self.assertIn("# Informatik 2026", (course_dir / "index.mdx").read_text(encoding="utf-8"))
            self.assertTrue((course_dir / "chapters" / "001-getting-started.mdx").exists())
            self.assertTrue((course_dir / "h5p" / "001-getting-started" / "hello-world" / "content.mdx").exists())
            self.assertTrue((course_dir / "h5p" / "001-getting-started" / "hello-world" / "settings.yml").exists())
            self.assertTrue((course_dir / "h5p" / "001-getting-started" / "hello-world" / "h5p.json").exists())

    def test_list_course_summaries_reads_index_title_and_chapter_count(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            courses_dir = Path(temp_dir) / "courses"
            create_new_course(courses_dir, "info-2026", title="Informatik 2026")

            self.assertEqual(
                [{"slug": "info-2026", "title": "Informatik 2026", "chapters": 1}],
                list_course_summaries(courses_dir),
            )

    def test_create_new_course_does_not_overwrite_existing_course_with_force(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            courses_dir = Path(temp_dir) / "courses"
            create_new_course(courses_dir, "info-2026")

            with self.assertRaises(FileExistsError):
                create_new_course(courses_dir, "info-2026", force=True)

    def test_resolve_course_dir_error_lists_available_courses(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            courses_dir = Path(temp_dir) / "courses"
            create_new_course(courses_dir, "info-2026")

            with self.assertRaises(FileNotFoundError) as context:
                resolve_course_dir("missing-course", courses_dir)

            self.assertIn("info-2026", str(context.exception))

    def test_run_cli_command_handles_new_course_without_service_callbacks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root_dir = Path(temp_dir)
            courses_dir = root_dir / "courses"
            parser = build_arg_parser(default_port=8765)
            args = parser.parse_args(["new-course", "info-2026", "--title", "Informatik 2026"])

            with redirect_stdout(io.StringIO()):
                run_cli_command(
                    args,
                    parser=parser,
                    root_dir=root_dir,
                    courses_dir=courses_dir,
                    sync_course=_unused,
                    build_preview_runtime=_unused,
                    serve_preview=_unused,
                    resolve_moodle_client=_unused,
                    import_moodle_course=_unused,
                    push_moodle_course=_unused,
                    sync_metadata_path=_unused,
                    build_moodle_ping_report=_unused,
                    print_moodle_ping_report=_unused,
                    build_course_status=_unused,
                    print_course_status=_unused,
                )

            self.assertTrue((courses_dir / "info-2026" / "index.mdx").exists())
