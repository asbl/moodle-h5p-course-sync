from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from scripts.classes.moodle_playwright_uploader import (
    MoodleH5PUploadPackage,
    MoodlePlaywrightUploader,
    collect_h5p_upload_packages,
    normalize_moodle_identifier,
    normalize_moodle_section_title,
    read_chapter_question_order,
)


class MoodlePlaywrightUploaderTests(unittest.TestCase):
    def test_normalize_moodle_identifier_matches_import_slugs(self) -> None:
        self.assertEqual(normalize_moodle_identifier("Einführung: Die while-Schleife"), "einfuehrung-die-while-schleife")
        self.assertEqual(normalize_moodle_identifier("Die max() Funktion anwenden"), "die-max-funktion-anwenden")

    def test_normalize_moodle_section_title_matches_local_and_remote_variants(self) -> None:
        self.assertEqual(normalize_moodle_section_title("Texte und Strings"), "texte strings")
        self.assertEqual(normalize_moodle_section_title("Texte (Strings)"), "texte strings")
        self.assertEqual(normalize_moodle_section_title("Schleifen and Bedingungen"), "schleifen bedingungen")

    def test_collect_h5p_upload_packages_reads_title_from_archive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            course_dir = Path(tmp_dir) / "python-2026"
            build_dir = course_dir / "build" / "h5p" / "012-miniworlds"
            build_dir.mkdir(parents=True)
            package_path = build_dir / "miniworlds-tutorial.h5p"
            with zipfile.ZipFile(package_path, "w") as archive:
                archive.writestr("h5p.json", json.dumps({"title": "Minis erkunden"}))

            packages = collect_h5p_upload_packages(course_dir, "012-miniworlds")

        self.assertEqual(len(packages), 1)
        self.assertEqual(packages[0].identifier, "miniworlds-tutorial")
        self.assertEqual(packages[0].title, "Minis erkunden")
        self.assertEqual(packages[0].path, package_path)

    def test_collect_h5p_upload_packages_uses_chapter_question_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            course_dir = Path(tmp_dir) / "python-2026"
            build_dir = course_dir / "build" / "h5p" / "013-texte-strings"
            chapter_dir = course_dir / "chapters"
            build_dir.mkdir(parents=True)
            chapter_dir.mkdir(parents=True)
            (chapter_dir / "013-texte-strings.mdx").write_text(
                '\n'.join(
                    [
                        '<PythonQuestion identifier="strings-grundlagen" />',
                        '<PythonQuestion identifier="p5-textposter" />',
                        '<PythonQuestion identifier="bonus-kleiner-textgenerator" />',
                    ]
                ),
                encoding="utf-8",
            )
            for identifier, title in [
                ("bonus-kleiner-textgenerator", "Bonus"),
                ("strings-grundlagen", "Grundlagen"),
                ("p5-textposter", "Poster"),
            ]:
                with zipfile.ZipFile(build_dir / f"{identifier}.h5p", "w") as archive:
                    archive.writestr("h5p.json", json.dumps({"title": title}))

            packages = collect_h5p_upload_packages(course_dir, "013-texte-strings")

        self.assertEqual(
            [package.identifier for package in packages],
            ["strings-grundlagen", "p5-textposter", "bonus-kleiner-textgenerator"],
        )

    def test_collect_h5p_upload_packages_ignores_stale_build_files_when_chapter_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            course_dir = Path(tmp_dir) / "python-2026"
            build_dir = course_dir / "build" / "h5p" / "013-texte-strings"
            chapter_dir = course_dir / "chapters"
            build_dir.mkdir(parents=True)
            chapter_dir.mkdir(parents=True)
            (chapter_dir / "013-texte-strings.mdx").write_text(
                '<PythonQuestion identifier="strings-grundlagen" />\n',
                encoding="utf-8",
            )
            for identifier, title in [
                ("strings-grundlagen", "Grundlagen"),
                ("p5-textposter", "Altes p5 Paket"),
            ]:
                with zipfile.ZipFile(build_dir / f"{identifier}.h5p", "w") as archive:
                    archive.writestr("h5p.json", json.dumps({"title": title}))

            packages = collect_h5p_upload_packages(course_dir, "013-texte-strings")

        self.assertEqual([package.identifier for package in packages], ["strings-grundlagen"])

    def test_read_chapter_question_order_reads_multiline_tags(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            course_dir = Path(tmp_dir) / "python-2026"
            chapter_dir = course_dir / "chapters"
            chapter_dir.mkdir(parents=True)
            (chapter_dir / "013-texte-strings.mdx").write_text(
                '<PythonQuestion\n  identifier="strings-grundlagen"\n/>\n'
                '<PythonQuestion\n  identifier="test-namensschild"\n/>\n',
                encoding="utf-8",
            )

            order = read_chapter_question_order(course_dir, "013-texte-strings")

        self.assertEqual(order, ["strings-grundlagen", "test-namensschild"])

    def test_find_or_create_section_continues_when_rename_form_is_missing(self) -> None:
        class UploaderWithMissingRenameForm(MoodlePlaywrightUploader):
            def __init__(self) -> None:
                super().__init__(course_url="https://example.invalid/course/view.php?id=5", section_title="Texte")
                self.created = False

            def _find_section(self, page):  # type: ignore[no-untyped-def]
                return None

            def _find_section_containing_packages(self, page, packages):  # type: ignore[no-untyped-def]
                return None

            def _create_section_at_end(self, page):  # type: ignore[no-untyped-def]
                self.created = True
                return {"selector": "[data-section='9']", "number": 9, "title": ""}

            def _rename_section(self, page, section_number: int, title: str) -> bool:  # type: ignore[no-untyped-def]
                assert section_number == 9
                assert title == "Texte"
                return False

            def _turn_editing_on(self, page):  # type: ignore[no-untyped-def]
                return None

            def _section_by_number(self, page, section_number: int):  # type: ignore[no-untyped-def]
                return {"selector": "[data-section='9']", "number": section_number, "title": ""}

            def _last_section(self, page):  # type: ignore[no-untyped-def]
                return {"selector": "[data-section='9']", "number": 9, "title": ""}

        class FakePage:
            def __init__(self) -> None:
                self.urls: list[str] = []

            def goto(self, url: str, **kwargs: object) -> None:
                self.urls.append(url)

        uploader = UploaderWithMissingRenameForm()
        page = FakePage()

        section = uploader._find_or_create_section(page, [])

        self.assertTrue(uploader.created)
        self.assertEqual(section["number"], 9)
        self.assertEqual(
            page.urls,
            [
                "https://example.invalid/course/view.php?id=5",
                "https://example.invalid/course/view.php?id=5",
            ],
        )

    def test_find_or_create_section_reuses_section_with_existing_packages(self) -> None:
        class UploaderWithExistingPackageSection(MoodlePlaywrightUploader):
            def __init__(self) -> None:
                super().__init__(course_url="https://example.invalid/course/view.php?id=5", section_title="Texte")
                self.created = False

            def _find_section(self, page):  # type: ignore[no-untyped-def]
                return None

            def _find_section_containing_packages(self, page, packages):  # type: ignore[no-untyped-def]
                return {"selector": "[data-section='26']", "number": 26, "title": "Texte"}

            def _create_section_at_end(self, page):  # type: ignore[no-untyped-def]
                self.created = True
                return {"selector": "[data-section='27']", "number": 27, "title": ""}

        uploader = UploaderWithExistingPackageSection()

        section = uploader._find_or_create_section(object(), [])

        self.assertFalse(uploader.created)
        self.assertEqual(section["number"], 26)

    def test_find_or_create_section_renames_section_with_existing_packages(self) -> None:
        class UploaderWithExistingPackageSection(MoodlePlaywrightUploader):
            def __init__(self) -> None:
                super().__init__(course_url="https://example.invalid/course/view.php?id=5", section_title="Texte")
                self.renamed: list[tuple[int, str]] = []

            def _find_section(self, page):  # type: ignore[no-untyped-def]
                if self.renamed:
                    return {"selector": "[data-section='26']", "number": 26, "title": "Texte"}
                return None

            def _find_section_containing_packages(self, page, packages):  # type: ignore[no-untyped-def]
                return {"selector": "[data-section='26']", "number": 26, "title": "Neuer Abschnitt"}

            def _rename_section(self, page, section_number: int, title: str) -> bool:  # type: ignore[no-untyped-def]
                self.renamed.append((section_number, title))
                return True

            def _section_by_number(self, page, section_number: int):  # type: ignore[no-untyped-def]
                return {"selector": "[data-section='26']", "number": section_number, "title": "Texte"}

            def _turn_editing_on(self, page):  # type: ignore[no-untyped-def]
                return None

        class FakePage:
            def __init__(self) -> None:
                self.urls: list[str] = []

            def goto(self, url: str, **kwargs: object) -> None:
                self.urls.append(url)

        uploader = UploaderWithExistingPackageSection()

        section = uploader._find_or_create_section(FakePage(), [])

        self.assertEqual(uploader.renamed, [(26, "Texte")])
        self.assertEqual(section["title"], "Texte")

    def test_find_or_create_section_prefers_package_section_over_matching_empty_title_section(self) -> None:
        class UploaderWithDuplicateTitleSection(MoodlePlaywrightUploader):
            def __init__(self) -> None:
                super().__init__(course_url="https://example.invalid/course/view.php?id=5", section_title="Texte")
                self.renamed: list[tuple[int, str]] = []

            def _find_section(self, page):  # type: ignore[no-untyped-def]
                return {"selector": "[data-section='10']", "number": 10, "title": "Texte"}

            def _section_contains_packages(self, page, section, packages):  # type: ignore[no-untyped-def]
                return False

            def _find_section_containing_packages(self, page, packages):  # type: ignore[no-untyped-def]
                return {"selector": "[data-section='16']", "number": 16, "title": "Alter Titel"}

            def _section_by_number(self, page, section_number: int):  # type: ignore[no-untyped-def]
                return {"selector": "[data-section='16']", "number": section_number, "title": "Texte"}

            def _rename_section(self, page, section_number: int, title: str) -> bool:  # type: ignore[no-untyped-def]
                self.renamed.append((section_number, title))
                return True

            def _turn_editing_on(self, page):  # type: ignore[no-untyped-def]
                return None

        class FakePage:
            def goto(self, url: str, **kwargs: object) -> None:
                return None

        uploader = UploaderWithDuplicateTitleSection()

        section = uploader._find_or_create_section(
            FakePage(),
            [MoodleH5PUploadPackage("strings-grundlagen", "Strings: Grundlagen", Path("a.h5p"))],
        )

        self.assertEqual(section["number"], 16)
        self.assertEqual(uploader.renamed, [(16, "Texte")])

    def test_sort_section_h5p_activities_moves_reverse_into_desired_order(self) -> None:
        class SortUploader(MoodlePlaywrightUploader):
            def __init__(self) -> None:
                super().__init__(course_url="https://example.invalid/course/view.php?id=5", section_title="Texte")
                self.activities = [
                    {"id": 3, "identifier": "c", "title": "C"},
                    {"id": 1, "identifier": "a", "title": "A"},
                    {"id": 2, "identifier": "b", "title": "B"},
                ]
                self.moves: list[tuple[int, int]] = []

            def _section_by_number(self, page, section_number: int):  # type: ignore[no-untyped-def]
                return {"selector": "[data-section='9']", "number": section_number, "title": ""}

            def _collect_section_h5p_activity_order(self, page, section_selector: str):  # type: ignore[no-untyped-def]
                return list(self.activities)

            def _move_activity_before(self, page, activity_id: int, before_activity_id: int) -> None:
                self.moves.append((activity_id, before_activity_id))
                self.activities = [activity for activity in self.activities if activity["id"] != activity_id]
                before_index = next(
                    index for index, activity in enumerate(self.activities) if activity["id"] == before_activity_id
                )
                self.activities.insert(before_index, {"id": activity_id, "identifier": chr(96 + activity_id), "title": ""})

            def _turn_editing_on(self, page):  # type: ignore[no-untyped-def]
                return None

        class FakePage:
            def goto(self, url: str, **kwargs: object) -> None:
                return None

        uploader = SortUploader()
        packages = [
            MoodleH5PUploadPackage("a", "A", Path("a.h5p")),
            MoodleH5PUploadPackage("b", "B", Path("b.h5p")),
            MoodleH5PUploadPackage("c", "C", Path("c.h5p")),
        ]

        uploader._sort_section_h5p_activities(FakePage(), 9, packages)

        self.assertEqual(uploader.moves, [(2, 3), (1, 2)])
        self.assertEqual([activity["id"] for activity in uploader.activities], [1, 2, 3])

    def test_sort_section_h5p_activities_ignores_existing_ids_outside_target_section(self) -> None:
        class SortUploader(MoodlePlaywrightUploader):
            def __init__(self) -> None:
                super().__init__(
                    course_url="https://example.invalid/course/view.php?id=5",
                    section_title="Texte",
                    existing_activity_ids={"a": 10, "b": 11},
                )
                self.moves: list[tuple[int, int]] = []

            def _section_by_number(self, page, section_number: int):  # type: ignore[no-untyped-def]
                return {"selector": "[data-section='19']", "number": section_number, "title": ""}

            def _collect_section_h5p_activity_order(self, page, section_selector: str):  # type: ignore[no-untyped-def]
                return []

            def _move_activity_before(self, page, activity_id: int, before_activity_id: int) -> None:
                self.moves.append((activity_id, before_activity_id))

        uploader = SortUploader()
        packages = [
            MoodleH5PUploadPackage("a", "A", Path("a.h5p")),
            MoodleH5PUploadPackage("b", "B", Path("b.h5p")),
        ]

        uploader._sort_section_h5p_activities(object(), 19, packages)

        self.assertEqual(uploader.moves, [])

    def test_create_section_at_end_raises_when_no_new_section_was_created(self) -> None:
        class UploaderWithoutCreation(MoodlePlaywrightUploader):
            def __init__(self) -> None:
                super().__init__(course_url="https://example.invalid/course/view.php?id=5", section_title="Funktionen")

            def _last_section_number(self, page):  # type: ignore[no-untyped-def]
                return 12

            def _create_section_by_url(self, page, before):  # type: ignore[no-untyped-def]
                return False

            def _create_section_by_ui(self, page, before):  # type: ignore[no-untyped-def]
                return False

        uploader = UploaderWithoutCreation()

        with self.assertRaisesRegex(RuntimeError, "konnte keine neue Section"):
            uploader._create_section_at_end(object())


if __name__ == "__main__":
    unittest.main()
