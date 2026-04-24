from __future__ import annotations

import tempfile
import time
import unittest
from dataclasses import dataclass
from http import HTTPStatus
from pathlib import Path

from scripts.classes.content_store import ContentStore
from scripts.classes.preview_controller import PreviewController
from scripts.classes.runtime_preparation import RuntimePreparationService


@dataclass(slots=True)
class DummyQuestion:
    identifier: str
    runtime_content_id: str
    package_path: Path


class ContentStoreTests(unittest.TestCase):
    def test_write_h5p_content_files_removes_legacy_json(self) -> None:
        store = ContentStore()
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            (target / "content.json").write_text("{}", encoding="utf-8")

            store.write_h5p_content_files(target, {"key": "value"})

            self.assertTrue((target / "content.yml").exists())
            self.assertFalse((target / "content.json").exists())
            loaded = store.read_h5p_content_payload(target)
            self.assertEqual(loaded["key"], "value")

    def test_write_h5p_content_files_uses_backtick_literals_for_long_text_fields(self) -> None:
        store = ContentStore()
        payload = {
            "contentType": "text_only",
            "pythonRunner": "skulpt",
            "contents": [
                {
                    "type": "text",
                    "text": "Line 1\nLine 2",
                    "options": {"showEditor": True},
                },
                {
                    "type": "code",
                    "code": "print('a')\nprint('b')\n",
                    "options": {"showEditor": True},
                },
            ],
            "editorSettings": {"options": {"allowAddingFiles": False}},
        }

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            store.write_h5p_content_files(target, payload)

            raw_yaml = (target / "content.yml").read_text(encoding="utf-8")
            loaded = store.read_h5p_content_payload(target)

        self.assertIn("`Line 1", raw_yaml)
        self.assertIn("`print('a')", raw_yaml)
        self.assertNotIn("\\n", raw_yaml)
        self.assertEqual(loaded["contents"][0]["text"], "Line 1\nLine 2")
        self.assertEqual(loaded["contents"][1]["code"], "print('a')\nprint('b')\n")

    def test_write_h5p_content_files_compacts_defaults_and_restores_on_read(self) -> None:
        store = ContentStore()
        payload = {
            "contentType": "text_only",
            "contents": [
                {
                    "type": "text",
                    "text": "Kurz",
                    "options": {
                        "showEditor": True,
                        "enableImageUploads": False,
                        "defaultImages": [{}],
                        "enableSoundUploads": False,
                        "sourceFiles": [{"visibleToLearner": True, "learnerEditable": True}],
                        "allowAddingFiles": False,
                        "editorMode": "code",
                    },
                    "blocklyCategories": {
                        "variables": True,
                        "logic": True,
                        "loops": True,
                        "math": True,
                        "text": True,
                        "lists": True,
                        "functions": True,
                    },
                }
            ],
            "editorSettings": {
                "options": {
                    "enableImageUploads": False,
                    "defaultImages": [{}],
                    "enableSoundUploads": False,
                    "sourceFiles": [{"code": "", "visibleToLearner": True, "learnerEditable": True}],
                    "allowAddingFiles": False,
                    "editorMode": "code",
                },
                "blocklyCategories": {
                    "variables": True,
                    "logic": True,
                    "loops": True,
                    "math": True,
                    "text": True,
                    "lists": True,
                    "functions": True,
                },
            },
            "advancedOptions": {
                "showConsole": True,
                "disableOutputPopups": False,
                "enableSaveLoadButtons": True,
                "execLimit": 0,
                "blocklyCdnUrl": "",
                "codeMirrorCdnUrl": "",
                "markdownCdnUrl": "",
                "fontAwesomeCdnUrl": "",
                "sweetAlertCdnUrl": "",
                "jsZipCdnUrl": "",
                "p5CdnUrl": "",
                "skulptCdnUrl": "",
                "sqlJsUrl": "",
            },
            "pyodideOptions": {
                "pyodideCdnUrl": "",
            },
        }

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            store.write_h5p_content_files(target, payload)
            raw_yaml = (target / "content.yml").read_text(encoding="utf-8")
            loaded = store.read_h5p_content_payload(target)

        self.assertNotIn("showEditor", raw_yaml)
        self.assertNotIn("editorMode", raw_yaml)
        self.assertNotIn("blocklyCategories", raw_yaml)
        self.assertNotIn("advancedOptions", raw_yaml)
        self.assertNotIn("pyodideOptions", raw_yaml)

        self.assertEqual(loaded["contents"][0]["options"]["showEditor"], True)
        self.assertEqual(loaded["contents"][0]["options"]["editorMode"], "code")
        self.assertEqual(loaded["contents"][0]["blocklyCategories"]["variables"], True)
        self.assertEqual(loaded["editorSettings"]["options"]["allowAddingFiles"], False)
        self.assertEqual(loaded["editorSettings"]["blocklyCategories"]["functions"], True)
        self.assertEqual(loaded["advancedOptions"]["p5CdnUrl"], "")
        self.assertEqual(loaded["advancedOptions"]["showConsole"], True)
        self.assertEqual(loaded["pyodideOptions"]["pyodideCdnUrl"], "")

    def test_write_h5p_content_files_keeps_non_default_advanced_option(self) -> None:
        store = ContentStore()
        payload = {
            "contentType": "text_only",
            "pythonRunner": "skulpt",
            "contents": [{"type": "text", "text": "Kurz"}],
            "advancedOptions": {
                "p5CdnUrl": "https://cdn.example/p5.js",
                "showConsole": True,
            },
        }

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            store.write_h5p_content_files(target, payload)
            raw_yaml = (target / "content.yml").read_text(encoding="utf-8")
            loaded = store.read_h5p_content_payload(target)

        self.assertIn("advancedOptions", raw_yaml)
        self.assertIn("p5CdnUrl", raw_yaml)
        self.assertIn("https://cdn.example/p5.js", raw_yaml)
        self.assertEqual(loaded["advancedOptions"]["p5CdnUrl"], "https://cdn.example/p5.js")


class RuntimePreparationServiceTests(unittest.TestCase):
    def test_ensure_ready_imports_once_and_sets_ready_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            content_root = Path(tmp) / "content"
            content_root.mkdir(parents=True)
            package_path = Path(tmp) / "task.h5p"
            question = DummyQuestion(identifier="q1", runtime_content_id="cid", package_path=package_path)
            service = RuntimePreparationService(content_root)
            calls: list[str] = []

            def compute_hash(_: DummyQuestion) -> str:
                return "h1"

            def write_package(q: DummyQuestion) -> Path:
                calls.append("write")
                q.package_path.write_text("x", encoding="utf-8")
                return q.package_path

            def import_into_runtime(q: DummyQuestion) -> None:
                calls.append("import")
                (content_root / q.runtime_content_id).mkdir(parents=True, exist_ok=True)

            service.ensure_ready(
                question,
                compute_hash=compute_hash,
                write_package=write_package,
                import_into_runtime=import_into_runtime,
            )
            self.assertEqual(calls, ["write", "import"])
            self.assertTrue(service.is_ready(question, compute_hash))

            service.ensure_ready(
                question,
                compute_hash=compute_hash,
                write_package=write_package,
                import_into_runtime=import_into_runtime,
            )
            self.assertEqual(calls, ["write", "import"])

    def test_start_preparation_sets_error_state_when_worker_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            content_root = Path(tmp) / "content"
            content_root.mkdir(parents=True)
            package_path = Path(tmp) / "task.h5p"
            question = DummyQuestion(identifier="q1", runtime_content_id="cid", package_path=package_path)
            service = RuntimePreparationService(content_root)

            def compute_hash(_: DummyQuestion) -> str:
                return "h1"

            def write_package(q: DummyQuestion) -> Path:
                q.package_path.write_text("x", encoding="utf-8")
                return q.package_path

            def import_into_runtime(_: DummyQuestion) -> None:
                raise RuntimeError("boom")

            service.start_preparation(
                question,
                compute_hash=compute_hash,
                write_package=write_package,
                import_into_runtime=import_into_runtime,
            )

            deadline = time.time() + 2.0
            state = service.state(question, compute_hash)
            while state["status"] == "preparing" and time.time() < deadline:
                time.sleep(0.02)
                state = service.state(question, compute_hash)

            self.assertEqual(state["status"], "error")
            self.assertIn("boom", state["error"])


class PreviewControllerTests(unittest.TestCase):
    def test_preview_status_returns_not_found_for_missing_course(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            controller = PreviewController(
                courses_dir=Path(tmp),
                load_course_preview_state=lambda _path: ([], ""),
                get_runtime_preparation_state=lambda _q: {"status": "idle", "error": ""},
                start_runtime_question_preparation=lambda _q: None,
                is_runtime_question_ready=lambda _q: False,
                build_runtime_proxy_path=lambda *_args, **_kwargs: "/runtime/view/x",
                render_preview_waiting_page=lambda *_args, **_kwargs: "<html></html>",
            )

            result = controller.preview_status("missing-course", "q1")
            self.assertEqual(result.status_code, HTTPStatus.NOT_FOUND)
            self.assertIn("Kurs", result.error_message)

    def test_preview_route_redirects_when_ready(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            course_dir = Path(tmp) / "python-2026"
            course_dir.mkdir(parents=True)
            question = DummyQuestion(identifier="q1", runtime_content_id="cid", package_path=Path(tmp) / "q1.h5p")

            controller = PreviewController(
                courses_dir=Path(tmp),
                load_course_preview_state=lambda _path: ([question], ""),
                get_runtime_preparation_state=lambda _q: {"status": "ready", "error": ""},
                start_runtime_question_preparation=lambda _q: None,
                is_runtime_question_ready=lambda _q: True,
                build_runtime_proxy_path=lambda *_args, **_kwargs: "/runtime/view/cid",
                render_preview_waiting_page=lambda *_args, **_kwargs: "<html></html>",
            )

            result = controller.preview_route("python-2026", "q1", mode="view", simple=False)
            self.assertEqual(result.status_code, HTTPStatus.FOUND)
            self.assertEqual(result.redirect_url, "/runtime/view/cid")
