from __future__ import annotations

import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts.classes.cli.runner import resolve_course_dir, run_cli_command


@dataclass(slots=True)
class DummyQuestion:
    package_path: Path


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
        package_path = self.root_dir / "courses" / "python-2026" / "h5p" / "q1.h5p"

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

        print_mock.assert_called_once_with(Path("courses/python-2026/h5p/q1.h5p"))

    def test_run_cli_command_build_prints_relative_package_paths(self) -> None:
        args = SimpleNamespace(command="build", course="python-2026")
        parser = DummyParser()
        package_path = self.root_dir / "courses" / "python-2026" / "h5p" / "q1.h5p"

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

        print_mock.assert_called_once_with(Path("courses/python-2026/h5p/q1.h5p"))

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
