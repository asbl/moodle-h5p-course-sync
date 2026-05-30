from __future__ import annotations

import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT_DIR = Path(__file__).resolve().parent.parent
DEMO_COURSE_DIR = ROOT_DIR / "courses" / "h5p-demo"


def load_tasks_module():
    spec = importlib.util.spec_from_file_location("course_sync_tasks", ROOT_DIR / "tasks.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load tasks.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class H5PDemoWorkflowTests(unittest.TestCase):
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
            ],
        )


if __name__ == "__main__":
    unittest.main()
