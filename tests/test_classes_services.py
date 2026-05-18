from __future__ import annotations

import tempfile
import threading
import time
import shutil
import sys
import unittest
from dataclasses import dataclass
from http import HTTPStatus
from pathlib import Path
from zipfile import ZipFile

from scripts.classes.cli.runtime_cli_service import RuntimeCliService
from scripts.classes.content_store import ContentStore
from scripts.classes.h5p_file_service import H5PFileService
from scripts.classes.preview_controller import PreviewController
from scripts.classes.runtime_preparation import RuntimePreparationService


@dataclass(slots=True)
class DummyQuestion:
    identifier: str
    runtime_content_id: str
    package_path: Path


class FailingRuntimeBackend:
    def ensure_h5p_runtime_libraries(self) -> None:
        pass

    def get_h5p_cli_command(self) -> list[str]:
        return [sys.executable, "-c", "import sys; print('runtime failed'); sys.exit(7)"]

    def run_h5p_cli(self, args: list[str], cwd: Path):
        raise AssertionError("not used")


class RuntimeCliServiceTests(unittest.TestCase):
    def test_ensure_h5p_server_config_disables_livereload_watcher(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = RuntimeCliService(
                workspace_lock=threading.RLock(),
                runtime_dir=Path(tmp),
                runtime_content_dir=Path(tmp) / "content",
                backend=FailingRuntimeBackend(),
            )

            service.ensure_h5p_server_config()

            config = (Path(tmp) / "config.js").read_text(encoding="utf-8")
            self.assertIn("watch: false", config)

    def test_ensure_h5p_runtime_server_reports_early_process_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = RuntimeCliService(
                workspace_lock=threading.RLock(),
                runtime_dir=Path(tmp),
                runtime_content_dir=Path(tmp) / "content",
                backend=FailingRuntimeBackend(),
            )

            with self.assertRaisesRegex(RuntimeError, "runtime failed"):
                service.ensure_h5p_runtime_server(8876)


class ContentStoreTests(unittest.TestCase):
    def _make_h5p_file_service(self) -> H5PFileService:
        store = ContentStore()
        return H5PFileService(
            courses_dir=Path("courses"),
            ensure_directory=lambda path: path.mkdir(parents=True, exist_ok=True),
            read_yaml=store.read_yaml,
            read_h5p_content_payload=store.read_h5p_content_payload,
            write_h5p_content_files=store.write_h5p_content_files,
            write_json=lambda path, payload: path.write_text("{}\n", encoding="utf-8"),
        )

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

            raw_mdx = (target / "content.mdx").read_text(encoding="utf-8")
            loaded = store.read_h5p_content_payload(target)

        self.assertIn("Line 1", raw_mdx)
        self.assertIn("```python\nprint('a')", raw_mdx)
        self.assertNotIn("\\n", raw_mdx)
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
                        "defaultImages": [],
                        "enableSoundUploads": False,
                        "sourceFiles": [],
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
                {
                    "type": "code",
                    "code": "print('ok')\n",
                    "options": {
                        "showEditor": True,
                        "enableImageUploads": False,
                        "defaultImages": [],
                        "enableSoundUploads": False,
                        "sourceFiles": [],
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
            ],
            "editorSettings": {
                "options": {
                    "enableImageUploads": False,
                    "defaultImages": [],
                    "enableSoundUploads": False,
                    "sourceFiles": [],
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
            raw_yaml = (target / "settings.yml").read_text(encoding="utf-8")
            raw_mdx = (target / "content.mdx").read_text(encoding="utf-8")
            loaded = store.read_h5p_content_payload(target)

        self.assertIn("Kurz", raw_mdx)
        self.assertNotIn("showEditor", raw_yaml)
        self.assertNotIn("editorMode", raw_yaml)
        self.assertNotIn("blocklyCategories", raw_yaml)
        self.assertNotIn("editorSettings", raw_yaml)
        self.assertNotIn("gradingSettings", raw_yaml)
        self.assertNotIn("advancedOptions", raw_yaml)
        self.assertNotIn("pyodideOptions", raw_yaml)

        self.assertNotIn("options", loaded["contents"][0])
        self.assertNotIn("blocklyCategories", loaded["contents"][0])
        self.assertEqual(loaded["contents"][1]["options"]["showEditor"], True)
        self.assertEqual(loaded["contents"][1]["options"]["editorMode"], "code")
        self.assertEqual(loaded["contents"][1]["blocklyCategories"]["variables"], True)
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
            raw_yaml = (target / "settings.yml").read_text(encoding="utf-8")
            loaded = store.read_h5p_content_payload(target)

        self.assertIn("advancedOptions", raw_yaml)
        self.assertIn("p5CdnUrl", raw_yaml)
        self.assertIn("https://cdn.example/p5.js", raw_yaml)
        self.assertEqual(loaded["advancedOptions"]["p5CdnUrl"], "https://cdn.example/p5.js")

    def test_write_h5p_content_files_preserves_noeditor_code_fences(self) -> None:
        store = ContentStore()
        payload = {
            "contentType": "text_only",
            "pythonRunner": "skulpt",
            "contents": [
                {
                    "type": "code",
                    "code": "print('nur ansehen')\n",
                    "options": {"showEditor": False},
                }
            ],
        }

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            store.write_h5p_content_files(target, payload)
            raw_mdx = (target / "content.mdx").read_text(encoding="utf-8")
            loaded = store.read_h5p_content_payload(target)

        self.assertIn("```python noeditor", raw_mdx)
        self.assertEqual(loaded["contents"][0]["options"]["showEditor"], False)

    def test_write_h5p_content_files_unescapes_entities_and_uses_backticks_for_editor_code(self) -> None:
        store = ContentStore()
        payload = {
            "contentType": "text_only",
            "pythonRunner": "skulpt",
            "contents": [
                {
                    "type": "code",
                    "code": 'turtle.color(&quot;yellow&quot;)\n',
                },
                {
                    "type": "text",
                    "text": 'Farben mit &quot;Namen&quot; wählen.',
                },
            ],
            "editorSettings": {
                "instructions": 'Nutze &quot;yellow&quot; als Farbe.',
                "startingCode": 'print(&quot;Hello World&quot;)\n',
                "preCode": '',
                "postCode": '',
                "options": {"allowAddingFiles": False},
            },
        }

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            store.write_h5p_content_files(target, payload)
            raw_yaml = (target / "settings.yml").read_text(encoding="utf-8")
            raw_mdx = (target / "content.mdx").read_text(encoding="utf-8")
            loaded = store.read_h5p_content_payload(target)

        self.assertIn('turtle.color("yellow")', raw_mdx)
        self.assertIn('Farben mit "Namen" wählen.', raw_mdx)
        self.assertNotIn("editorSettings", raw_yaml)
        self.assertNotIn("gradingSettings", raw_yaml)
        self.assertNotIn('&quot;', raw_yaml + raw_mdx)
        self.assertEqual(loaded["contents"][0]["code"], 'turtle.color("yellow")\n')

    def test_read_h5p_content_payload_supports_noeditor_code_fences(self) -> None:
        store = ContentStore()
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            (target / "settings.yml").write_text(
                "contentType: text_only\npythonRunner: skulpt\n",
                encoding="utf-8",
            )
            (target / "content.mdx").write_text(
                "```python noeditor\nprint('nur ansehen')\n```\n\n"
                "```text\nAusgabe ohne Editor\n```\n",
                encoding="utf-8",
            )

            loaded = store.read_h5p_content_payload(target)

        self.assertEqual(loaded["contents"][0]["options"]["showEditor"], False)
        self.assertEqual(loaded["contents"][1]["options"]["showEditor"], False)

    def test_read_h5p_content_payload_converts_standalone_markdown_images(self) -> None:
        store = ContentStore()
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            (target / "settings.yml").write_text(
                "contentType: text_only\npythonRunner: skulpt\n",
                encoding="utf-8",
            )
            (target / "content.mdx").write_text(
                "Vorher\n\n![Diagramm](images/diagramm.png)\n\nNachher\n",
                encoding="utf-8",
            )

            loaded = store.read_h5p_content_payload(target)

        self.assertEqual(loaded["contents"][0]["type"], "text")
        self.assertEqual(loaded["contents"][1]["type"], "image")
        self.assertEqual(loaded["contents"][1]["image"]["path"], "images/diagramm.png")
        self.assertEqual(loaded["contents"][1]["image"]["mime"], "image/png")
        self.assertEqual(loaded["contents"][2]["type"], "text")

    @unittest.skipUnless(shutil.which("magick") or shutil.which("convert"), "ImageMagick fehlt")
    def test_h5p_file_service_renders_editable_images_and_skips_sources_in_archive(self) -> None:
        service = self._make_h5p_file_service()
        with tempfile.TemporaryDirectory() as tmp:
            source_dir = Path(tmp)
            (source_dir / "settings.yml").write_text(
                "contentType: text_only\npythonRunner: skulpt\n",
                encoding="utf-8",
            )
            (source_dir / "content.mdx").write_text(
                "![Diagramm](images/diagramm.png)\n",
                encoding="utf-8",
            )
            (source_dir / "editable-images").mkdir()
            (source_dir / "editable-images" / "diagramm.svg").write_text(
                '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="10">'
                '<rect width="20" height="10" fill="red"/></svg>',
                encoding="utf-8",
            )

            archive_path = source_dir / "out.h5p"
            with ZipFile(archive_path, "w") as archive:
                service.write_h5p_archive_from_directory(archive, source_dir)

            with ZipFile(archive_path) as archive:
                names = archive.namelist()

        self.assertIn("content/images/diagramm.png", names)
        self.assertNotIn("content/editable-images/diagramm.svg", names)

    def test_write_h5p_content_files_moves_ide_settings_into_mdx(self) -> None:
        store = ContentStore()
        payload = {
            "contentType": "ide_only",
            "pythonRunner": "skulpt",
            "contents": [],
            "editorSettings": {
                "instructions": "Aendere den Code.",
                "startingCode": "print('start')\n",
                "preCode": "",
                "postCode": "",
                "options": {"allowAddingFiles": False},
            },
            "gradingSettings": {
                "gradingMethod": "ioTestCases",
                "targetCode": "print('ziel')\n",
                "testCases": [{"hidden": False, "inputs": [], "outputs": [{"output": "ziel"}]}],
            },
        }

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            store.write_h5p_content_files(target, payload)
            raw_settings = (target / "settings.yml").read_text(encoding="utf-8")
            raw_mdx = (target / "content.mdx").read_text(encoding="utf-8")
            loaded = store.read_h5p_content_payload(target)

        self.assertNotIn("editorSettings", raw_settings)
        self.assertNotIn("gradingSettings", raw_settings)
        self.assertIn("<Instructions>", raw_mdx)
        self.assertIn("```python editor:startingCode", raw_mdx)
        self.assertIn("```python grading:targetCode", raw_mdx)
        self.assertIn("```yaml grading", raw_mdx)
        self.assertEqual(loaded["editorSettings"]["instructions"], "Aendere den Code.")
        self.assertEqual(loaded["editorSettings"]["startingCode"], "print('start')\n")
        self.assertEqual(loaded["gradingSettings"]["targetCode"], "print('ziel')\n")
        self.assertEqual(loaded["gradingSettings"]["gradingMethod"], "ioTestCases")

    def test_read_h5p_content_payload_rejects_p5_or_turtle_with_pyodide(self) -> None:
        store = ContentStore()
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            (target / "settings.yml").write_text(
                "contentType: text_only\npythonRunner: pyodide\n",
                encoding="utf-8",
            )
            (target / "content.mdx").write_text(
                "```python\nimport p5\n\np5.run()\n```\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "pythonRunner: skulpt"):
                store.read_h5p_content_payload(target)

    def test_read_h5p_content_payload_allows_p5_or_turtle_with_skulpt(self) -> None:
        store = ContentStore()
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            (target / "settings.yml").write_text(
                "contentType: text_only\npythonRunner: skulpt\n",
                encoding="utf-8",
            )
            (target / "content.mdx").write_text(
                "```python\nimport turtle\n\nturtle.forward(50)\n```\n",
                encoding="utf-8",
            )

            loaded = store.read_h5p_content_payload(target)

        self.assertEqual(loaded["pythonRunner"], "skulpt")

    def test_read_h5p_content_payload_enables_miniworlds_images_and_files(self) -> None:
        store = ContentStore()
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            (target / "settings.yml").write_text(
                "contentType: text_only\n"
                "pythonRunner: pyodide\n"
                "pyodideOptions:\n"
                "  packages:\n"
                "  - package: miniworlds\n",
                encoding="utf-8",
            )
            (target / "content.mdx").write_text(
                "```python\nimport miniworlds\n\nworld = miniworlds.World(300, 200)\nworld.run()\n```\n",
                encoding="utf-8",
            )

            loaded = store.read_h5p_content_payload(target)

        code_item = next(item for item in loaded["contents"] if item["type"] == "code")
        options = code_item["options"]
        self.assertEqual(options["enableImageUploads"], True)
        self.assertEqual(options["allowAddingFiles"], True)
        self.assertEqual(options["sourceFiles"], [])

    def test_read_h5p_content_payload_uses_mdx_images_as_miniworlds_default_images(self) -> None:
        store = ContentStore()
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            (target / "settings.yml").write_text(
                "contentType: text_only\n"
                "pythonRunner: pyodide\n"
                "pyodideOptions:\n"
                "  packages:\n"
                "  - package: miniworlds\n",
                encoding="utf-8",
            )
            (target / "content.mdx").write_text(
                "![Hintergrund](images/hintergrund.png)\n\n"
                "```python\nimport miniworlds\n\nworld = miniworlds.World(300, 200)\nworld.run()\n```\n",
                encoding="utf-8",
            )

            loaded = store.read_h5p_content_payload(target)

        code_item = next(item for item in loaded["contents"] if item["type"] == "code")
        default_images = code_item["options"]["defaultImages"]
        self.assertEqual(default_images[0]["fileName"], "hintergrund.png")
        self.assertEqual(default_images[0]["image"]["path"], "images/hintergrund.png")

    def test_read_h5p_content_payload_uses_images_folder_as_miniworlds_default_images(self) -> None:
        store = ContentStore()
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            (target / "images").mkdir()
            (target / "images" / "char.png").write_bytes(b"png")
            (target / "settings.yml").write_text(
                "contentType: text_only\n"
                "pythonRunner: pyodide\n"
                "pyodideOptions:\n"
                "  packages:\n"
                "  - package: miniworlds\n",
                encoding="utf-8",
            )
            (target / "content.mdx").write_text(
                "```python\nimport miniworlds\n\nworld = miniworlds.World(300, 200)\nworld.run()\n```\n",
                encoding="utf-8",
            )

            loaded = store.read_h5p_content_payload(target)

        code_item = next(item for item in loaded["contents"] if item["type"] == "code")
        default_images = code_item["options"]["defaultImages"]
        self.assertEqual(default_images[0]["fileName"], "char.png")
        self.assertEqual(default_images[0]["image"]["path"], "images/char.png")


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
