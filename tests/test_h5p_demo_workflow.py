from __future__ import annotations

import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.classes.h5p_file_service import H5PFileService


ROOT_DIR = Path(__file__).resolve().parent.parent
DEMO_COURSE_DIR = ROOT_DIR / "courses" / "h5p-demo"
EN_DEMO_COURSE_DIR = ROOT_DIR / "courses" / "h5p-demo-en"


def load_tasks_module():
    spec = importlib.util.spec_from_file_location("course_sync_tasks", ROOT_DIR / "tasks.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load tasks.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class H5PDemoWorkflowTests(unittest.TestCase):
    def test_h5p_archive_filter_skips_library_development_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            library_dir = Path(temp_dir) / "H5P.Components-1.0"
            library_dir.mkdir()
            (library_dir / ".h5pignore").write_text("src\neslint.config.mjs\n", encoding="utf-8")
            service = H5PFileService(
                courses_dir=ROOT_DIR / "courses",
                ensure_directory=lambda path: path.mkdir(parents=True, exist_ok=True),
                read_yaml=lambda _path: {},
                read_h5p_content_payload=lambda _path: {},
                write_h5p_content_files=lambda _path, _payload: None,
                write_json=lambda _path, _payload: None,
            )

            self.assertTrue(service._should_skip_archive_path("H5P.Components-1.0/LICENSE", archive_root=Path(temp_dir)))
            self.assertTrue(service._should_skip_archive_path("H5P.Components-1.0/eslint.config.mjs", archive_root=Path(temp_dir)))
            self.assertTrue(service._should_skip_archive_path("H5P.Components-1.0/vitest.config.mjs", archive_root=Path(temp_dir)))
            self.assertTrue(service._should_skip_archive_path("H5P.Components-1.0/src/components/button.js", archive_root=Path(temp_dir)))
            self.assertFalse(service._should_skip_archive_path("H5P.Components-1.0/dist/h5p-components.js", archive_root=Path(temp_dir)))

    def test_h5p_demo_covers_question_libraries(self) -> None:
        expected_libraries = {
            "H5P.PythonQuestion",
            "H5P.JavaQuestion",
            "H5P.SQLQuestion",
            "H5P.AutomataQuestion",
        }

        self.assertTrue(DEMO_COURSE_DIR.exists(), "h5p-demo course is missing")
        h5p_json_paths = sorted(DEMO_COURSE_DIR.glob("h5p/**/h5p.json"))
        self.assertGreaterEqual(len(h5p_json_paths), len(expected_libraries))

        actual_libraries = {
            json.loads(path.read_text(encoding="utf-8")).get("mainLibrary")
            for path in h5p_json_paths
        }
        self.assertTrue(
            expected_libraries.issubset(actual_libraries),
            f"Missing demo libraries: {sorted(expected_libraries - actual_libraries)}",
        )

    def test_h5p_demo_index_references_every_question_chapter(self) -> None:
        index_source = (DEMO_COURSE_DIR / "index.mdx").read_text(encoding="utf-8")
        chapter_paths = sorted((DEMO_COURSE_DIR / "chapters").glob("*.mdx"))

        self.assertGreaterEqual(len(chapter_paths), 10)
        for chapter_path in chapter_paths:
            expected_src = f'./chapters/{chapter_path.name}'
            self.assertIn(expected_src, index_source)

    def test_english_h5p_demo_stays_structurally_synced_with_german_demo(self) -> None:
        self.assertTrue(EN_DEMO_COURSE_DIR.exists(), "h5p-demo-en course is missing")

        german_chapters = sorted(path.name for path in (DEMO_COURSE_DIR / "chapters").glob("*.mdx"))
        english_chapters = sorted(path.name for path in (EN_DEMO_COURSE_DIR / "chapters").glob("*.mdx"))
        self.assertEqual(english_chapters, german_chapters)

        german_h5p_json = {
            path.relative_to(DEMO_COURSE_DIR / "h5p"): json.loads(path.read_text(encoding="utf-8")).get("mainLibrary")
            for path in sorted((DEMO_COURSE_DIR / "h5p").glob("**/h5p.json"))
        }
        english_h5p_json = {
            path.relative_to(EN_DEMO_COURSE_DIR / "h5p"): json.loads(path.read_text(encoding="utf-8")).get("mainLibrary")
            for path in sorted((EN_DEMO_COURSE_DIR / "h5p").glob("**/h5p.json"))
        }
        self.assertEqual(english_h5p_json, german_h5p_json)

    def test_english_h5p_demo_uses_english_language_metadata(self) -> None:
        h5p_json_paths = sorted(EN_DEMO_COURSE_DIR.glob("h5p/**/h5p.json"))
        self.assertGreaterEqual(len(h5p_json_paths), 10)

        for path in h5p_json_paths:
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertNotEqual(data.get("language"), "de", path)
            self.assertNotEqual(data.get("defaultLanguage"), "de", path)
            if "language" in data:
                self.assertEqual(data["language"], "en", path)
            if "defaultLanguage" in data:
                self.assertEqual(data["defaultLanguage"], "en", path)

    def test_demo_python_tests_use_mdx_payload_instead_of_empty_inferred_sidecar(self) -> None:
        from scripts.main import parse_course

        for course_dir in (DEMO_COURSE_DIR, EN_DEMO_COURSE_DIR):
            _source, questions, _rendered = parse_course(course_dir)
            question = next(item for item in questions if item.identifier == "python-tests")

            self.assertEqual("", question.source_package_path)
            self.assertIsNone(question.h5p_content)
            self.assertIn("celsius", question.starter_code.lower())
            self.assertGreater(len(question.test_cases), 0)

    def test_sync_h5p_demo_courses_moodle_builds_and_uploads_all_chapters(self) -> None:
        tasks = load_tasks_module()
        german_chapters = tasks._course_chapters("h5p-demo")
        english_chapters = tasks._course_chapters("h5p-demo-en")
        calls: list[tuple[tuple[str, ...], Path]] = []

        def fake_run(args, *, check, cwd):
            self.assertTrue(check)
            calls.append((tuple(str(item) for item in args), Path(cwd)))
            return subprocess.CompletedProcess(args=args, returncode=0)

        with patch.object(tasks.subprocess, "run", side_effect=fake_run):
            tasks.sync_h5p_demo_courses_moodle.body(None, headless=True)

        expected_calls = [
            ((tasks.PYTHON, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"), ROOT_DIR),
            ((tasks.PYTHON, "-m", "scripts.main", "build", "h5p-demo"), ROOT_DIR),
            ((tasks.PYTHON, "-m", "scripts.main", "build", "h5p-demo-en"), ROOT_DIR),
        ]
        expected_calls.extend(
            ((tasks.PYTHON, "-m", "scripts.main", "upload-chapter-moodle", "h5p-demo", chapter, "--headless"), ROOT_DIR)
            for chapter in german_chapters
        )
        expected_calls.extend(
            ((tasks.PYTHON, "-m", "scripts.main", "upload-chapter-moodle", "h5p-demo-en", chapter, "--headless"), ROOT_DIR)
            for chapter in english_chapters
        )

        self.assertEqual(calls, expected_calls)

    def test_release_questions_workflow_orders_release_update_tests_and_demo_build(self) -> None:
        tasks = load_tasks_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            h5p_dev_dir = Path(temp_dir) / "h5p-dev"
            h5p_dev_dir.mkdir()

            calls: list[tuple[tuple[str, ...], Path]] = []

            def fake_run(args, *, check, cwd):
                self.assertTrue(check)
                calls.append((tuple(str(item) for item in args), Path(cwd)))
                return subprocess.CompletedProcess(args=args, returncode=0)

            with patch.object(tasks.subprocess, "run", side_effect=fake_run):
                tasks.release_questions_workflow.body(
                    None,
                    h5p_dev_dir=str(h5p_dev_dir),
                    course="h5p-demo",
                    english_course="h5p-demo-en",
                    tag="v6.90.0",
                    dry_run=True,
                )

        self.assertEqual(
            calls,
            [
                ((tasks._invoke_binary(h5p_dev_dir), "pack-all"), h5p_dev_dir.resolve()),
                ((tasks._invoke_binary(h5p_dev_dir), "deploy.release", "--all", "--dry-run"), h5p_dev_dir.resolve()),
                ((tasks.PYTHON, "-m", "scripts.main", "update-h5p-libraries", "--tag", "v6.90.0"), ROOT_DIR),
                ((tasks.PYTHON, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"), ROOT_DIR),
                ((tasks.PYTHON, "-m", "scripts.main", "build", "h5p-demo"), ROOT_DIR),
                ((tasks.PYTHON, "-m", "scripts.main", "build", "h5p-demo-en"), ROOT_DIR),
            ],
        )


if __name__ == "__main__":
    unittest.main()
