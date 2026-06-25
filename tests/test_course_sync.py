import json
import os
import tarfile
import tempfile
import threading
import unittest
from hashlib import sha1
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import yaml

from scripts.main import (
    MoodleApiClient,
    MoodleH5PActivity,
    SyncMetadata,
    SyncMetadataEntry,
    build_moodle_ping_report,
    build_h5p_content,
    build_course_status,
    build_imported_question_from_h5p_package,
    compute_question_hash,
    export_static_site,
    extract_h5p_package_url_from_activity_html,
    import_moodle_course,
    load_course_preview_state,
    load_sync_metadata,
    make_stable_identifier,
    parse_course,
    prepare_preview_runtime,
    render_imported_question_mdx,
    render_course_page,
    resolve_moodle_client,
    rewrite_runtime_html,
    save_sync_metadata,
    sync_course,
    write_h5p_package,
    write_source_package_sidecar,
)
from scripts.classes.h5p_runtime_manager import build_runtime_content_id


class CourseSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp_dir.name)
        self.course_dir = self.root / "courses" / "python-2026"
        (self.course_dir / "h5p").mkdir(parents=True)
        (self.course_dir / "assets").mkdir()
        (self.course_dir / "index.mdx").write_text(
            """# Python Kurs

<PythonQuestion
  identifier="12eck"
  title="Zwölfeck zeichnen"
  instructions="Schreibe ein Programm, das ein Zwölfeck zeichnet."
  runner="pyodide"
  packages="miniworlds"
  gradingMethod="ioTestCases"
/>

```python question:12eck starter
import miniworlds

world = miniworlds.World(400, 400)
world.run()
```

```python question:12eck solution
print("fertig")
```

```json question:12eck testcase
{"hidden": false, "inputs": [], "outputs": ["fertig"]}
```

<PythonQuestion
    identifier="quadrat"
    title="Quadrat zeichnen"
    instructions="Schreibe ein Programm, das ein Quadrat zeichnet."
    runner="pyodide"
    packages="miniworlds"
    gradingMethod="ioTestCases"
/>

```python question:quadrat starter
import miniworlds

world = miniworlds.World(300, 300)
pen = miniworlds.Turtle()
world.run()
```

```python question:quadrat solution
print("quadrat")
```

```json question:quadrat testcase
{"hidden": true, "inputs": [], "outputs": ["quadrat"]}
```
""",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_parse_course_reads_python_questions(self) -> None:
        _, questions, rendered_source = parse_course(self.course_dir)

        self.assertEqual(len(questions), 2)
        first_question = questions[0]
        second_question = questions[1]
        self.assertEqual(first_question.identifier, "12eck")
        self.assertEqual(first_question.packages, ["miniworlds"])
        self.assertEqual(second_question.identifier, "quadrat")
        self.assertIn("[[[PYTHON_QUESTION:12eck]]]", rendered_source)
        self.assertIn("[[[PYTHON_QUESTION:quadrat]]]", rendered_source)

    def test_parse_course_defaults_plain_python_questions_to_skulpt(self) -> None:
        (self.course_dir / "index.mdx").write_text(
            """# Python Kurs

<PythonQuestion
  identifier="text-test"
  title="Text"
/>

```python question:text-test starter
print("bereit")
```
""",
            encoding="utf-8",
        )

        _, questions, _ = parse_course(self.course_dir)

        self.assertEqual(questions[0].runner, "skulpt")

    def test_parse_course_defaults_miniworlds_questions_to_pyodide(self) -> None:
        (self.course_dir / "index.mdx").write_text(
            """# Python Kurs

<PythonQuestion
  identifier="welt"
  title="Welt"
  packages="miniworlds"
/>

```python question:welt starter
import miniworlds
```
""",
            encoding="utf-8",
        )

        _, questions, _ = parse_course(self.course_dir)

        self.assertEqual(questions[0].runner, "pyodide")

    def test_parse_course_rejects_turtle_question_with_pyodide(self) -> None:
        (self.course_dir / "index.mdx").write_text(
            """# Python Kurs

<PythonQuestion
  identifier="turtle-test"
  title="Turtle"
  runner="pyodide"
/>

```python question:turtle-test starter
import turtle
turtle.forward(50)
```
""",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(ValueError, "pythonRunner: skulpt"):
            parse_course(self.course_dir)

    def test_parse_course_expands_chapter_files_and_sets_h5p_subdir(self) -> None:
        chapters_dir = self.course_dir / "chapters"
        chapters_dir.mkdir()
        (self.course_dir / "index.mdx").write_text(
            '# Python Kurs\n\n<Chapter src="./chapters/010-einfuehrung.mdx" title="Einführung" />\n',
            encoding="utf-8",
        )
        (chapters_dir / "010-einfuehrung.mdx").write_text(
            """## Einführung

<PythonQuestion
  identifier="intro"
  title="Intro"
  instructions="Los gehts."
/>
""",
            encoding="utf-8",
        )

        _, questions, rendered_source = parse_course(self.course_dir)

        self.assertEqual(len(questions), 1)
        self.assertEqual(questions[0].identifier, "intro")
        self.assertEqual(questions[0].h5p_subdir, "010-einfuehrung")
        self.assertEqual(questions[0].package_path, self.course_dir / "build" / "h5p" / "010-einfuehrung" / "intro.h5p")
        self.assertIn("[[[PYTHON_QUESTION:intro]]]", rendered_source)

    def test_parse_course_rejects_duplicate_identifiers(self) -> None:
        (self.course_dir / "index.mdx").write_text(
            """# Python Kurs

<PythonQuestion identifier="dup" title="Eins" instructions="A" />
<PythonQuestion identifier="dup" title="Zwei" instructions="B" />
""",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(ValueError, "mehrfach vergeben"):
            parse_course(self.course_dir)

    def test_build_h5p_content_contains_editor_and_tests(self) -> None:
        _, questions, _ = parse_course(self.course_dir)
        question = questions[0]
        payload = build_h5p_content(question)

        self.assertEqual(payload["editorSettings"]["startingCode"].splitlines()[0], "import miniworlds")
        self.assertEqual(payload["gradingSettings"]["gradingMethod"], "ioTestCases")
        self.assertEqual(payload["gradingSettings"]["testCases"][0]["outputs"][0]["output"], "fertig")
        self.assertEqual(payload["editorSettings"]["options"]["enableImageUploads"], True)
        self.assertEqual(payload["editorSettings"]["options"]["allowAddingFiles"], True)

    def test_render_course_page_embeds_preview_link(self) -> None:
        html = render_course_page(self.course_dir)
        self.assertEqual(html.count("<iframe"), 0)
        self.assertIn('href="build/h5p/12eck.h5p"', html)
        self.assertIn('href="build/h5p/quadrat.h5p"', html)
        self.assertIn('class="course-section course-section-h1"', html)
        self.assertIn('class="course-section-body"', html)
        self.assertNotIn('PythonQuestion', html)
        self.assertNotIn('Vorschau und Bearbeitung nur auf Klick', html)
        self.assertNotIn('data-open-modal="true"', html)
        self.assertIn('class="question-toolbar"', html)
        self.assertNotIn('/preview-status/python-2026/12eck', html)
        self.assertNotIn('id="preview-modal"', html)
        self.assertIn('>H5P herunterladen<', html)
        self.assertNotIn('>Öffnen<', html)
        self.assertNotIn('>Edit<', html)
        self.assertNotIn('>Split View<', html)
        self.assertNotIn('>Löschen<', html)
        self.assertNotIn('delete-runtime', html)
        self.assertNotIn('<details', html)

    def test_export_static_site_writes_index_and_h5p_packages(self) -> None:
        output_dir = self.root / "public"

        written_paths = export_static_site(output_dir, self.course_dir)

        self.assertIn(output_dir / ".nojekyll", written_paths)
        self.assertTrue((output_dir / "index.html").is_file())
        course_index = output_dir / "courses" / "python-2026" / "index.html"
        self.assertTrue(course_index.is_file())
        self.assertTrue((output_dir / "h5p" / "python-2026" / "12eck.h5p").is_file())
        self.assertTrue((output_dir / "h5p" / "python-2026" / "quadrat.h5p").is_file())
        course_html = course_index.read_text(encoding="utf-8")
        self.assertIn('href="../../h5p/python-2026/12eck.h5p"', course_html)
        self.assertNotIn("/preview/", course_html)

    def test_export_static_site_rejects_project_root_as_output(self) -> None:
        from scripts import main as module

        original_root_dir = module.ROOT_DIR
        original_courses_dir = module.COURSES_DIR
        try:
            module.ROOT_DIR = self.root
            module.COURSES_DIR = self.root / "courses"
            with self.assertRaisesRegex(ValueError, "Export-Zielordner"):
                export_static_site(self.root, self.course_dir)
            with self.assertRaisesRegex(ValueError, "Export-Zielordner"):
                export_static_site(self.root / "courses", self.course_dir)
        finally:
            module.ROOT_DIR = original_root_dir
            module.COURSES_DIR = original_courses_dir

    def test_rewrite_runtime_html_rewrites_paths_and_hides_view_chrome(self) -> None:
        source = (
            '<head></head><body>'
            '<a href="/edit/x/y">Edit</a>'
            '<script>'
            'fetch("/remove/y"); '
            'const baseUrl = "http://localhost:8766"; '
            'const ajaxPath = "http://localhost:8766/edit/x/y/";'
            '</script>'
            '<div id="status"></div>'
            '</body>'
        )

        rewritten = rewrite_runtime_html(source, '/view/x/y', 'simple=1')

        self.assertIn('href="/runtime/edit/x/y"', rewritten)
        self.assertIn('fetch("/runtime/remove/y")', rewritten)
        self.assertIn('"/runtime"', rewritten)
        self.assertIn('"/runtime/edit/x/y/"', rewritten)
        self.assertNotIn('"/runtime/runtime"', rewritten)
        self.assertIn('#status', rewritten)
        self.assertIn('.menu-holder', rewritten)
        self.assertIn('#sessions', rewritten)
        self.assertIn('.submenu', rewritten)
        self.assertIn('a[href*="/split/"]', rewritten)
        self.assertIn('a[href*="/remove/"]', rewritten)
        self.assertIn("getElementById('sessions')", rewritten)

    def test_rewrite_runtime_html_hides_edit_and_split_chrome(self) -> None:
        source = '<head></head><body><div id="menu"></div><div class="h5p-cli-view"><div class="col50"></div></div></body>'

        rewritten_edit = rewrite_runtime_html(source, '/edit/x/y')
        rewritten_split = rewrite_runtime_html(source, '/split/x/y')

        self.assertIn('#menu', rewritten_edit)
        self.assertIn('.h5p-cli-view > .col50', rewritten_split)

    def test_runtime_content_id_namespaces_identifier_by_course(self) -> None:
        self.assertEqual(build_runtime_content_id("python-2026", "12eck"), "python-2026-12eck")
        self.assertEqual(build_runtime_content_id("python basics", "frage/1"), "python%20basics-frage%2F1")

    def test_make_stable_identifier_suffixes_duplicates(self) -> None:
        existing: set[str] = set()

        first = make_stable_identifier("Quadrat H5P", existing)
        second = make_stable_identifier("Quadrat H5P", existing)

        self.assertEqual(first, "quadrat-h5p")
        self.assertEqual(second, "quadrat-h5p-2")

    def test_make_stable_identifier_transliterates_umlauts(self) -> None:
        identifier = make_stable_identifier("Einführung: Größen & Maße", set())

        self.assertEqual(identifier, "einfuehrung-groessen-masse")

    def test_extract_h5p_package_url_from_activity_html_reads_embed_iframe(self) -> None:
        page_html = (
            '<iframe src="https://www.opencoding.de/h5p/embed.php?url=https%3A%2F%2Fwww.opencoding.de%2Fpluginfile.php%2F157%2Fmod_h5pactivity%2Fpackage%2F0%2Fhello-world.h5p&amp;preventredirect=1"></iframe>'
        )

        package_url = extract_h5p_package_url_from_activity_html(page_html)

        self.assertEqual(
            package_url,
            "https://www.opencoding.de/pluginfile.php/157/mod_h5pactivity/package/0/hello-world.h5p",
        )

    def test_build_imported_question_from_h5p_package_maps_python_question_fields(self) -> None:
        activity = MoodleH5PActivity(
            identifier="test-erste-aufgabe",
            title="Test: Erste Aufgabe",
            course_id=5,
            activity_id=135,
            instance_id=96,
            section_title="Einführung",
            url="https://example.invalid/mod/h5pactivity/view.php?id=135",
        )
        metadata_payload = {
            "title": "PythonQuestion",
            "mainLibrary": "H5P.PythonQuestion",
        }
        content_payload = {
            "pythonRunner": "skulpt",
            "pyodideOptions": {
                "packages": [{"package": "miniworlds"}],
            },
            "editorSettings": {
                "instructions": "**Aufgabe:** Gebe Informatik aus.",
                "preCode": "import math",
                "startingCode": 'print("Informatik")\n',
                "postCode": "print('fertig')",
                "options": {
                    "allowAddingFiles": True,
                    "sourceFiles": [
                        {
                            "fileName": "helper.py",
                            "code": "VALUE = 1\n",
                            "visibleToLearner": False,
                            "learnerEditable": False,
                        }
                    ],
                },
            },
            "gradingSettings": {
                "gradingMethod": "ioTestCases",
                "targetCode": 'print("Informatik")',
                "testCases": [
                    {
                        "hidden": False,
                        "inputs": [],
                        "outputs": ["Informatik"],
                    }
                ],
            },
            "advancedOptions": {
                "showConsole": False,
            },
        }

        question = build_imported_question_from_h5p_package(
            "python-2026",
            activity,
            metadata_payload,
            content_payload,
        )

        assert question is not None
        self.assertEqual(question.title, "Test: Erste Aufgabe")
        self.assertEqual(question.runner, "skulpt")
        self.assertEqual(question.packages, ["miniworlds"])
        self.assertEqual(question.instructions, "**Aufgabe:** Gebe Informatik aus.")
        self.assertEqual(question.pre_code, "import math")
        self.assertEqual(question.starter_code, 'print("Informatik")')
        self.assertEqual(question.solution_code, 'print("Informatik")')
        self.assertEqual(question.post_code, "print('fertig')")
        self.assertEqual(question.grading_method, "ioTestCases")
        self.assertFalse(question.show_console)
        self.assertTrue(question.allow_adding_files)
        self.assertEqual(question.source_files[0].file_name, "helper.py")
        self.assertFalse(question.source_files[0].visible_to_learner)
        self.assertFalse(question.source_files[0].learner_editable)
        self.assertEqual(question.test_cases[0].outputs, ["Informatik"])
        self.assertEqual(question.h5p_metadata, metadata_payload)
        self.assertEqual(question.h5p_content, content_payload)

    def test_build_imported_question_from_h5p_package_keeps_questionset_as_raw_h5p(self) -> None:
        activity = MoodleH5PActivity(
            identifier="quiz-division",
            title="Quiz Division",
            course_id=5,
            activity_id=165,
            instance_id=120,
            section_title="Quiz",
            intro="",
            url="https://example.invalid/mod/h5pactivity/view.php?id=165",
            package_url="https://example.invalid/pluginfile.php/165/mod_h5pactivity/package/0/quiz-division.h5p",
        )
        metadata_payload = {
            "title": "Quiz Division",
            "mainLibrary": "H5P.QuestionSet",
        }
        content_payload = {
            "questions": [
                {"params": {"question": "Was ist 12 / 3?"}},
                {"params": {"question": "Was ist 9 / 3?"}},
            ]
        }

        question = build_imported_question_from_h5p_package(
            "python-2026",
            activity,
            metadata_payload,
            content_payload,
        )

        assert question is not None
        self.assertEqual(question.main_library, "H5P.QuestionSet")
        self.assertEqual(question.package_url, activity.package_url)
        self.assertIn("2 Teilfragen", question.instructions)
        self.assertIn("12 / 3", question.instructions)
        self.assertEqual(question.h5p_metadata, metadata_payload)
        self.assertEqual(question.h5p_content, content_payload)

    def test_build_imported_question_from_h5p_package_keeps_text_only_python_question_as_raw_h5p(self) -> None:
        activity = MoodleH5PActivity(
            identifier="einfuehrung-farben",
            title="Einführung: Farben",
            course_id=5,
            activity_id=145,
            instance_id=105,
            section_title="Farben",
            intro="",
            url="https://example.invalid/mod/h5pactivity/view.php?id=145",
            package_url="https://example.invalid/pluginfile.php/168/mod_h5pactivity/package/0/einfuhrung-farben-891.h5p",
        )
        metadata_payload = {
            "title": "Einführung: Farben",
            "mainLibrary": "H5P.PythonQuestion",
        }
        content_payload = {
            "contentType": "text_only",
            "pythonRunner": "skulpt",
            "contents": [
                {"type": "text", "text": "Schau dir den folgenden Code an."},
                {"type": "code", "code": "import turtle\nturtle.forward(10)\n"},
            ],
            "editorSettings": {
                "instructions": "Schau dir den folgenden Code an.",
                "options": {},
            },
            "gradingSettings": {},
        }

        question = build_imported_question_from_h5p_package(
            "python-2026",
            activity,
            metadata_payload,
            content_payload,
        )

        assert question is not None
        self.assertEqual(question.main_library, "H5P.PythonQuestion")
        self.assertEqual(question.package_url, activity.package_url)
        self.assertEqual(question.runner, "skulpt")
        self.assertIn("Schau dir den folgenden Code an.", question.instructions)
        self.assertEqual(question.h5p_metadata, metadata_payload)
        self.assertEqual(question.h5p_content, content_payload)

    def test_render_and_parse_imported_question_roundtrips_exact_h5p_json_blocks(self) -> None:
        question = build_imported_question_from_h5p_package(
            "python-2026",
            MoodleH5PActivity(
                identifier="einfuehrung-farben",
                title="Einführung: Farben",
                course_id=5,
                activity_id=145,
                instance_id=105,
                section_title="Farben",
                intro="",
                url="https://example.invalid/mod/h5pactivity/view.php?id=145",
                package_url="https://example.invalid/pluginfile.php/168/mod_h5pactivity/package/0/einfuhrung-farben-891.h5p",
            ),
            {"title": "Einführung: Farben", "mainLibrary": "H5P.PythonQuestion"},
            {
                "contentType": "text_only",
                "pythonRunner": "skulpt",
                "contents": [
                    {"type": "text", "text": "Schau dir den folgenden Code an."},
                    {"type": "code", "code": "import turtle\nturtle.forward(10)\n"},
                ],
                "editorSettings": {"instructions": "Schau dir den folgenden Code an.", "options": {}},
                "gradingSettings": {},
            },
        )

        assert question is not None
        question.course_dir = self.course_dir
        source_archive = self.course_dir / "einfuehrung-farben.h5p"
        source_archive.write_bytes(
            self._build_h5p_archive_bytes(
                {"title": "Einführung: Farben", "mainLibrary": "H5P.PythonQuestion"},
                question.h5p_content or {},
            )
        )
        question.source_package_path = write_source_package_sidecar(question, source_archive)
        mdx = "# Python Kurs\n\n" + "\n".join(render_imported_question_mdx(question)) + "\n"
        (self.course_dir / "index.mdx").write_text(mdx, encoding="utf-8")

        _, questions, _ = parse_course(self.course_dir)

        self.assertEqual(len(questions), 1)
        parsed = questions[0]
        from scripts import main as module

        self.assertEqual(parsed.h5p_metadata["title"], question.h5p_metadata["title"])
        self.assertEqual(parsed.h5p_metadata["mainLibrary"], question.h5p_metadata["mainLibrary"])
        self.assertEqual(
            module.component_syncer().build_editable_h5p_payload(parsed),
            module.component_syncer().build_editable_h5p_payload(question),
        )
        self.assertEqual(parsed.source_package_path, "h5p/einfuehrung-farben")
        self.assertNotIn("h5p={{", mdx)
        self.assertNotIn("title=", mdx)
        self.assertNotIn("instructions=", mdx)
        self.assertNotIn("runner=", mdx)

    def test_prepare_preview_runtime_prepares_all_questions_for_selected_course(self) -> None:
        from scripts import main as module

        original_ensure_h5p_runtime_libraries = module.ensure_h5p_runtime_libraries
        original_load_course_preview_state = module.load_course_preview_state
        original_ensure_runtime_question_ready = module.ensure_runtime_question_ready
        try:
            course_dir = self.course_dir
            other_course_dir = self.root / "courses" / "python-2027"
            other_course_dir.mkdir(parents=True)

            first = build_imported_question_from_h5p_package(
                "python-2026",
                MoodleH5PActivity(
                    identifier="eins",
                    title="Eins",
                    course_id=5,
                    activity_id=1,
                    instance_id=1,
                    section_title="Intro",
                    intro="",
                    url="https://example.invalid/mod/h5pactivity/view.php?id=1",
                ),
                {"title": "Eins", "mainLibrary": "H5P.QuestionSet"},
                {"questions": []},
            )
            second = build_imported_question_from_h5p_package(
                "python-2026",
                MoodleH5PActivity(
                    identifier="zwei",
                    title="Zwei",
                    course_id=5,
                    activity_id=2,
                    instance_id=2,
                    section_title="Intro",
                    intro="",
                    url="https://example.invalid/mod/h5pactivity/view.php?id=2",
                ),
                {"title": "Zwei", "mainLibrary": "H5P.QuestionSet"},
                {"questions": []},
            )
            other = build_imported_question_from_h5p_package(
                "python-2027",
                MoodleH5PActivity(
                    identifier="drei",
                    title="Drei",
                    course_id=6,
                    activity_id=3,
                    instance_id=3,
                    section_title="Intro",
                    intro="",
                    url="https://example.invalid/mod/h5pactivity/view.php?id=3",
                ),
                {"title": "Drei", "mainLibrary": "H5P.QuestionSet"},
                {"questions": []},
            )

            assert first is not None
            assert second is not None
            assert other is not None

            library_calls: list[str] = []
            prepared_ids: list[str] = []

            module.ensure_h5p_runtime_libraries = lambda: library_calls.append("libraries")
            module.load_course_preview_state = (
                lambda current_course_dir: ([first, second], "") if current_course_dir == course_dir else ([other], "")
            )
            module.ensure_runtime_question_ready = lambda question: prepared_ids.append(question.identifier)

            prepared = prepare_preview_runtime(course_dir)
        finally:
            module.ensure_h5p_runtime_libraries = original_ensure_h5p_runtime_libraries
            module.load_course_preview_state = original_load_course_preview_state
            module.ensure_runtime_question_ready = original_ensure_runtime_question_ready

        self.assertEqual(library_calls, ["libraries"])
        self.assertEqual(prepared_ids, ["eins", "zwei"])
        self.assertEqual([question.identifier for question in prepared], ["eins", "zwei"])

    def test_build_editable_h5p_payload_omits_python_question_defaults(self) -> None:
        question = build_imported_question_from_h5p_package(
            "python-2026",
            MoodleH5PActivity(
                identifier="einfuehrung-farben",
                title="Einführung: Farben",
                course_id=5,
                activity_id=145,
                instance_id=105,
                section_title="Farben",
                intro="",
                url="https://example.invalid/mod/h5pactivity/view.php?id=145",
            ),
            {"title": "Einführung: Farben", "mainLibrary": "H5P.PythonQuestion"},
            {
                "contentType": "text_only",
                "pythonRunner": "skulpt",
                "advancedOptions": {"showConsole": True},
                "contents": [
                    {"type": "text", "text": "Schau dir den folgenden Code an."},
                    {"type": "code", "code": "import turtle\nturtle.forward(10)\n", "options": {"showEditor": True}},
                ],
                "editorSettings": {"instructions": "Schau dir den folgenden Code an.", "options": {"allowAddingFiles": False}},
                "gradingSettings": {"gradingMethod": "please_choose"},
            },
        )

        assert question is not None
        from scripts import main as module
        payload = module.component_syncer().build_editable_h5p_payload(question)

        self.assertEqual(
            payload,
            {
                "contentType": "text_only",
                "contents": [
                    {"text": "Schau dir den folgenden Code an."},
                    {"type": "code", "code": "import turtle\nturtle.forward(10)\n"},
                ],
            },
        )

    def test_build_editable_h5p_payload_omits_duplicate_ide_instructions(self) -> None:
        question = build_imported_question_from_h5p_package(
            "python-2026",
            MoodleH5PActivity(
                identifier="test-timestamps",
                title="Python Question",
                course_id=5,
                activity_id=170,
                instance_id=130,
                section_title="While",
                intro="",
                url="https://example.invalid/mod/h5pactivity/view.php?id=170",
            ),
            {"title": "Python Question", "mainLibrary": "H5P.PythonQuestion"},
            {
                "contentType": "ide_only",
                "pythonRunner": "skulpt",
                "editorSettings": {
                    "instructions": "Lese zwei timestamps ein und berechne die Anzahl an Sekunden zwischen zwei Timestamps\n",
                    "startingCode": "\n",
                },
                "gradingSettings": {
                    "gradingMethod": "ioTestCases",
                    "testCases": [{"inputs": [{"input": "1"}], "outputs": [{"output": "1"}]}],
                },
            },
        )

        assert question is not None
        self.assertEqual(question.instructions, "Lese zwei timestamps ein und berechne die Anzahl an Sekunden zwischen zwei Timestamps")
        from scripts import main as module
        self.assertNotIn("instructions", json.dumps(module.component_syncer().build_editable_h5p_payload(question), ensure_ascii=False))

    def test_non_python_imported_payload_uses_source_package_as_baseline(self) -> None:
        question = build_imported_question_from_h5p_package(
            "python-2026",
            MoodleH5PActivity(
                identifier="quiz-division",
                title="Quiz Division",
                course_id=5,
                activity_id=165,
                instance_id=120,
                section_title="Quiz",
                intro="",
                url="https://example.invalid/mod/h5pactivity/view.php?id=165",
            ),
            {"title": "Quiz Division", "mainLibrary": "H5P.QuestionSet", "language": "de"},
            {"questions": [{"params": {"question": "Alt?"}}]},
        )

        assert question is not None
        question.course_dir = self.course_dir
        source_archive = self.course_dir / "quiz-division.h5p"
        source_archive.write_bytes(
            self._build_h5p_archive_bytes(
                {"title": "Quiz Division", "mainLibrary": "H5P.QuestionSet", "language": "de"},
                {"questions": [{"params": {"question": "Alt?"}}]},
            )
        )
        question.source_package_path = write_source_package_sidecar(question, source_archive)

        from scripts import main as module
        self.assertEqual(module.component_syncer().build_editable_h5p_payload(question), {})

        mdx = "# Python Kurs\n\n" + "\n".join(render_imported_question_mdx(question)) + "\n"
        self.assertIn('identifier="quiz-division"', mdx)
        self.assertNotIn("h5p={{", mdx)

        (self.course_dir / "index.mdx").write_text(mdx, encoding="utf-8")
        _, questions, _ = parse_course(self.course_dir)

        self.assertEqual(len(questions), 1)
        parsed = questions[0]
        self.assertEqual(parsed.h5p_metadata, question.h5p_metadata)
        self.assertEqual(parsed.h5p_content, question.h5p_content)

    def test_sync_course_preserves_imported_h5p_assets_in_sidecar_and_output(self) -> None:
        question = build_imported_question_from_h5p_package(
            "python-2026",
            MoodleH5PActivity(
                identifier="test-zahlen-addieren",
                title="Test: Zahlen addieren",
                course_id=5,
                activity_id=1,
                instance_id=1,
                section_title="Variablen",
                intro="",
                url="https://example.invalid/mod/h5pactivity/view.php?id=1",
            ),
            {"title": "Test: Zahlen addieren", "mainLibrary": "H5P.PythonQuestion"},
            {
                "contentType": "ide_only",
                "pythonRunner": "skulpt",
                "editorSettings": {
                    "instructionsImage": {
                        "path": "images/instructions.png",
                        "mime": "image/png",
                    },
                    "startingCode": "print(12)\n",
                },
                "gradingSettings": {
                    "gradingMethod": "ioTestCases",
                    "testCases": [{"outputs": [{"output": "12"}]}],
                },
            },
        )

        assert question is not None
        question.course_dir = self.course_dir
        source_archive = self.course_dir / "test-zahlen-addieren.h5p"
        source_archive.write_bytes(
            self._build_h5p_archive_bytes(
                {"title": "Test: Zahlen addieren", "mainLibrary": "H5P.PythonQuestion"},
                question.h5p_content or {},
                extra_files={"content/images/instructions.png": b"png-bytes"},
            )
        )
        question.source_package_path = write_source_package_sidecar(question, source_archive)

        mdx = "# Python Kurs\n\n" + "\n".join(render_imported_question_mdx(question)) + "\n"
        (self.course_dir / "index.mdx").write_text(mdx, encoding="utf-8")

        sync_course(self.course_dir)

        self.assertTrue((self.course_dir / "h5p" / "test-zahlen-addieren" / "images" / "instructions.png").exists())
        self.assertTrue((self.course_dir / "h5p" / "test-zahlen-addieren" / "content.mdx").exists())
        self.assertTrue((self.course_dir / "h5p" / "test-zahlen-addieren" / "settings.yml").exists())
        self.assertFalse((self.course_dir / "h5p" / "test-zahlen-addieren" / "content.json").exists())
        with ZipFile(self.course_dir / "build" / "h5p" / "test-zahlen-addieren.h5p") as archive:
            self.assertIn("content/images/instructions.png", archive.namelist())

    def test_render_imported_question_mdx_uses_readable_strings(self) -> None:
        question = build_imported_question_from_h5p_package(
            "python-2026",
            MoodleH5PActivity(
                identifier="variablen-das-gehirn-des-computers",
                title="Python Question",
                course_id=5,
                activity_id=1,
                instance_id=1,
                section_title="Variablen",
                intro="",
                url="https://example.invalid/mod/h5pactivity/view.php?id=1",
            ),
            {"title": "Python Question", "mainLibrary": "H5P.PythonQuestion"},
            {
                "contentType": "text_only",
                "pythonRunner": "skulpt",
                "contents": [
                    {"type": "text", "text": "Variablen sind das **&quot;Gehirn des Computers&quot;**.\n\n"},
                    {"type": "code", "code": "print(&quot;Hallo&quot;)\n"},
                ],
                "editorSettings": {"instructions": "Variablen sind das **&quot;Gehirn des Computers&quot;**.\n\n", "options": {}},
                "gradingSettings": {},
            },
        )

        assert question is not None
        mdx = "\n".join(render_imported_question_mdx(question))

        self.assertIn('instructions={`Variablen sind das **"Gehirn des Computers"**.', mdx)
        self.assertIn('"text": `', mdx)
        self.assertIn('Variablen sind das **"Gehirn des Computers"**.', mdx)
        self.assertIn('"code": `', mdx)
        self.assertIn('print("Hallo")', mdx)
        self.assertNotIn("&quot;", mdx)

    def test_parse_tag_attributes_supports_template_literals(self) -> None:
        from scripts import main as module

        attrs = module.mdx_course_parser().parse_tag_attributes(
            ' instructions={`Zeile 1\nZeile 2 mit "Zitat"`} h5p={{"contents": [{"text": `A "B"\n\nC`} ]}} '
        )

        self.assertEqual(attrs["instructions"], 'Zeile 1\nZeile 2 mit "Zitat"')
        assert isinstance(attrs["h5p"], dict)
        self.assertEqual(attrs["h5p"]["contents"][0]["text"], 'A "B"\n\nC')

    def test_write_h5p_package_patches_original_imported_package(self) -> None:
        from scripts import main as module

        original_download_file = module.download_file
        original_courses_dir = module.COURSES_DIR
        try:
            module.COURSES_DIR = self.root / "courses"

            downloaded: list[tuple[str, Path]] = []

            def fake_download(url: str, destination: Path) -> None:
                downloaded.append((url, destination))
                with ZipFile(destination, "w") as archive:
                    archive.writestr("h5p.json", json.dumps({"title": "Original", "mainLibrary": "H5P.QuestionSet"}))
                    archive.writestr("content/content.json", json.dumps({"questions": [{"params": {"question": "Alt?"}}]}))
                    archive.writestr("content/extra.txt", "behalten")

            module.download_file = fake_download
            question = build_imported_question_from_h5p_package(
                "python-2026",
                MoodleH5PActivity(
                    identifier="quiz-division",
                    title="Quiz Division",
                    course_id=5,
                    activity_id=165,
                    instance_id=120,
                    section_title="Quiz",
                    intro="",
                    url="https://example.invalid/mod/h5pactivity/view.php?id=165",
                    package_url="https://example.invalid/pluginfile.php/165/mod_h5pactivity/package/0/quiz-division.h5p",
                ),
                {"title": "Quiz Division", "mainLibrary": "H5P.QuestionSet"},
                {"questions": [{"params": {"question": "Original aus MDX"}}]},
            )

            assert question is not None
            assert question.h5p_content is not None
            question.h5p_content["questions"][0]["params"]["question"] = "Neu?"
            package_path = write_h5p_package(question)
        finally:
            module.download_file = original_download_file
            module.COURSES_DIR = original_courses_dir

        self.assertEqual(len(downloaded), 1)
        self.assertEqual(
            downloaded[0][0],
            "https://example.invalid/pluginfile.php/165/mod_h5pactivity/package/0/quiz-division.h5p",
        )
        self.assertEqual(package_path, self.course_dir / "build" / "h5p" / "quiz-division.h5p")
        self.assertTrue(package_path.exists())
        with ZipFile(package_path) as archive:
            self.assertEqual(json.loads(archive.read("h5p.json").decode("utf-8"))["title"], "Quiz Division")
            self.assertEqual(
                json.loads(archive.read("content/content.json").decode("utf-8"))["questions"][0]["params"]["question"],
                "Neu?",
            )
            self.assertEqual(archive.read("content/extra.txt").decode("utf-8"), "behalten")

    def test_write_h5p_package_patches_original_python_question_package(self) -> None:
        from scripts import main as module

        original_download_file = module.download_file
        original_courses_dir = module.COURSES_DIR
        try:
            module.COURSES_DIR = self.root / "courses"

            downloaded: list[tuple[str, Path]] = []

            def fake_download(url: str, destination: Path) -> None:
                downloaded.append((url, destination))
                with ZipFile(destination, "w") as archive:
                    archive.writestr(
                        "h5p.json",
                        json.dumps({"title": "Original", "mainLibrary": "H5P.PythonQuestion"}),
                    )
                    archive.writestr(
                        "content/content.json",
                        json.dumps({"contentType": "text_only", "pythonRunner": "pyodide", "contents": [], "editorSettings": {}, "gradingSettings": {}}),
                    )
                    archive.writestr("content/extra.txt", "behalten")

            module.download_file = fake_download
            question = build_imported_question_from_h5p_package(
                "python-2026",
                MoodleH5PActivity(
                    identifier="einfuehrung-farben",
                    title="Einführung: Farben",
                    course_id=5,
                    activity_id=145,
                    instance_id=105,
                    section_title="Farben",
                    intro="",
                    url="https://example.invalid/mod/h5pactivity/view.php?id=145",
                    package_url="https://example.invalid/pluginfile.php/168/mod_h5pactivity/package/0/einfuhrung-farben-891.h5p",
                ),
                {"title": "Einführung: Farben", "mainLibrary": "H5P.PythonQuestion"},
                {
                    "contentType": "text_only",
                    "pythonRunner": "skulpt",
                    "contents": [{"type": "text", "text": "A"}],
                    "editorSettings": {"instructions": "A", "options": {}},
                    "gradingSettings": {},
                },
            )

            assert question is not None
            question.title = "Geänderte Farben"
            package_path = write_h5p_package(question)
        finally:
            module.download_file = original_download_file
            module.COURSES_DIR = original_courses_dir

        self.assertEqual(len(downloaded), 1)
        self.assertEqual(
            downloaded[0][0],
            "https://example.invalid/pluginfile.php/168/mod_h5pactivity/package/0/einfuhrung-farben-891.h5p",
        )
        self.assertEqual(package_path, self.course_dir / "build" / "h5p" / "einfuehrung-farben.h5p")
        self.assertTrue(package_path.exists())
        with ZipFile(package_path) as archive:
            self.assertEqual(json.loads(archive.read("h5p.json").decode("utf-8"))["title"], "Geänderte Farben")
            self.assertEqual(json.loads(archive.read("content/content.json").decode("utf-8"))["pythonRunner"], "skulpt")
            self.assertEqual(archive.read("content/extra.txt").decode("utf-8"), "behalten")

    def test_write_h5p_package_recovers_missing_python_question_preloaded_dependencies(self) -> None:
        from scripts import main as module

        original_download_file = module.download_file
        original_courses_dir = module.COURSES_DIR
        original_runtime_libraries_dir = module.H5P_RUNTIME_LIBRARIES_DIR
        original_ensure_runtime = module.ensure_h5p_runtime_libraries
        try:
            (self.course_dir / "h5p" / "12eck.h5p").write_text("legacy-package", encoding="utf-8")
            (self.course_dir / "h5p" / ".12eck.build-hash").write_text("legacy-hash", encoding="utf-8")
            (self.course_dir / "h5p" / "orphaned-old-package.h5p").write_text("legacy-package", encoding="utf-8")
            (self.course_dir / "h5p" / ".orphaned-old-package.build-hash").write_text("legacy-hash", encoding="utf-8")
            module.COURSES_DIR = self.root / "courses"
            module.H5P_RUNTIME_LIBRARIES_DIR = self.root / ".h5p-runtime" / "libraries"
            module.ensure_h5p_runtime_libraries = lambda: None

            self._create_fake_library(module.H5P_RUNTIME_LIBRARIES_DIR, "H5P.Question", 1, 5, [])
            self._create_fake_library(module.H5P_RUNTIME_LIBRARIES_DIR, "H5P.LibCodeTools", 6, 73, [])
            self._create_fake_library(
                module.H5P_RUNTIME_LIBRARIES_DIR,
                "H5P.CodeQuestion",
                6,
                73,
                [("H5P.Question", 1, 5), ("H5P.LibCodeTools", 6, 73)],
            )
            self._create_fake_library(
                module.H5P_RUNTIME_LIBRARIES_DIR,
                "H5P.PythonQuestion",
                6,
                73,
                [("H5P.CodeQuestion", 6, 73), ("H5P.LibCodeTools", 6, 73)],
            )

            def fake_download(url: str, destination: Path) -> None:
                with ZipFile(destination, "w") as archive:
                    archive.writestr(
                        "h5p.json",
                        json.dumps({"title": "Original", "mainLibrary": "H5P.PythonQuestion"}),
                    )
                    archive.writestr(
                        "content/content.json",
                        json.dumps(
                            {
                                "contentType": "text_only",
                                "pythonRunner": "skulpt",
                                "contents": [{"type": "text", "text": "A"}],
                                "editorSettings": {"instructions": "A", "options": {}},
                                "gradingSettings": {},
                            }
                        ),
                    )

            module.download_file = fake_download
            question = build_imported_question_from_h5p_package(
                "python-2026",
                MoodleH5PActivity(
                    identifier="einfuehrung-farben",
                    title="Einführung: Farben",
                    course_id=5,
                    activity_id=145,
                    instance_id=105,
                    section_title="Farben",
                    intro="",
                    url="https://example.invalid/mod/h5pactivity/view.php?id=145",
                    package_url="https://example.invalid/pluginfile.php/168/mod_h5pactivity/package/0/einfuhrung-farben-891.h5p",
                ),
                {
                    "title": "Einführung: Farben",
                    "mainLibrary": "H5P.PythonQuestion",
                },
                {
                    "contentType": "text_only",
                    "pythonRunner": "skulpt",
                    "contents": [{"type": "text", "text": "A"}],
                    "editorSettings": {"instructions": "A", "options": {}},
                    "gradingSettings": {},
                },
            )

            assert question is not None
            package_path = write_h5p_package(question)
        finally:
            module.download_file = original_download_file
            module.COURSES_DIR = original_courses_dir
            module.H5P_RUNTIME_LIBRARIES_DIR = original_runtime_libraries_dir
            module.ensure_h5p_runtime_libraries = original_ensure_runtime

        with ZipFile(package_path) as archive:
            metadata = json.loads(archive.read("h5p.json").decode("utf-8"))

        self.assertEqual(metadata["mainLibrary"], "H5P.PythonQuestion")
        self.assertIn("preloadedDependencies", metadata)
        self.assertIsInstance(metadata["preloadedDependencies"], list)
        self.assertGreater(len(metadata["preloadedDependencies"]), 0)

    def test_extract_h5p_package_from_course_backup_recovers_hidden_activity(self) -> None:
        backup_path = self.root / "course.mbz"
        destination = self.root / "timestamps2.h5p"
        package_bytes = self._build_h5p_archive_bytes(
            {"title": "Bonus: Timestamps II", "mainLibrary": "H5P.PythonQuestion"},
            {"contentType": "ide_only", "pythonRunner": "skulpt", "contents": []},
        )
        content_hash = sha1(package_bytes).hexdigest()
        file_member = f"files/{content_hash[:2]}/{content_hash[2:4]}/{content_hash}"
        with tarfile.open(backup_path, "w:gz") as archive:
            self._add_tar_text(
                archive,
                "moodle_backup.xml",
                """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<moodle_backup>
  <information>
    <contents>
      <activities>
        <activity>
          <moduleid>110020</moduleid>
          <modulename>h5pactivity</modulename>
          <title>Bonus: Timestamps II</title>
          <directory>activities/h5pactivity_110020</directory>
        </activity>
      </activities>
    </contents>
  </information>
</moodle_backup>
""",
            )
            self._add_tar_text(
                archive,
                "activities/h5pactivity_110020/inforef.xml",
                """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<inforef><fileref><file><id>4448317</id></file></fileref></inforef>
""",
            )
            self._add_tar_text(
                archive,
                "files.xml",
                f"""<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<files>
  <file id=\"4448317\">
    <contenthash>{content_hash}</contenthash>
    <contextid>197210</contextid>
    <component>mod_h5pactivity</component>
    <filearea>package</filearea>
    <itemid>0</itemid>
    <filepath>/</filepath>
    <filename>timestamps2.h5p</filename>
  </file>
</files>
""",
            )
            self._add_tar_bytes(archive, file_member, package_bytes)

        activity = MoodleH5PActivity(
            identifier="bonus-timestamps-ii",
            title="Bonus: Timestamps II",
            course_id=5,
            activity_id=171,
            instance_id=131,
            section_title="While",
            intro="",
            url="https://example.invalid/mod/h5pactivity/view.php?id=171",
        )

        from scripts import main as module

        extracted = module.moodle_backup_extractor().extract_h5p_package_from_backup_activity(
            backup_path,
            "activities/h5pactivity_110020",
            destination,
        )

        self.assertTrue(extracted)
        with ZipFile(destination) as archive:
            self.assertEqual(json.loads(archive.read("h5p.json").decode("utf-8"))["title"], "Bonus: Timestamps II")

    def test_download_activity_question_falls_back_to_course_backup(self) -> None:
        backup_path = self.root / "course.mbz"
        package_bytes = self._build_h5p_archive_bytes(
            {"title": "Bonus: Timestamps II", "mainLibrary": "H5P.PythonQuestion"},
            {
                "contentType": "ide_only",
                "pythonRunner": "skulpt",
                "editorSettings": {"instructions": "Aus Backup", "options": {}},
                "gradingSettings": {},
            },
        )
        content_hash = sha1(package_bytes).hexdigest()
        with tarfile.open(backup_path, "w:gz") as archive:
            self._add_tar_text(
                archive,
                "moodle_backup.xml",
                """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<moodle_backup><information><contents><activities><activity><moduleid>110020</moduleid><modulename>h5pactivity</modulename><title>Bonus: Timestamps II</title><directory>activities/h5pactivity_110020</directory></activity></activities></contents></information></moodle_backup>
""",
            )
            self._add_tar_text(
                archive,
                "activities/h5pactivity_110020/inforef.xml",
                """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<inforef><fileref><file><id>4448317</id></file></fileref></inforef>
""",
            )
            self._add_tar_text(
                archive,
                "files.xml",
                f"""<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<files><file id=\"4448317\"><contenthash>{content_hash}</contenthash><component>mod_h5pactivity</component><filearea>package</filearea><filename>timestamps2.h5p</filename></file></files>
""",
            )
            self._add_tar_bytes(archive, f"files/{content_hash[:2]}/{content_hash[2:4]}/{content_hash}", package_bytes)

        from scripts import main as module

        original_fetch_text = module.fetch_text
        original_download_file = module.download_file
        original_courses_dir = module.COURSES_DIR
        try:
            module.COURSES_DIR = self.root / "courses"
            def fake_fetch_text(url: str) -> str:
                if "view.php?id=171" in url:
                    return "<html><body>Kein iframe</body></html>"
                if "course/view.php?id=5" in url:
                    return '<a href="https://example.invalid/backup/course.mbz">Backup</a>'
                raise AssertionError(url)

            def fake_download_file(url: str, destination: Path) -> None:
                if url == "https://example.invalid/backup/course.mbz":
                    destination.write_bytes(backup_path.read_bytes())
                    return
                raise AssertionError(url)

            module.fetch_text = fake_fetch_text
            module.download_file = fake_download_file
            backup_extractor = module.moodle_backup_extractor()
            client = MoodleApiClient(
                "https://example.invalid",
                "token",
                make_stable_identifier=module.make_stable_identifier,
                strip_html=module.strip_html,
                fetch_text=module.fetch_text,
                extract_h5p_package_url_from_activity_html=lambda page_html: module.extract_h5p_package_url_from_activity_html(
                    page_html,
                    base_url="https://example.invalid",
                ),
                download_file=module.download_file,
                extract_h5p_package_from_course_backup=backup_extractor.extract_h5p_package_from_course_backup,
                build_imported_question_from_h5p_package=module.build_imported_question_from_h5p_package,
                write_source_package_sidecar=module.write_source_package_sidecar,
            )
            question = client.download_activity_question(
                "python-2026",
                MoodleH5PActivity(
                    identifier="bonus-timestamps-ii",
                    title="Bonus: Timestamps II",
                    course_id=5,
                    activity_id=171,
                    instance_id=131,
                    section_title="While",
                    intro="",
                    url="https://example.invalid/mod/h5pactivity/view.php?id=171",
                ),
            )
        finally:
            module.fetch_text = original_fetch_text
            module.download_file = original_download_file
            module.COURSES_DIR = original_courses_dir

        assert question is not None
        self.assertEqual(question.title, "Bonus: Timestamps II")
        self.assertEqual(question.instructions, "Aus Backup")
        self.assertEqual(question.source_package_path, "h5p/bonus-timestamps-ii")
        self.assertTrue((self.root / "courses" / "python-2026" / question.source_package_path).exists())

    def test_load_course_preview_state_uses_cache_until_mdx_changes(self) -> None:
        from scripts import main as module

        original_courses_dir = module.COURSES_DIR
        original_preview_cache = dict(module.PREVIEW_CACHE)
        try:
            module.COURSES_DIR = self.root / "courses"
            module.PREVIEW_CACHE.clear()

            questions_one, html_one = load_course_preview_state(self.course_dir)
            questions_two, html_two = load_course_preview_state(self.course_dir)

            self.assertIs(questions_one, questions_two)
            self.assertEqual(html_one, html_two)

            updated = (self.course_dir / "index.mdx").read_text(encoding="utf-8") + "\n\n## Neu\n"
            (self.course_dir / "index.mdx").write_text(updated, encoding="utf-8")

            questions_three, html_three = load_course_preview_state(self.course_dir)
        finally:
            module.COURSES_DIR = original_courses_dir
            module.PREVIEW_CACHE.clear()
            module.PREVIEW_CACHE.update(original_preview_cache)

        self.assertIsNot(questions_one, questions_three)
        self.assertIn("Neu", html_three)

    def test_sync_metadata_roundtrip(self) -> None:
        metadata = SyncMetadata(
            course_slug="python-2026",
            remote_course_id=5,
            moodle_base_url="https://example.invalid",
            entries={
                "12eck": SyncMetadataEntry(
                    identifier="12eck",
                    remote_activity_id=134,
                    remote_instance_id=77,
                    remote_title="Zwölfeck zeichnen",
                    remote_url="https://example.invalid/mod/h5pactivity/view.php?id=134",
                    remote_visible=True,
                    local_hash="abc",
                )
            },
        )

        save_sync_metadata(self.course_dir, metadata)
        reloaded = load_sync_metadata(self.course_dir)

        self.assertIsNotNone(reloaded)
        assert reloaded is not None
        self.assertEqual(reloaded.remote_course_id, 5)
        self.assertEqual(reloaded.entries["12eck"].remote_activity_id, 134)
        self.assertEqual(reloaded.entries["12eck"].local_hash, "abc")

    def test_load_dotenv_file_sets_missing_values_only(self) -> None:
        from scripts import main as module

        dotenv_path = self.root / ".env"
        dotenv_path.write_text(
            'MOODLE_BASE_URL="https://example.invalid"\nMOODLE_TOKEN=test-token\n',
            encoding="utf-8",
        )

        original_base = os.environ.pop("MOODLE_BASE_URL", None)
        original_token = os.environ.pop("MOODLE_TOKEN", None)
        try:
            module.moodle_client_resolver().load_dotenv_file(dotenv_path)
            self.assertEqual(os.environ["MOODLE_BASE_URL"], "https://example.invalid")
            self.assertEqual(os.environ["MOODLE_TOKEN"], "test-token")

            os.environ["MOODLE_TOKEN"] = "override-token"
            module.moodle_client_resolver().load_dotenv_file(dotenv_path)
            self.assertEqual(os.environ["MOODLE_TOKEN"], "override-token")
        finally:
            if original_base is None:
                os.environ.pop("MOODLE_BASE_URL", None)
            else:
                os.environ["MOODLE_BASE_URL"] = original_base
            if original_token is None:
                os.environ.pop("MOODLE_TOKEN", None)
            else:
                os.environ["MOODLE_TOKEN"] = original_token

    def test_resolve_moodle_client_reads_from_dotenv(self) -> None:
        from scripts import main as module

        dotenv_path = self.root / ".env"
        dotenv_path.write_text(
            "MOODLE_BASE_URL=https://example.invalid\nMOODLE_TOKEN=token-from-env\n",
            encoding="utf-8",
        )

        original_dotenv_file = module.DOTENV_FILE
        original_base = os.environ.pop("MOODLE_BASE_URL", None)
        original_token = os.environ.pop("MOODLE_TOKEN", None)
        try:
            module.DOTENV_FILE = dotenv_path
            client = resolve_moodle_client()
            self.assertEqual(client.base_url, "https://example.invalid")
            self.assertEqual(client.token, "token-from-env")
        finally:
            module.DOTENV_FILE = original_dotenv_file
            if original_base is None:
                os.environ.pop("MOODLE_BASE_URL", None)
            else:
                os.environ["MOODLE_BASE_URL"] = original_base
            if original_token is None:
                os.environ.pop("MOODLE_TOKEN", None)
            else:
                os.environ["MOODLE_TOKEN"] = original_token

    def test_upload_moodle_chapter_prefers_course_specific_dotenv_values(self) -> None:
        from scripts import main as module

        dotenv_path = self.root / ".env"
        dotenv_path.write_text(
            "\n".join(
                [
                    "MOODLE_COURSE_URL=https://global.example/course/view.php?id=1",
                    "MOODLE_USERNAME=global-user",
                    "MOODLE_PASSWORD=global-pass",
                    "MOODLE_PYTHON_2026_COURSE_URL=https://course.example/course/view.php?id=5",
                    "MOODLE_PYTHON_2026_USERNAME=course-user",
                    "MOODLE_PYTHON_2026_PASSWORD=course-pass",
                ]
            ),
            encoding="utf-8",
        )

        captured: dict[str, object] = {}

        class FakeUploader:
            def __init__(self, **kwargs: object) -> None:
                captured.update(kwargs)

            def upload_packages(self, packages: object) -> list[object]:
                captured["packages"] = packages
                return []

        original_dotenv_file = module.DOTENV_FILE
        original_sync_course = module.sync_course
        original_collect = module.collect_h5p_upload_packages
        original_uploader = module.MoodlePlaywrightUploader
        original_keys = {
            key: os.environ.pop(key, None)
            for key in [
                "MOODLE_COURSE_URL",
                "MOODLE_USERNAME",
                "MOODLE_PASSWORD",
                "MOODLE_PYTHON_2026_COURSE_URL",
                "MOODLE_PYTHON_2026_USERNAME",
                "MOODLE_PYTHON_2026_PASSWORD",
            ]
        }
        try:
            module.DOTENV_FILE = dotenv_path
            module.sync_course = lambda _course_dir: []
            module.collect_h5p_upload_packages = lambda _course_dir, _chapter: []
            module.MoodlePlaywrightUploader = FakeUploader

            module.upload_moodle_chapter(self.course_dir, "013-texte-strings")

            self.assertEqual(captured["course_url"], "https://course.example/course/view.php?id=5")
            self.assertEqual(captured["username"], "course-user")
            self.assertEqual(captured["password"], "course-pass")
        finally:
            module.DOTENV_FILE = original_dotenv_file
            module.sync_course = original_sync_course
            module.collect_h5p_upload_packages = original_collect
            module.MoodlePlaywrightUploader = original_uploader
            for key, value in original_keys.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_upload_moodle_chapter_prefers_named_target_dotenv_values(self) -> None:
        from scripts import main as module

        dotenv_path = self.root / ".env"
        dotenv_path.write_text(
            "\n".join(
                [
                    "MOODLE_PYTHON_2026_COURSE_URL=https://primary.example/course/view.php?id=5",
                    "MOODLE_PYTHON_2026_USERNAME=primary-user",
                    "MOODLE_PYTHON_2026_PASSWORD=primary-pass",
                    "MOODLE_PYTHON_2026_SECOND_COURSE_URL=https://second.example/course/view.php?id=9",
                    "MOODLE_PYTHON_2026_SECOND_USERNAME=second-user",
                    "MOODLE_PYTHON_2026_SECOND_PASSWORD=second-pass",
                ]
            ),
            encoding="utf-8",
        )

        captured: dict[str, object] = {}

        class FakeUploader:
            def __init__(self, **kwargs: object) -> None:
                captured.update(kwargs)

            def upload_packages(self, packages: object) -> list[object]:
                captured["packages"] = packages
                return []

        original_dotenv_file = module.DOTENV_FILE
        original_sync_course = module.sync_course
        original_collect = module.collect_h5p_upload_packages
        original_uploader = module.MoodlePlaywrightUploader
        original_store = module._store_uploaded_activity_ids
        store_calls: list[object] = []
        original_keys = {
            key: os.environ.pop(key, None)
            for key in [
                "MOODLE_PYTHON_2026_COURSE_URL",
                "MOODLE_PYTHON_2026_USERNAME",
                "MOODLE_PYTHON_2026_PASSWORD",
                "MOODLE_PYTHON_2026_SECOND_COURSE_URL",
                "MOODLE_PYTHON_2026_SECOND_USERNAME",
                "MOODLE_PYTHON_2026_SECOND_PASSWORD",
            ]
        }
        try:
            module.DOTENV_FILE = dotenv_path
            module.sync_course = lambda _course_dir: []
            module.collect_h5p_upload_packages = lambda _course_dir, _chapter: []
            module.MoodlePlaywrightUploader = FakeUploader
            module._store_uploaded_activity_ids = lambda *args: store_calls.append(args)

            module.upload_moodle_chapter(self.course_dir, "013-texte-strings", target="second")

            self.assertEqual(captured["course_url"], "https://second.example/course/view.php?id=9")
            self.assertEqual(captured["username"], "second-user")
            self.assertEqual(captured["password"], "second-pass")
            self.assertEqual(captured["storage_state"], self.course_dir / ".moodle-storage-state-second.json")
            self.assertEqual(store_calls, [])
        finally:
            module.DOTENV_FILE = original_dotenv_file
            module.sync_course = original_sync_course
            module.collect_h5p_upload_packages = original_collect
            module.MoodlePlaywrightUploader = original_uploader
            module._store_uploaded_activity_ids = original_store
            for key, value in original_keys.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_upload_moodle_chapter_reads_credentials_for_sso_target(self) -> None:
        from scripts import main as module

        (self.course_dir / ".moodle-target-2.yml").write_text("auth: sso\n", encoding="utf-8")
        dotenv_path = self.root / ".env"
        dotenv_path.write_text(
            "\n".join(
                [
                    "MOODLE_PYTHON_2026_2_COURSE_URL=https://mo5235.schulportal.hessen.de/course/view.php?id=7845",
                    "MOODLE_PYTHON_2026_2_USERNAME=sso-user",
                    "MOODLE_PYTHON_2026_2_PASSWORD=sso-pass",
                ]
            ),
            encoding="utf-8",
        )

        captured: dict[str, object] = {}

        class FakeUploader:
            def __init__(self, **kwargs: object) -> None:
                captured.update(kwargs)

            def upload_packages(self, packages: object) -> list[object]:
                return []

        original_dotenv_file = module.DOTENV_FILE
        original_sync_course = module.sync_course
        original_collect = module.collect_h5p_upload_packages
        original_uploader = module.MoodlePlaywrightUploader
        original_keys = {
            key: os.environ.pop(key, None)
            for key in [
                "MOODLE_PYTHON_2026_2_COURSE_URL",
                "MOODLE_PYTHON_2026_2_USERNAME",
                "MOODLE_PYTHON_2026_2_PASSWORD",
            ]
        }
        try:
            module.DOTENV_FILE = dotenv_path
            module.sync_course = lambda _course_dir: []
            module.collect_h5p_upload_packages = lambda _course_dir, _chapter: []
            module.MoodlePlaywrightUploader = FakeUploader

            module.upload_moodle_chapter(self.course_dir, "013-texte-strings", target="2")

            self.assertEqual(captured["course_url"], "https://mo5235.schulportal.hessen.de/course/view.php?id=7845")
            self.assertEqual(captured["username"], "sso-user")
            self.assertEqual(captured["password"], "sso-pass")
        finally:
            module.DOTENV_FILE = original_dotenv_file
            module.sync_course = original_sync_course
            module.collect_h5p_upload_packages = original_collect
            module.MoodlePlaywrightUploader = original_uploader
            for key, value in original_keys.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_upload_moodle_chapter_prefers_chapter_heading_for_section_title(self) -> None:
        from scripts import main as module

        (self.course_dir / "chapters").mkdir()
        (self.course_dir / "chapters" / "013-texte-strings.mdx").write_text(
            "## Texte (Strings)\n\n<PythonQuestion identifier=\"strings-grundlagen\" />\n",
            encoding="utf-8",
        )
        (self.course_dir / "index.mdx").write_text(
            '# Python Kurs\n\n<Chapter src="./chapters/013-texte-strings.mdx" title="Schleifen und Texte" />\n',
            encoding="utf-8",
        )

        captured: dict[str, object] = {}

        class FakeUploader:
            def __init__(self, **kwargs: object) -> None:
                captured.update(kwargs)

            def upload_packages(self, packages: object) -> list[object]:
                return []

        original_sync_course = module.sync_course
        original_collect = module.collect_h5p_upload_packages
        original_uploader = module.MoodlePlaywrightUploader
        try:
            module.sync_course = lambda _course_dir: []
            module.collect_h5p_upload_packages = lambda _course_dir, _chapter: []
            module.MoodlePlaywrightUploader = FakeUploader

            module.upload_moodle_chapter(
                self.course_dir,
                "013-texte-strings",
                course_url="https://example.invalid/course/view.php?id=5",
            )

            self.assertEqual(captured["section_title"], "Texte (Strings)")
        finally:
            module.sync_course = original_sync_course
            module.collect_h5p_upload_packages = original_collect
            module.MoodlePlaywrightUploader = original_uploader

    def test_load_existing_h5p_activity_ids_skips_ambiguous_titles(self) -> None:
        from scripts import main as module

        class FakeClient:
            def list_course_h5p_activities(self, course_id: int):
                self.course_id = course_id
                return [
                    type("Activity", (), {"activity_id": 197, "title": "Python Question"})(),
                    type("Activity", (), {"activity_id": 198, "title": "Python Question"})(),
                ]

        original_resolve = module.resolve_moodle_client
        try:
            module.resolve_moodle_client = lambda: FakeClient()
            packages = [
                type("Pkg", (), {"identifier": "a", "title": "Python Question"})(),
                type("Pkg", (), {"identifier": "b", "title": "Python Question"})(),
            ]

            result = module._load_existing_h5p_activity_ids_from_moodle(
                self.course_dir,
                "https://example.invalid/course/view.php?id=5",
                packages,
            )

            self.assertEqual(result, {})
        finally:
            module.resolve_moodle_client = original_resolve

    def test_load_existing_h5p_activity_ids_maps_unique_title(self) -> None:
        from scripts import main as module

        class FakeClient:
            def list_course_h5p_activities(self, course_id: int):
                self.course_id = course_id
                return [
                    type("Activity", (), {"activity_id": 241, "title": "Funktionen definieren"})(),
                ]

        original_resolve = module.resolve_moodle_client
        try:
            module.resolve_moodle_client = lambda: FakeClient()
            packages = [
                type("Pkg", (), {"identifier": "funktionen-definieren", "title": "Funktionen definieren"})(),
            ]

            result = module._load_existing_h5p_activity_ids_from_moodle(
                self.course_dir,
                "https://example.invalid/course/view.php?id=5",
                packages,
            )

            self.assertEqual(result, {"funktionen-definieren": 241})
        finally:
            module.resolve_moodle_client = original_resolve

    def test_build_moodle_ping_report_detects_import_capability(self) -> None:
        class FakeMoodleClient:
            base_url = "https://example.invalid"

            def get_site_info(self) -> dict[str, object]:
                return {
                    "sitename": "OpenCode",
                    "siteurl": "https://example.invalid",
                    "userid": 42,
                    "username": "service-user",
                    "fullname": "Service User",
                    "functions": [
                        {"name": "core_course_get_contents"},
                        {"name": "core_webservice_get_site_info"},
                    ],
                }

        report = build_moodle_ping_report(FakeMoodleClient())

        self.assertEqual(report["baseUrl"], "https://example.invalid")
        self.assertEqual(report["siteName"], "OpenCode")
        self.assertEqual(report["userName"], "service-user")
        self.assertTrue(report["supportsCourseImport"])
        self.assertFalse(report["supportsCoursePush"])
        self.assertIn("core_course_get_contents", report["functions"])
        self.assertTrue(report["pushBlockers"])

    def test_moodle_api_client_push_support_report_marks_missing_write_apis(self) -> None:
        class StubMoodleApiClient(MoodleApiClient):
            def get_available_function_names(self) -> list[str]:
                return ["core_course_get_contents", "core_webservice_get_site_info"]

        client = StubMoodleApiClient(
            "https://example.invalid",
            "token",
            make_stable_identifier=lambda title, used: make_stable_identifier(title, used),
            strip_html=lambda text: text,
            fetch_text=lambda url: "",
            extract_h5p_package_url_from_activity_html=lambda page_html: "",
            download_file=lambda url, destination: None,
            extract_h5p_package_from_course_backup=lambda base, activity, archive_path: False,
            build_imported_question_from_h5p_package=lambda course_slug, activity, metadata, content: None,
            write_source_package_sidecar=lambda question, archive_path: "",
        )

        report = client.build_course_push_support_report()

        self.assertFalse(report["supportsCoursePush"])
        self.assertFalse(report["supportsDraftUpload"])
        self.assertFalse(report["supportsModuleCreation"])
        self.assertTrue(report["blockers"])

    def test_find_library_dir_seeds_runtime_from_local_archive(self) -> None:
        from scripts.classes import H5PLibraryManager

        runtime_dir = self.root / ".h5p-runtime"
        runtime_libraries_dir = runtime_dir / "libraries"
        runtime_downloads_dir = runtime_dir / "downloads"
        shared_libraries_dir = self.root / "libraries"
        shared_libraries_dir.mkdir(parents=True, exist_ok=True)

        archive_path = self.course_dir / "build" / "h5p" / "quiz-division.h5p"
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        archive_path.write_bytes(
            self._build_h5p_archive_bytes(
                {"title": "Quiz", "mainLibrary": "H5P.MultiChoice"},
                {"question": "2+2?"},
                libraries={
                    "H5P.MultiChoice-1.16": {
                        "machineName": "H5P.MultiChoice",
                        "majorVersion": 1,
                        "minorVersion": 16,
                    }
                },
            )
        )

        manager = H5PLibraryManager(
            workspace_lock=threading.RLock(),
            runtime_dir=runtime_dir,
            runtime_content_dir=runtime_dir / "content",
            runtime_libraries_dir=runtime_libraries_dir,
            runtime_downloads_dir=runtime_downloads_dir,
            shared_libraries_dir=shared_libraries_dir,
            courses_dir=self.root / "courses",
            release_repo="repo",
            release_tag="tag",
            asset_prefixes={},
            custom_short_names={},
            ensure_directory=lambda path: path.mkdir(parents=True, exist_ok=True),
            read_json=lambda path: json.loads(path.read_text(encoding="utf-8")),
            read_json_or_default=lambda path, default: default if not path.exists() else json.loads(path.read_text(encoding="utf-8")),
            write_json=lambda path, payload: path.write_text(json.dumps(payload), encoding="utf-8"),
            fetch_json=lambda url: {},
            download_file=lambda url, destination: None,
        )

        library_dir = manager.find_library_dir("H5P.MultiChoice", 1, 16)

        self.assertEqual(library_dir.name, "H5P.MultiChoice-1.16")
        self.assertTrue((runtime_libraries_dir / "H5P.MultiChoice-1.16" / "library.json").exists())

    def test_update_custom_libraries_from_github_downloads_latest_release_to_shared_and_runtime(self) -> None:
        from scripts.classes import H5PLibraryManager

        runtime_dir = self.root / ".h5p-runtime"
        runtime_libraries_dir = runtime_dir / "libraries"
        runtime_downloads_dir = runtime_dir / "downloads"
        shared_libraries_dir = self.root / "libraries"
        (shared_libraries_dir / "H5P.PythonQuestion-6.73").mkdir(parents=True)
        (runtime_libraries_dir / "H5P.PythonQuestion-6.73").mkdir(parents=True)

        archive_payload = self._build_h5p_archive_bytes(
            {"title": "Question", "mainLibrary": "H5P.PythonQuestion"},
            {},
            libraries={
                "H5P.PythonQuestion-6.74": {
                    "machineName": "H5P.PythonQuestion",
                    "majorVersion": 6,
                    "minorVersion": 74,
                    "title": "Python Question",
                }
            },
        )
        downloads = {"https://example.invalid/H5P.PythonQuestion-6.74.h5p": archive_payload}
        fetched_urls: list[str] = []

        manager = H5PLibraryManager(
            workspace_lock=threading.RLock(),
            runtime_dir=runtime_dir,
            runtime_content_dir=runtime_dir / "content",
            runtime_libraries_dir=runtime_libraries_dir,
            runtime_downloads_dir=runtime_downloads_dir,
            shared_libraries_dir=shared_libraries_dir,
            courses_dir=self.root / "courses",
            release_repo="owner/repo",
            release_tag="old-tag",
            asset_prefixes={"H5P.PythonQuestion": "H5P.PythonQuestion-6.73_"},
            custom_short_names={"H5P.PythonQuestion": "h5p-python-question"},
            ensure_directory=lambda path: path.mkdir(parents=True, exist_ok=True),
            read_json=lambda path: json.loads(path.read_text(encoding="utf-8")),
            read_json_or_default=lambda path, default: default if not path.exists() else json.loads(path.read_text(encoding="utf-8")),
            write_json=lambda path, payload: path.write_text(json.dumps(payload), encoding="utf-8"),
            fetch_json=lambda url: fetched_urls.append(url)
            or {
                "tag_name": "v6.74.0",
                "assets": [
                    {
                        "name": "H5P.PythonQuestion-6.74_20260511.h5p",
                        "browser_download_url": "https://example.invalid/H5P.PythonQuestion-6.74.h5p",
                    }
                ],
            },
            download_file=lambda url, destination: destination.write_bytes(downloads[url]),
        )

        updated = manager.update_custom_libraries_from_github()

        self.assertEqual(fetched_urls, ["https://api.github.com/repos/owner/repo/releases/latest"])
        self.assertEqual(updated[0]["asset"], "H5P.PythonQuestion-6.74_20260511.h5p")
        self.assertFalse((shared_libraries_dir / "H5P.PythonQuestion-6.73").exists())
        self.assertFalse((runtime_libraries_dir / "H5P.PythonQuestion-6.73").exists())
        self.assertTrue((shared_libraries_dir / "H5P.PythonQuestion-6.74" / "library.json").exists())
        self.assertTrue((runtime_libraries_dir / "H5P.PythonQuestion-6.74" / "library.json").exists())
        registry = json.loads((runtime_dir / "libraryRegistry.json").read_text(encoding="utf-8"))
        self.assertEqual(registry["H5P.PythonQuestion"]["shortName"], "h5p-python-question")

    def test_moodle_api_client_list_course_h5p_activities_filters_and_maps_fields(self) -> None:
        class StubMoodleApiClient(MoodleApiClient):
            def call(self, function_name: str, **params: object) -> object:
                self.last_call = (function_name, params)
                return [
                    {
                        "name": "Kapitel 1",
                        "modules": [
                            {
                                "modname": "h5pactivity",
                                "id": 10,
                                "instance": 20,
                                "name": "Einfuehrung Farben",
                                "description": "<p>Intro</p>",
                                "url": "https://example.invalid/mod/h5pactivity/view.php?id=10",
                                "visible": 1,
                            },
                            {
                                "modname": "assign",
                                "id": 11,
                                "name": "Abgabe",
                            },
                        ],
                    }
                ]

        client = StubMoodleApiClient(
            "https://example.invalid",
            "token",
            make_stable_identifier=lambda title, used: make_stable_identifier(title, used),
            strip_html=lambda text: "Intro" if "Intro" in text else text,
            fetch_text=lambda url: "",
            extract_h5p_package_url_from_activity_html=lambda page_html: "",
            download_file=lambda url, destination: None,
            extract_h5p_package_from_course_backup=lambda base, activity, archive_path: False,
            build_imported_question_from_h5p_package=lambda course_slug, activity, metadata, content: None,
            write_source_package_sidecar=lambda question, archive_path: "",
        )

        activities = client.list_course_h5p_activities(5)

        self.assertEqual(client.last_call[0], "core_course_get_contents")
        self.assertEqual(client.last_call[1]["courseid"], 5)
        self.assertEqual(len(activities), 1)
        self.assertEqual(activities[0].identifier, "einfuehrung-farben")
        self.assertEqual(activities[0].activity_id, 10)
        self.assertEqual(activities[0].instance_id, 20)
        self.assertEqual(activities[0].intro, "Intro")

    def test_moodle_api_client_places_subsection_activities_at_parent_module_position(self) -> None:
        class StubMoodleApiClient(MoodleApiClient):
            def call(self, function_name: str, **params: object) -> object:
                return [
                    {
                        "name": "Grundlagen",
                        "section": 1,
                        "modules": [
                            {"modname": "h5pactivity", "id": 10, "instance": 20, "name": "Vorher"},
                            {"modname": "subsection", "id": 11, "instance": 30, "name": "Erste Aufgaben"},
                            {"modname": "h5pactivity", "id": 12, "instance": 22, "name": "Nachher"},
                        ],
                    },
                    {
                        "name": "Erste Aufgaben",
                        "section": 8,
                        "component": "mod_subsection",
                        "itemid": 30,
                        "modules": [
                            {"modname": "h5pactivity", "id": 13, "instance": 23, "name": "Aufgabe 1"},
                            {"modname": "h5pactivity", "id": 14, "instance": 24, "name": "Aufgabe 2"},
                        ],
                    },
                ]

        client = StubMoodleApiClient(
            "https://example.invalid",
            "token",
            make_stable_identifier=lambda title, used: make_stable_identifier(title, used),
            strip_html=lambda text: text,
            fetch_text=lambda url: "",
            extract_h5p_package_url_from_activity_html=lambda page_html: "",
            download_file=lambda url, destination: None,
            extract_h5p_package_from_course_backup=lambda base, activity, archive_path: False,
            build_imported_question_from_h5p_package=lambda course_slug, activity, metadata, content: None,
            write_source_package_sidecar=lambda question, archive_path: "",
        )

        activities = client.list_course_h5p_activities(5)

        self.assertEqual([activity.title for activity in activities], ["Vorher", "Aufgabe 1", "Aufgabe 2", "Nachher"])
        self.assertEqual(activities[1].section_title, "Grundlagen")
        self.assertEqual(activities[1].subsection_title, "Erste Aufgaben")
        self.assertEqual(activities[1].module_index, 1)
        self.assertEqual(activities[1].submodule_index, 0)

    def test_moodle_api_client_get_site_info_rejects_non_object_payload(self) -> None:
        class StubMoodleApiClient(MoodleApiClient):
            def call(self, function_name: str, **params: object) -> object:
                return ["not-a-dict"]

        client = StubMoodleApiClient(
            "https://example.invalid",
            "token",
            make_stable_identifier=lambda title, used: make_stable_identifier(title, used),
            strip_html=lambda text: text,
            fetch_text=lambda url: "",
            extract_h5p_package_url_from_activity_html=lambda page_html: "",
            download_file=lambda url, destination: None,
            extract_h5p_package_from_course_backup=lambda base, activity, archive_path: False,
            build_imported_question_from_h5p_package=lambda course_slug, activity, metadata, content: None,
            write_source_package_sidecar=lambda question, archive_path: "",
        )

        with self.assertRaises(RuntimeError):
            client.get_site_info()

    def test_import_moodle_course_creates_local_scaffold_and_metadata(self) -> None:
        class FakeMoodleClient:
            base_url = "https://example.invalid"

            def list_course_h5p_activities(self, course_id: int):
                self.last_course_id = course_id
                return [
                    type(
                        "RemoteActivity",
                        (),
                        {
                            "identifier": "test-quadrat",
                            "title": "Test Quadrat",
                            "activity_id": 134,
                            "instance_id": 55,
                            "section_title": "Grundlagen",
                            "intro": "Beschreibe ein Quadrat.",
                            "url": "https://example.invalid/mod/h5pactivity/view.php?id=134",
                            "visible": True,
                        },
                    )(),
                    type(
                        "RemoteActivity",
                        (),
                        {
                            "identifier": "test-dreieck",
                            "title": "Test Dreieck",
                            "activity_id": 135,
                            "instance_id": 56,
                            "section_title": "Grundlagen",
                            "intro": "Beschreibe ein Dreieck.",
                            "url": "https://example.invalid/mod/h5pactivity/view.php?id=135",
                            "visible": False,
                        },
                    )(),
                ]

            def download_activity_question(self, course_slug: str, activity: object):
                return None

        from scripts import main as module

        original_courses_dir = module.COURSES_DIR
        try:
            module.COURSES_DIR = self.root / "courses"
            target_course_dir = import_moodle_course("imported-course", 5, FakeMoodleClient())
        finally:
            module.COURSES_DIR = original_courses_dir

        mdx = (target_course_dir / "index.mdx").read_text(encoding="utf-8")
        chapter_mdx = (target_course_dir / "chapters" / "001-grundlagen.mdx").read_text(encoding="utf-8")
        metadata = load_sync_metadata(target_course_dir)

        self.assertIn('<Chapter src="./chapters/001-grundlagen.mdx" title="Grundlagen" />', mdx)
        self.assertIn('identifier="test-quadrat"', chapter_mdx)
        self.assertIn('title="Test Quadrat"', chapter_mdx)
        self.assertNotIn("previewUrl", chapter_mdx)
        self.assertIsNotNone(metadata)
        assert metadata is not None
        self.assertEqual(metadata.remote_course_id, 5)
        self.assertEqual(metadata.entries["test-quadrat"].remote_activity_id, 134)
        self.assertFalse(metadata.entries["test-dreieck"].remote_visible)

    def test_import_moodle_course_preserves_remote_section_and_activity_order(self) -> None:
        class FakeMoodleClient:
            base_url = "https://example.invalid"

            def list_course_h5p_activities(self, course_id: int):
                self.last_course_id = course_id
                return [
                    type(
                        "RemoteActivity",
                        (),
                        {
                            "identifier": "s2-b",
                            "title": "S2-B",
                            "activity_id": 203,
                            "instance_id": 63,
                            "section_title": "Sektion 2",
                            "section_index": 2,
                            "module_index": 1,
                            "intro": "Zweite Sektion, zweites H5P.",
                            "url": "https://example.invalid/mod/h5pactivity/view.php?id=203",
                            "visible": True,
                        },
                    )(),
                    type(
                        "RemoteActivity",
                        (),
                        {
                            "identifier": "s1-a",
                            "title": "S1-A",
                            "activity_id": 101,
                            "instance_id": 61,
                            "section_title": "Sektion 1",
                            "section_index": 1,
                            "module_index": 0,
                            "intro": "Erste Sektion, erstes H5P.",
                            "url": "https://example.invalid/mod/h5pactivity/view.php?id=101",
                            "visible": True,
                        },
                    )(),
                    type(
                        "RemoteActivity",
                        (),
                        {
                            "identifier": "s2-a",
                            "title": "S2-A",
                            "activity_id": 202,
                            "instance_id": 62,
                            "section_title": "Sektion 2",
                            "section_index": 2,
                            "module_index": 0,
                            "intro": "Zweite Sektion, erstes H5P.",
                            "url": "https://example.invalid/mod/h5pactivity/view.php?id=202",
                            "visible": True,
                        },
                    )(),
                ]

            def download_activity_question(self, course_slug: str, activity: object):
                return None

        from scripts import main as module

        original_courses_dir = module.COURSES_DIR
        try:
            module.COURSES_DIR = self.root / "courses"
            target_course_dir = import_moodle_course("imported-order", 5, FakeMoodleClient())
        finally:
            module.COURSES_DIR = original_courses_dir

        index_mdx = (target_course_dir / "index.mdx").read_text(encoding="utf-8")
        first_chapter_mdx = (target_course_dir / "chapters" / "001-sektion-1.mdx").read_text(encoding="utf-8")
        second_chapter_mdx = (target_course_dir / "chapters" / "002-sektion-2.mdx").read_text(encoding="utf-8")

        self.assertLess(index_mdx.index("001-sektion-1"), index_mdx.index("002-sektion-2"))
        self.assertIn('identifier="s1-a"', first_chapter_mdx)
        self.assertLess(second_chapter_mdx.index('identifier="s2-a"'), second_chapter_mdx.index('identifier="s2-b"'))

    def test_import_moodle_course_renders_subsection_headings_in_remote_position(self) -> None:
        class FakeMoodleClient:
            base_url = "https://example.invalid"

            def list_course_h5p_activities(self, course_id: int):
                return [
                    MoodleH5PActivity(
                        identifier="after",
                        title="Nachher",
                        course_id=course_id,
                        activity_id=12,
                        instance_id=22,
                        section_title="Grundlagen",
                        section_index=1,
                        module_index=2,
                        intro="",
                        url="https://example.invalid/mod/h5pactivity/view.php?id=12",
                    ),
                    MoodleH5PActivity(
                        identifier="task-one",
                        title="Aufgabe 1",
                        course_id=course_id,
                        activity_id=13,
                        instance_id=23,
                        section_title="Grundlagen",
                        subsection_title="Erste Aufgaben",
                        section_index=1,
                        module_index=1,
                        subsection_index=8,
                        submodule_index=0,
                        intro="",
                        url="https://example.invalid/mod/h5pactivity/view.php?id=13",
                    ),
                    MoodleH5PActivity(
                        identifier="before",
                        title="Vorher",
                        course_id=course_id,
                        activity_id=10,
                        instance_id=20,
                        section_title="Grundlagen",
                        section_index=1,
                        module_index=0,
                        intro="",
                        url="https://example.invalid/mod/h5pactivity/view.php?id=10",
                    ),
                ]

            def download_activity_question(self, course_slug: str, activity: object):
                return None

        from scripts import main as module

        original_courses_dir = module.COURSES_DIR
        try:
            module.COURSES_DIR = self.root / "courses"
            target_course_dir = import_moodle_course("imported-subsection-order", 5, FakeMoodleClient())
        finally:
            module.COURSES_DIR = original_courses_dir

        mdx = (target_course_dir / "chapters" / "001-grundlagen.mdx").read_text(encoding="utf-8")

        self.assertLess(mdx.index('identifier="before"'), mdx.index("### Erste Aufgaben"))
        self.assertLess(mdx.index("### Erste Aufgaben"), mdx.index('identifier="task-one"'))
        self.assertLess(mdx.index('identifier="task-one"'), mdx.index('identifier="after"'))

    def test_download_activity_question_persists_source_archive_for_public_packages(self) -> None:
        package_path = self.root / "public-package.h5p"
        package_path.write_bytes(
            self._build_h5p_archive_bytes(
                {"title": "Einführung: Farben", "mainLibrary": "H5P.PythonQuestion"},
                {
                    "contentType": "text_only",
                    "pythonRunner": "skulpt",
                    "contents": [{"type": "text", "text": "Aus Paket"}],
                    "editorSettings": {"instructions": "Aus Paket", "options": {}},
                    "gradingSettings": {},
                },
            )
        )

        from scripts import main as module

        original_fetch_text = module.fetch_text
        original_download_file = module.download_file
        original_courses_dir = module.COURSES_DIR
        try:
            module.COURSES_DIR = self.root / "courses"

            def fake_fetch_text(url: str) -> str:
                self.assertIn("view.php?id=145", url)
                return '<iframe src="https://example.invalid/h5p/embed.php?url=https%3A%2F%2Fexample.invalid%2Ffiles%2Feinfuhrung-farben.h5p"></iframe>'

            def fake_download_file(url: str, destination: Path) -> None:
                self.assertEqual(url, "https://example.invalid/files/einfuhrung-farben.h5p")
                destination.write_bytes(package_path.read_bytes())

            module.fetch_text = fake_fetch_text
            module.download_file = fake_download_file
            backup_extractor = module.moodle_backup_extractor()
            client = MoodleApiClient(
                "https://example.invalid",
                "token",
                make_stable_identifier=module.make_stable_identifier,
                strip_html=module.strip_html,
                fetch_text=module.fetch_text,
                extract_h5p_package_url_from_activity_html=lambda page_html: module.extract_h5p_package_url_from_activity_html(
                    page_html,
                    base_url="https://example.invalid",
                ),
                download_file=module.download_file,
                extract_h5p_package_from_course_backup=backup_extractor.extract_h5p_package_from_course_backup,
                build_imported_question_from_h5p_package=module.build_imported_question_from_h5p_package,
                write_source_package_sidecar=module.write_source_package_sidecar,
            )
            question = client.download_activity_question(
                "python-2026",
                MoodleH5PActivity(
                    identifier="einfuehrung-farben",
                    title="Einführung: Farben",
                    course_id=5,
                    activity_id=145,
                    instance_id=105,
                    section_title="Farben",
                    intro="",
                    url="https://example.invalid/mod/h5pactivity/view.php?id=145",
                ),
            )
        finally:
            module.fetch_text = original_fetch_text
            module.download_file = original_download_file
            module.COURSES_DIR = original_courses_dir

        assert question is not None
        self.assertEqual(question.source_package_path, "h5p/einfuehrung-farben")
        self.assertTrue((self.root / "courses" / "python-2026" / question.source_package_path).exists())

    def test_build_course_status_reports_modified_and_remote_only(self) -> None:
        metadata = SyncMetadata(
            course_slug="python-2026",
            remote_course_id=5,
            moodle_base_url="https://example.invalid",
        )
        _, questions, _ = parse_course(self.course_dir)
        metadata.entries["12eck"] = SyncMetadataEntry(
            identifier="12eck",
            remote_activity_id=134,
            remote_instance_id=77,
            remote_title="Zwölfeck zeichnen",
            remote_url="https://example.invalid/mod/h5pactivity/view.php?id=134",
            remote_visible=True,
            local_hash="stale",
        )
        metadata.entries["online-only"] = SyncMetadataEntry(
            identifier="online-only",
            remote_activity_id=999,
            remote_instance_id=88,
            remote_title="Nur Online",
            remote_url="https://example.invalid/mod/h5pactivity/view.php?id=999",
            remote_visible=True,
            local_hash="",
        )
        save_sync_metadata(self.course_dir, metadata)

        status = build_course_status(self.course_dir)

        self.assertEqual(status["counts"]["modified-local"], 1)
        self.assertEqual(status["counts"]["local-only"], 1)
        self.assertEqual(status["counts"]["remote-only"], 1)
        self.assertIn(
            {"identifier": "12eck", "title": "Zwölfeck zeichnen", "status": "modified-local", "remoteActivityId": 134},
            status["items"],
        )

    def test_sync_course_writes_h5p_files(self) -> None:
        from scripts import main as module

        original_courses_dir = module.COURSES_DIR
        original_runtime_libraries_dir = module.H5P_RUNTIME_LIBRARIES_DIR
        original_ensure_runtime = module.ensure_h5p_runtime_libraries
        try:
            module.COURSES_DIR = self.root / "courses"
            module.H5P_RUNTIME_LIBRARIES_DIR = self.root / ".h5p-runtime" / "libraries"
            module.ensure_h5p_runtime_libraries = lambda: None
            self._create_fake_library(module.H5P_RUNTIME_LIBRARIES_DIR, "H5P.Question", 1, 5, [])
            self._create_fake_library(
                module.H5P_RUNTIME_LIBRARIES_DIR,
                "H5P.LibCodeTools",
                6,
                73,
                [],
            )
            self._create_fake_library(
                module.H5P_RUNTIME_LIBRARIES_DIR,
                "H5P.CodeQuestion",
                6,
                73,
                [("H5P.Question", 1, 5), ("H5P.LibCodeTools", 6, 73)],
            )
            self._create_fake_library(
                module.H5P_RUNTIME_LIBRARIES_DIR,
                "H5P.PythonQuestion",
                6,
                73,
                [("H5P.CodeQuestion", 6, 73), ("H5P.LibCodeTools", 6, 73)],
            )
            questions = sync_course(self.course_dir)
        finally:
            module.COURSES_DIR = original_courses_dir
            module.H5P_RUNTIME_LIBRARIES_DIR = original_runtime_libraries_dir
            module.ensure_h5p_runtime_libraries = original_ensure_runtime

        self.assertEqual([question.identifier for question in questions], ["12eck", "quadrat"])
        archive = self.course_dir / "build" / "h5p" / "12eck.h5p"
        content_mdx = self.course_dir / "h5p" / "12eck" / "content.mdx"
        build_hash = self.course_dir / "build" / "hashes" / "12eck.build-hash"
        second_archive = self.course_dir / "build" / "h5p" / "quadrat.h5p"
        shared_libraries_dir = self.root / "libraries"
        self.assertTrue(archive.exists())
        self.assertTrue(content_mdx.exists())
        self.assertTrue(build_hash.exists())
        self.assertFalse((self.course_dir / "h5p" / "12eck.h5p").exists())
        self.assertFalse((self.course_dir / "h5p" / ".12eck.build-hash").exists())
        self.assertFalse((self.course_dir / "h5p" / "orphaned-old-package.h5p").exists())
        self.assertFalse((self.course_dir / "h5p" / ".orphaned-old-package.build-hash").exists())
        self.assertFalse((self.course_dir / "h5p" / "12eck" / "12eck.h5p").exists())
        self.assertFalse((self.course_dir / "h5p" / "12eck" / ".build-hash").exists())
        self.assertFalse((self.course_dir / "h5p" / "12eck" / "content.json").exists())
        self.assertTrue(second_archive.exists())
        self.assertTrue((shared_libraries_dir / "H5P.PythonQuestion-6.73").exists())
        self.assertTrue((shared_libraries_dir / "H5P.CodeQuestion-6.73").exists())
        self.assertTrue((shared_libraries_dir / "H5P.LibCodeTools-6.73").exists())
        self.assertTrue((shared_libraries_dir / "H5P.Question-1.5").exists())
        self.assertFalse((self.course_dir / "h5p" / "libraries").exists())
        self.assertFalse((self.course_dir / "h5p" / "12eck" / "H5P.PythonQuestion-6.73").exists())

        settings_yaml = self.course_dir / "h5p" / "12eck" / "settings.yml"
        payload = yaml.safe_load(settings_yaml.read_text(encoding="utf-8"))
        self.assertEqual(payload["pythonRunner"], "pyodide")

        metadata = json.loads((self.course_dir / "h5p" / "12eck" / "h5p.json").read_text(encoding="utf-8"))
        self.assertEqual(metadata["preloadedDependencies"][0]["machineName"], "H5P.PythonQuestion")

        with ZipFile(archive) as package:
            package_names = set(package.namelist())
        self.assertNotIn("12eck.h5p", package_names)
        self.assertIn("H5P.PythonQuestion-6.73/library.json", package_names)
        self.assertIn("H5P.CodeQuestion-6.73/library.json", package_names)
        self.assertIn("H5P.LibCodeTools-6.73/library.json", package_names)
        self.assertIn("H5P.Question-1.5/library.json", package_names)

    def test_sync_course_package_skips_hidden_library_files_and_duplicate_entries(self) -> None:
        from scripts import main as module

        original_courses_dir = module.COURSES_DIR
        original_runtime_libraries_dir = module.H5P_RUNTIME_LIBRARIES_DIR
        original_ensure_runtime = module.ensure_h5p_runtime_libraries
        try:
            module.COURSES_DIR = self.root / "courses"
            module.H5P_RUNTIME_LIBRARIES_DIR = self.root / ".h5p-runtime" / "libraries"
            module.ensure_h5p_runtime_libraries = lambda: None
            self._create_fake_library(module.H5P_RUNTIME_LIBRARIES_DIR, "H5P.Question", 1, 5, [])
            self._create_fake_library(module.H5P_RUNTIME_LIBRARIES_DIR, "H5P.LibCodeTools", 6, 73, [])
            self._create_fake_library(
                module.H5P_RUNTIME_LIBRARIES_DIR,
                "H5P.CodeQuestion",
                6,
                73,
                [("H5P.Question", 1, 5), ("H5P.LibCodeTools", 6, 73)],
            )
            self._create_fake_library(
                module.H5P_RUNTIME_LIBRARIES_DIR,
                "H5P.PythonQuestion",
                6,
                73,
                [("H5P.CodeQuestion", 6, 73), ("H5P.LibCodeTools", 6, 73)],
            )

            python_library_dir = self.root / "libraries" / "H5P.PythonQuestion-6.73"
            (python_library_dir / ".git").mkdir(parents=True)
            (python_library_dir / ".git" / "config").write_text("ignored", encoding="utf-8")
            (python_library_dir / ".gitignore").write_text("ignored", encoding="utf-8")

            sync_course(self.course_dir)
        finally:
            module.COURSES_DIR = original_courses_dir
            module.H5P_RUNTIME_LIBRARIES_DIR = original_runtime_libraries_dir
            module.ensure_h5p_runtime_libraries = original_ensure_runtime

        archive = self.course_dir / "build" / "h5p" / "12eck.h5p"
        with ZipFile(archive) as package:
            package_names = package.namelist()

        self.assertEqual(len(package_names), len(set(package_names)))
        self.assertNotIn("H5P.PythonQuestion-6.73/.git/config", package_names)
        self.assertNotIn("H5P.PythonQuestion-6.73/.gitignore", package_names)

    def test_ensure_custom_h5p_libraries_skips_release_lookup_when_libraries_exist(self) -> None:
        from scripts import main as module

        original_libraries_dir = module.H5P_RUNTIME_LIBRARIES_DIR
        original_downloads_dir = module.H5P_RUNTIME_DOWNLOADS_DIR
        original_fetch_json = module.fetch_json
        try:
            module.H5P_RUNTIME_LIBRARIES_DIR = self.root / ".h5p-runtime" / "libraries"
            module.H5P_RUNTIME_DOWNLOADS_DIR = self.root / ".h5p-runtime" / "downloads"
            for machine_name in module.H5P_LIBRARY_ASSET_PREFIXES:
                self._create_fake_library(module.H5P_RUNTIME_LIBRARIES_DIR, machine_name, 6, 73, [])

            def fail_fetch(url: str) -> dict:
                raise AssertionError(f"fetch_json should not be called: {url}")

            module.fetch_json = fail_fetch
            module.h5p_library_manager().ensure_custom_h5p_libraries()
        finally:
            module.H5P_RUNTIME_LIBRARIES_DIR = original_libraries_dir
            module.H5P_RUNTIME_DOWNLOADS_DIR = original_downloads_dir
            module.fetch_json = original_fetch_json

    def test_ensure_custom_h5p_libraries_uses_cached_release_metadata(self) -> None:
        from scripts import main as module
        from scripts.classes import H5PLibraryManager

        original_libraries_dir = module.H5P_RUNTIME_LIBRARIES_DIR
        original_downloads_dir = module.H5P_RUNTIME_DOWNLOADS_DIR
        original_fetch_json = module.fetch_json
        original_download_file = module.download_file
        original_extract_library_asset = H5PLibraryManager.extract_library_asset
        original_register_local_library = H5PLibraryManager.register_local_library
        try:
            module.H5P_RUNTIME_LIBRARIES_DIR = self.root / ".h5p-runtime" / "libraries"
            module.H5P_RUNTIME_DOWNLOADS_DIR = self.root / ".h5p-runtime" / "downloads"
            module.H5P_RUNTIME_DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)

            cache_path = module.h5p_library_manager().release_metadata_cache_path()
            cache_path.write_text(
                json.dumps(
                    {
                        "assets": [
                            {
                                "name": "H5P.PythonQuestion-6.73_cached.h5p",
                                "browser_download_url": "https://example.invalid/python-question.h5p",
                            },
                            {
                                "name": "H5P.CodeQuestion-6.73_cached.h5p",
                                "browser_download_url": "https://example.invalid/code-question.h5p",
                            },
                            {
                                "name": "H5P.LibCodeTools-6.73_cached.h5p",
                                "browser_download_url": "https://example.invalid/lib-code-tools.h5p",
                            },
                            {
                                "name": "H5PEditor.CodeWidget-6.73_cached.h5p",
                                "browser_download_url": "https://example.invalid/code-widget.h5p",
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )

            downloaded: list[tuple[str, Path]] = []
            extracted: list[tuple[Path, str]] = []
            registered: list[Path] = []

            def fail_fetch(url: str) -> dict:
                raise AssertionError(f"fetch_json should not be called: {url}")

            def fake_download(url: str, destination: Path) -> None:
                downloaded.append((url, destination))
                destination.write_bytes(b"fake")

            def fake_extract(self: H5PLibraryManager, archive_path: Path, machine_name: str) -> Path:
                extracted.append((archive_path, machine_name))
                library_dir = module.H5P_RUNTIME_LIBRARIES_DIR / f"{machine_name}-6.73"
                library_dir.mkdir(parents=True, exist_ok=True)
                (library_dir / "library.json").write_text(
                    json.dumps({"machineName": machine_name, "majorVersion": 6, "minorVersion": 73}),
                    encoding="utf-8",
                )
                return library_dir

            def fake_register(self: H5PLibraryManager, library_dir: Path) -> None:
                registered.append(library_dir)

            module.fetch_json = fail_fetch
            module.download_file = fake_download
            H5PLibraryManager.extract_library_asset = fake_extract
            H5PLibraryManager.register_local_library = fake_register

            module.h5p_library_manager().ensure_custom_h5p_libraries()
        finally:
            module.H5P_RUNTIME_LIBRARIES_DIR = original_libraries_dir
            module.H5P_RUNTIME_DOWNLOADS_DIR = original_downloads_dir
            module.fetch_json = original_fetch_json
            module.download_file = original_download_file
            H5PLibraryManager.extract_library_asset = original_extract_library_asset
            H5PLibraryManager.register_local_library = original_register_local_library

        self.assertEqual(len(downloaded), 4)
        self.assertEqual(downloaded[0][0], "https://example.invalid/python-question.h5p")
        self.assertEqual(extracted[0][1], "H5P.PythonQuestion")
        self.assertEqual(len(registered), 4)

    def _create_fake_library(
        self,
        libraries_dir: Path,
        machine_name: str,
        major_version: int,
        minor_version: int,
        dependencies: list[tuple[str, int, int]],
    ) -> None:
        library_dir = libraries_dir / f"{machine_name}-{major_version}.{minor_version}"
        library_dir.mkdir(parents=True)
        (library_dir / "library.json").write_text(
            json.dumps(
                {
                    "machineName": machine_name,
                    "majorVersion": major_version,
                    "minorVersion": minor_version,
                    "preloadedDependencies": [
                        {
                            "machineName": dependency_name,
                            "majorVersion": dependency_major,
                            "minorVersion": dependency_minor,
                        }
                        for dependency_name, dependency_major, dependency_minor in dependencies
                    ],
                }
            ),
            encoding="utf-8",
        )

    def _build_h5p_archive_bytes(
        self,
        metadata: dict[str, object],
        content: dict[str, object],
        extra_files: dict[str, bytes | str] | None = None,
        libraries: dict[str, dict[str, object]] | None = None,
    ) -> bytes:
        buffer = BytesIO()
        with ZipFile(buffer, "w") as archive:
            archive.writestr("h5p.json", json.dumps(metadata))
            archive.writestr("content/content.json", json.dumps(content))
            for library_root, library_metadata in (libraries or {}).items():
                archive.writestr(f"{library_root}/library.json", json.dumps(library_metadata))
                archive.writestr(f"{library_root}/scripts/example.js", "console.log('ok');")
            for path, payload in (extra_files or {}).items():
                archive.writestr(path, payload)
        return buffer.getvalue()

    def _add_tar_text(self, archive: tarfile.TarFile, name: str, content: str) -> None:
        self._add_tar_bytes(archive, name, content.encode("utf-8"))

    def _add_tar_bytes(self, archive: tarfile.TarFile, name: str, content: bytes) -> None:
        info = tarfile.TarInfo(name)
        info.size = len(content)
        archive.addfile(info, BytesIO(content))


if __name__ == "__main__":
    unittest.main()
