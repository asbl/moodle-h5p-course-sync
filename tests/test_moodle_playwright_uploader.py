from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from scripts.classes.moodle_playwright_uploader import (
    MoodleH5PUploadPackage,
    MoodleH5PUploadResult,
    MoodlePlaywrightUploader,
    collect_h5p_upload_packages,
    infer_h5p_package_points,
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
        self.assertEqual(packages[0].points, 2)

    def test_infer_h5p_package_points_returns_zero_for_ungraded_python_question(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            package_path = Path(tmp_dir) / "plain-text.h5p"
            with zipfile.ZipFile(package_path, "w") as archive:
                archive.writestr("h5p.json", json.dumps({"mainLibrary": "H5P.PythonQuestion"}))
                archive.writestr(
                    "content/content.json",
                    json.dumps({"contentType": "text_only", "gradingSettings": {"gradingMethod": "please_choose"}}),
                )

            points = infer_h5p_package_points(package_path)

        self.assertEqual(points, 0)

    def test_infer_h5p_package_points_returns_two_for_graded_python_question(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            package_path = Path(tmp_dir) / "graded.h5p"
            with zipfile.ZipFile(package_path, "w") as archive:
                archive.writestr("h5p.json", json.dumps({"mainLibrary": "H5P.PythonQuestion"}))
                archive.writestr(
                    "content/content.json",
                    json.dumps({"contentType": "ide_only", "gradingSettings": {"gradingMethod": "ioTestCases"}}),
                )

            points = infer_h5p_package_points(package_path)

        self.assertEqual(points, 2)

    def test_set_completion_tracking_sets_hidden_gradepass_without_fill_timeout(self) -> None:
        class FakeLocator:
            def __init__(self, *, visible: bool = True, checked: bool = False) -> None:
                self.first = self
                self.visible = visible
                self.checked = checked
                self.filled_values: list[str] = []
                self.evaluated_values: list[str | None] = []
                self.selected_values: list[str] = []

            def count(self) -> int:
                return 1

            def is_visible(self) -> bool:
                return self.visible

            def fill(self, value: str) -> None:
                if not self.visible:
                    raise AssertionError("hidden locator must not be filled through Playwright")
                self.filled_values.append(value)

            def evaluate(self, script: str, value: str | None = None) -> None:
                self.evaluated_values.append(value)

            def select_option(self, *, value: str) -> None:
                self.selected_values.append(value)

            def is_checked(self) -> bool:
                return self.checked

            def check(self) -> None:
                self.checked = True

        class EmptyLocator:
            first = None

            def count(self) -> int:
                return 0

        class FakePage:
            def __init__(self) -> None:
                self.gradepass = FakeLocator(visible=False)
                self.completion = FakeLocator()
                self.grade_condition = FakeLocator(checked=False)
                self.passgrade_condition = FakeLocator(visible=False, checked=False)
                self.waits: list[int] = []

            def locator(self, selector: str):  # type: ignore[no-untyped-def]
                if "gradepass" in selector:
                    return self.gradepass
                if "completionusegrade" in selector:
                    return self.grade_condition
                if "completionpassgrade" in selector:
                    return self.passgrade_condition
                if 'select[name="completion"]' in selector:
                    return self.completion
                return EmptyLocator()

            def wait_for_timeout(self, timeout: int) -> None:
                self.waits.append(timeout)

        page = FakePage()
        uploader = MoodlePlaywrightUploader(course_url="https://example.invalid/course/view.php?id=7845")

        uploader._set_completion_tracking(page, gradepass=1)

        self.assertEqual(page.gradepass.filled_values, [])
        self.assertEqual(page.gradepass.evaluated_values, ["1"])
        self.assertEqual(page.completion.selected_values, ["2"])
        self.assertTrue(page.grade_condition.checked)
        self.assertEqual(page.passgrade_condition.evaluated_values, [None])

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

    def test_headless_login_if_needed_reports_external_sso_redirect(self) -> None:
        class EmptyLocator:
            def count(self) -> int:
                return 0

        class FakePage:
            url = "https://login.schulportal.hessen.de/saml/singleSignOn?SAMLRequest=test"

            def locator(self, selector: str):  # type: ignore[no-untyped-def]
                return EmptyLocator()

        uploader = MoodlePlaywrightUploader(
            course_url="https://mo5235.schulportal.hessen.de/course/view.php?id=7845",
            section_title="Listen",
            headless=True,
        )

        with self.assertRaisesRegex(RuntimeError, "abgelaufener SSO-Storage-State.*singleSignOn"):
            uploader._login_if_needed(FakePage())

    def test_login_with_credentials_submits_schulportal_hessen_sso(self) -> None:
        class FakeLocator:
            def __init__(
                self,
                page,
                name: str,
                present: bool = True,
                visible: bool = True,
                enabled: bool = True,
            ) -> None:  # type: ignore[no-untyped-def]
                self.page = page
                self.name = name
                self.present = present
                self.visible = visible
                self.enabled = enabled
                self.first = self

            def count(self) -> int:
                return 1 if self.present else 0

            def nth(self, index: int):  # type: ignore[no-untyped-def]
                return self

            def is_visible(self) -> bool:
                return self.visible

            def is_enabled(self) -> bool:
                return self.enabled

            def fill(self, value: str) -> None:
                self.page.filled[self.name] = value

            def click(self) -> None:
                self.page.clicks.append(self.name)
                if self.name == "submit":
                    self.page.url = self.page.course_url

        class EmptyLocator:
            first = None

            def count(self) -> int:
                return 0

        class FakePage:
            def __init__(self) -> None:
                self.course_url = "https://mo5235.schulportal.hessen.de/course/view.php?id=7845"
                self.url = "https://login.schulportal.hessen.de/saml/singleSignOn?SAMLRequest=test"
                self.logged_in = False
                self.filled: dict[str, str] = {}
                self.clicks: list[str] = []
                self.gotos: list[str] = []

            def locator(self, selector: str):  # type: ignore[no-untyped-def]
                if selector == 'input[name="username"]':
                    return FakeLocator(self, "username", not self.logged_in)
                if selector == 'input[name="password"]':
                    return FakeLocator(self, "password", not self.logged_in)
                if selector == 'button[type="submit"]':
                    return FakeLocator(self, "submit", not self.logged_in)
                if selector in (
                    ".logininfo a[href*='login/index.php'], a[href*='login/index.php']",
                    ".usermenu, [data-region='usermenu'], a[href*='login/logout.php'], .logininfo a[href*='login/logout.php']",
                ):
                    return EmptyLocator()
                return EmptyLocator()

            def goto(self, url: str, **kwargs: object) -> None:
                self.gotos.append(url)
                self.url = url
                if url == "https://login.schulportal.hessen.de/?i=5235":
                    return
                if url == self.course_url:
                    self.logged_in = True

            def wait_for_load_state(self, state: str) -> None:
                return None

        page = FakePage()
        uploader = MoodlePlaywrightUploader(
            course_url=page.course_url,
            section_title="Listen",
            username="alice",
            password="secret",
            headless=True,
        )

        uploader._login_with_credentials_if_needed(page)

        self.assertEqual(page.filled, {"username": "alice", "password": "secret"})
        self.assertEqual(page.clicks, ["submit"])
        self.assertEqual(page.gotos, ["https://login.schulportal.hessen.de/?i=5235", page.course_url])
        self.assertEqual(page.url, page.course_url)

    def test_schulportal_hessen_login_skips_hidden_user_input(self) -> None:
        class FakeLocator:
            def __init__(self, page, items: list[dict[str, object]], index: int = 0) -> None:  # type: ignore[no-untyped-def]
                self.page = page
                self.items = items
                self.index = index
                self.first = self if index == 0 else self.nth(0)

            def count(self) -> int:
                return len(self.items)

            def nth(self, index: int):  # type: ignore[no-untyped-def]
                return FakeLocator(self.page, self.items, index)

            def is_visible(self) -> bool:
                return bool(self.items[self.index].get("visible", True))

            def is_enabled(self) -> bool:
                return bool(self.items[self.index].get("enabled", True))

            def fill(self, value: str) -> None:
                self.page.filled[str(self.items[self.index]["name"])] = value

            def click(self) -> None:
                self.page.clicks.append(str(self.items[self.index]["name"]))
                if self.items[self.index]["name"] == "submit":
                    self.page.url = self.page.course_url

        class EmptyLocator:
            first = None

            def count(self) -> int:
                return 0

        class FakePage:
            def __init__(self) -> None:
                self.course_url = "https://mo5235.schulportal.hessen.de/course/view.php?id=7845"
                self.url = "https://login.schulportal.hessen.de/saml/singleSignOn?SAMLRequest=test"
                self.logged_in = False
                self.filled: dict[str, str] = {}
                self.clicks: list[str] = []
                self.gotos: list[str] = []

            def locator(self, selector: str):  # type: ignore[no-untyped-def]
                if selector == 'input[name="user"]':
                    return FakeLocator(self, [{"name": "hidden-user", "visible": False}])
                if selector == 'input[type="text"]':
                    return FakeLocator(self, [{"name": "visible-user"}])
                if selector == 'input[name="password"]':
                    return FakeLocator(self, [{"name": "password"}])
                if selector == 'button[type="submit"]':
                    return FakeLocator(self, [{"name": "submit"}])
                return EmptyLocator()

            def goto(self, url: str, **kwargs: object) -> None:
                self.gotos.append(url)
                self.url = url
                if url == self.course_url:
                    self.logged_in = True

            def wait_for_load_state(self, state: str) -> None:
                return None

        page = FakePage()
        uploader = MoodlePlaywrightUploader(
            course_url=page.course_url,
            section_title="Listen",
            username="alice",
            password="secret",
            headless=True,
        )

        uploader._login_with_credentials_if_needed(page)

        self.assertEqual(page.filled, {"visible-user": "alice", "password": "secret"})
        self.assertEqual(page.gotos, ["https://login.schulportal.hessen.de/?i=5235", page.course_url])

    def test_schulportal_hessen_login_keeps_existing_instance_login_url(self) -> None:
        class FakeLocator:
            def __init__(self, page, name: str) -> None:  # type: ignore[no-untyped-def]
                self.page = page
                self.name = name
                self.first = self

            def count(self) -> int:
                return 1

            def nth(self, index: int):  # type: ignore[no-untyped-def]
                return self

            def is_visible(self) -> bool:
                return True

            def is_enabled(self) -> bool:
                return True

            def fill(self, value: str) -> None:
                self.page.filled[self.name] = value

            def click(self) -> None:
                self.page.clicks.append(self.name)
                if self.name == "submit":
                    self.page.url = self.page.course_url

        class EmptyLocator:
            first = None

            def count(self) -> int:
                return 0

        class FakePage:
            def __init__(self) -> None:
                self.course_url = "https://mo5235.schulportal.hessen.de/course/view.php?id=7845"
                self.url = "https://login.schulportal.hessen.de/?i=5235"
                self.filled: dict[str, str] = {}
                self.clicks: list[str] = []
                self.gotos: list[str] = []

            def locator(self, selector: str):  # type: ignore[no-untyped-def]
                if selector == 'input[name="username"]':
                    return FakeLocator(self, "username")
                if selector == 'input[name="password"]':
                    return FakeLocator(self, "password")
                if selector == 'button[type="submit"]':
                    return FakeLocator(self, "submit")
                return EmptyLocator()

            def goto(self, url: str, **kwargs: object) -> None:
                self.gotos.append(url)
                self.url = url

            def wait_for_load_state(self, state: str) -> None:
                return None

        page = FakePage()
        uploader = MoodlePlaywrightUploader(
            course_url=page.course_url,
            section_title="Listen",
            username="alice",
            password="secret",
            headless=True,
        )

        uploader._login_with_schulportal_hessen_credentials(page)

        self.assertEqual(page.gotos, [])
        self.assertEqual(page.filled, {"username": "alice", "password": "secret"})

    def test_login_with_credentials_keeps_real_moodle_form_login(self) -> None:
        class FakeLocator:
            def __init__(self, page, name: str, present: bool = True) -> None:  # type: ignore[no-untyped-def]
                self.page = page
                self.name = name
                self.present = present
                self.first = self

            def count(self) -> int:
                return 1 if self.present else 0

            def nth(self, index: int):  # type: ignore[no-untyped-def]
                return self

            def is_visible(self) -> bool:
                return self.present

            def is_enabled(self) -> bool:
                return self.present

            def fill(self, value: str) -> None:
                self.page.filled[self.name] = value

            def click(self) -> None:
                self.page.clicks.append(self.name)
                self.page.logged_in = True

        class EmptyLocator:
            first = None

            def count(self) -> int:
                return 0

        class FakePage:
            def __init__(self) -> None:
                self.course_url = "https://moodle.example/course/view.php?id=5"
                self.url = self.course_url
                self.logged_in = False
                self.filled: dict[str, str] = {}
                self.clicks: list[str] = []
                self.gotos: list[str] = []

            def locator(self, selector: str):  # type: ignore[no-untyped-def]
                if selector == 'input[name="username"]':
                    return FakeLocator(self, "username", not self.logged_in)
                if selector == 'input[name="password"]':
                    return FakeLocator(self, "password", not self.logged_in)
                if selector == "#loginbtn":
                    return FakeLocator(self, "login-button", not self.logged_in)
                if selector == ".logininfo a[href*='login/index.php'], a[href*='login/index.php']":
                    return EmptyLocator()
                if selector == (
                    ".usermenu, [data-region='usermenu'], a[href*='login/logout.php'], "
                    ".logininfo a[href*='login/logout.php']"
                ):
                    return FakeLocator(self, "logout", self.logged_in)
                return EmptyLocator()

            def get_by_role(self, role: str, name):  # type: ignore[no-untyped-def]
                if role == "button":
                    return FakeLocator(self, "login-button")
                return EmptyLocator()

            def get_by_text(self, text):  # type: ignore[no-untyped-def]
                return EmptyLocator()

            def goto(self, url: str, **kwargs: object) -> None:
                self.gotos.append(url)
                self.url = url

            def wait_for_load_state(self, state: str) -> None:
                return None

        page = FakePage()
        uploader = MoodlePlaywrightUploader(
            course_url=page.course_url,
            section_title="Listen",
            username="alice",
            password="secret",
            headless=True,
        )

        uploader._login_with_credentials_if_needed(page)

        self.assertEqual(page.filled, {"username": "alice", "password": "secret"})
        self.assertEqual(page.clicks, ["login-button"])
        self.assertEqual(page.gotos, [page.course_url])

    def test_login_with_credentials_uses_visible_fields_and_login_button(self) -> None:
        class Candidate:
            def __init__(self, page, name: str, visible: bool = True) -> None:  # type: ignore[no-untyped-def]
                self.page = page
                self.name = name
                self.visible = visible

            def is_visible(self) -> bool:
                return self.visible

            def is_enabled(self) -> bool:
                return True

            def fill(self, value: str) -> None:
                if not self.visible:
                    raise AssertionError("hidden field must not be filled")
                self.page.filled[self.name] = value

            def click(self) -> None:
                self.page.clicks.append(self.name)
                self.page.logged_in = True

        class FakeLocator:
            def __init__(self, candidates: list[Candidate]) -> None:
                self.candidates = candidates
                self.first = candidates[0] if candidates else None

            def count(self) -> int:
                return len(self.candidates)

            def nth(self, index: int) -> Candidate:
                return self.candidates[index]

        class EmptyLocator:
            first = None

            def count(self) -> int:
                return 0

        class FakePage:
            def __init__(self) -> None:
                self.course_url = "https://www.opencoding.de/course/view.php?id=11"
                self.url = "https://www.opencoding.de/enrol/index.php?id=11"
                self.logged_in = False
                self.filled: dict[str, str] = {}
                self.clicks: list[str] = []
                self.gotos: list[str] = []

            def locator(self, selector: str):  # type: ignore[no-untyped-def]
                if selector == 'input[name="username"]':
                    if self.logged_in:
                        return EmptyLocator()
                    return FakeLocator([Candidate(self, "hidden-user", visible=False), Candidate(self, "visible-user")])
                if selector == 'input[name="password"]':
                    if self.logged_in:
                        return EmptyLocator()
                    return FakeLocator([Candidate(self, "hidden-password", visible=False), Candidate(self, "visible-password")])
                if selector == "#loginbtn":
                    return FakeLocator([Candidate(self, "login-button")])
                if selector == ".logininfo, .usermenu, [data-region='usermenu']":
                    return EmptyLocator()
                if selector == ".logininfo a[href*='login/index.php'], a[href*='login/index.php']":
                    return EmptyLocator()
                if selector == (
                    ".usermenu, [data-region='usermenu'], a[href*='login/logout.php'], "
                    ".logininfo a[href*='login/logout.php']"
                ):
                    return FakeLocator([Candidate(self, "logout")]) if self.logged_in else EmptyLocator()
                return EmptyLocator()

            def goto(self, url: str, **kwargs: object) -> None:
                self.gotos.append(url)
                self.url = url

            def wait_for_load_state(self, state: str) -> None:
                return None

        page = FakePage()
        uploader = MoodlePlaywrightUploader(
            course_url=page.course_url,
            section_title="Grundlagen",
            username="playwright",
            password="secret",
            headless=True,
        )

        uploader._login_with_credentials_if_needed(page)

        self.assertEqual(page.filled, {"visible-user": "playwright", "visible-password": "secret"})
        self.assertEqual(page.clicks, ["login-button"])

    def test_turn_editing_on_reports_external_sso_redirect_as_login(self) -> None:
        class EmptyLocator:
            first = None

            def count(self) -> int:
                return 0

            def get_attribute(self, name: str) -> str | None:
                return None

        class FakePage:
            url = "https://login.schulportal.hessen.de/saml/singleSignOn?SAMLRequest=test"

            def locator(self, selector: str):  # type: ignore[no-untyped-def]
                return EmptyLocator()

            def get_by_role(self, role: str, name):  # type: ignore[no-untyped-def]
                return EmptyLocator()

            def goto(self, url: str, **kwargs: object) -> None:
                self.url = "https://login.schulportal.hessen.de/saml/singleSignOn?SAMLRequest=test"

        uploader = MoodlePlaywrightUploader(
            course_url="https://mo5235.schulportal.hessen.de/course/view.php?id=7845",
            section_title="Listen",
            headless=True,
        )

        with self.assertRaisesRegex(RuntimeError, "Seite im Login.*singleSignOn"):
            uploader._turn_editing_on(FakePage())

    def test_external_login_page_detection_ignores_same_host_course_page(self) -> None:
        class EmptyLocator:
            def count(self) -> int:
                return 0

        class FakePage:
            url = "https://mo5235.schulportal.hessen.de/course/view.php?id=7845"

            def locator(self, selector: str):  # type: ignore[no-untyped-def]
                return EmptyLocator()

        uploader = MoodlePlaywrightUploader(
            course_url="https://mo5235.schulportal.hessen.de/course/view.php?id=7845",
            section_title="Listen",
        )

        self.assertFalse(uploader._login_is_required(FakePage()))

    def test_headless_login_if_needed_treats_guest_enrol_page_as_stale_storage(self) -> None:
        class EmptyLocator:
            def count(self) -> int:
                return 0

        class TextLocator:
            def count(self) -> int:
                return 1

            def nth(self, index: int):  # type: ignore[no-untyped-def]
                return self

            def inner_text(self, timeout: int = 0) -> str:
                return "Sie sind als Gast angemeldet Anmelden"

        class FakePage:
            url = "https://www.opencoding.de/enrol/index.php?id=11"

            def locator(self, selector: str):  # type: ignore[no-untyped-def]
                if selector in {".logininfo, .usermenu, [data-region='usermenu']"}:
                    return TextLocator()
                return EmptyLocator()

        uploader = MoodlePlaywrightUploader(
            course_url="https://www.opencoding.de/course/view.php?id=11",
            section_title="Grundlagen",
            headless=True,
        )

        with self.assertRaisesRegex(RuntimeError, "nur als Gast angemeldet.*Storage-State"):
            uploader._login_if_needed(FakePage())

    def test_find_or_create_section_aborts_when_rename_form_is_missing(self) -> None:
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

            def _course_is_in_edit_mode(self, page):  # type: ignore[no-untyped-def]
                return True

            def _section_by_number(self, page, section_number: int):  # type: ignore[no-untyped-def]
                return {"selector": "[data-section='9']", "number": section_number, "title": ""}

        class FakePage:
            def __init__(self) -> None:
                self.urls: list[str] = []

            def goto(self, url: str, **kwargs: object) -> None:
                self.urls.append(url)

        uploader = UploaderWithMissingRenameForm()
        page = FakePage()

        with self.assertRaisesRegex(RuntimeError, "konnte aber nicht in 'Texte' umbenannt werden"):
            uploader._find_or_create_section(page, [])

        self.assertTrue(uploader.created)

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

            def _course_is_in_edit_mode(self, page):  # type: ignore[no-untyped-def]
                return True

        class FakePage:
            def __init__(self) -> None:
                self.urls: list[str] = []

            def goto(self, url: str, **kwargs: object) -> None:
                self.urls.append(url)

        uploader = UploaderWithExistingPackageSection()

        section = uploader._find_or_create_section(FakePage(), [])

        self.assertEqual(uploader.renamed, [(26, "Texte")])
        self.assertEqual(section["title"], "Texte")

    def test_find_or_create_section_keeps_explicit_target_section_even_if_packages_exist_elsewhere(self) -> None:
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

            def _course_is_in_edit_mode(self, page):  # type: ignore[no-untyped-def]
                return True

        class FakePage:
            def goto(self, url: str, **kwargs: object) -> None:
                return None

        uploader = UploaderWithDuplicateTitleSection()

        section = uploader._find_or_create_section(
            FakePage(),
            [MoodleH5PUploadPackage("strings-grundlagen", "Strings: Grundlagen", Path("a.h5p"))],
        )

        self.assertEqual(section["number"], 10)
        self.assertEqual(uploader.renamed, [])

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

    def test_resolve_existing_activity_id_can_reuse_coursewide_id_outside_target_section(self) -> None:
        uploader = MoodlePlaywrightUploader(
            course_url="https://example.invalid/course/view.php?id=5",
            section_title="Grundlagen",
            existing_activity_ids={"bewegung": 44},
        )

        activity_id = uploader._resolve_existing_activity_id(
            MoodleH5PUploadPackage("bewegung", "Bewegung", Path("bewegung.h5p")),
            {"installation": 11},
            require_section_match=True,
        )

        self.assertIsNone(activity_id)

        activity_id = uploader._resolve_existing_activity_id(
            MoodleH5PUploadPackage("bewegung", "Bewegung", Path("bewegung.h5p")),
            {"installation": 11},
            require_section_match=False,
        )

        self.assertEqual(activity_id, 44)

    def test_resolve_existing_activity_id_keeps_coursewide_id_inside_target_section(self) -> None:
        uploader = MoodlePlaywrightUploader(
            course_url="https://example.invalid/course/view.php?id=5",
            section_title="Grundlagen",
            existing_activity_ids={"bewegung": 44},
        )

        activity_id = uploader._resolve_existing_activity_id(
            MoodleH5PUploadPackage("bewegung", "Bewegung", Path("bewegung.h5p")),
            {"alter-titel": 44},
            require_section_match=True,
        )

        self.assertEqual(activity_id, 44)

    def test_set_activity_section_updates_hidden_section_field(self) -> None:
        class FakeLocator:
            def __init__(self, present: bool) -> None:
                self.present = present
                self.first = self
                self.evaluated_values: list[str | None] = []

            def count(self) -> int:
                return 1 if self.present else 0

            def select_option(self, *, value: str) -> None:
                raise AssertionError("hidden input must be used in this test")

            def evaluate(self, script: str, value: str | None = None) -> None:
                self.evaluated_values.append(value)

        class FakePage:
            def __init__(self) -> None:
                self.section_input = FakeLocator(True)

            def locator(self, selector: str):  # type: ignore[no-untyped-def]
                if selector.startswith("select"):
                    return FakeLocator(False)
                if selector.startswith("input"):
                    return self.section_input
                return FakeLocator(False)

        page = FakePage()
        uploader = MoodlePlaywrightUploader(course_url="https://example.invalid/course/view.php?id=5")

        uploader._set_activity_section(page, 2)

        self.assertEqual(page.section_input.evaluated_values, ["2"])

    def test_move_activity_to_section_number_uses_moodle_movetosection(self) -> None:
        class MoveUploader(MoodlePlaywrightUploader):
            def __init__(self) -> None:
                super().__init__(course_url="https://example.invalid/course/view.php?id=5")

            def _turn_editing_on(self, page):  # type: ignore[no-untyped-def]
                return None

            def _section_by_number(self, page, section_number: int):  # type: ignore[no-untyped-def]
                return {"selector": "[data-section='1']", "number": section_number, "title": "Grundlagen", "sectionDbId": "143"}

            def _moodle_sesskey(self, page):  # type: ignore[no-untyped-def]
                return "abc123"

        class FakePage:
            def __init__(self) -> None:
                self.urls: list[str] = []

            def goto(self, url: str, **kwargs: object) -> None:
                self.urls.append(url)

        page = FakePage()
        uploader = MoveUploader()

        uploader._move_activity_to_section_number(page, 412, 1)

        self.assertEqual(
            page.urls,
            [
                "https://example.invalid/course/view.php?id=5",
                "https://example.invalid/course/mod.php?sesskey=abc123&copy=412",
                "https://example.invalid/course/mod.php?movetosection=143&sesskey=abc123",
                "https://example.invalid/course/view.php?id=5",
            ],
        )

    def test_upload_packages_reloads_section_after_invalid_existing_activity_id(self) -> None:
        class UploadUploader(MoodlePlaywrightUploader):
            def __init__(self) -> None:
                super().__init__(
                    course_url="https://example.invalid/course/view.php?id=5",
                    section_title="Weitere Tutorials",
                    existing_activity_ids={"animationen": 419},
                )
                self.section_calls = 0
                self.created = False

            def _find_or_create_section(self, page, packages):  # type: ignore[no-untyped-def]
                self.section_calls += 1
                return {"selector": f"[data-section='{self.section_calls}']", "number": 5, "title": "Weitere Tutorials", "sectionDbId": "148"}

            def _collect_section_h5p_activities(self, page, section_selector: str):  # type: ignore[no-untyped-def]
                return {}

            def _update_h5p_activity(self, page, activity_id: int, package, *, section_number=None):  # type: ignore[no-untyped-def]
                return None

            def _create_h5p_activity(self, page, section_number: int, package):  # type: ignore[no-untyped-def]
                self.created = True
                return MoodleH5PUploadResult(package.identifier, package.title, "created", 777)

            def _sort_section_h5p_activities(self, page, section_number: int, packages):  # type: ignore[no-untyped-def]
                return None

            def _turn_editing_on(self, page):  # type: ignore[no-untyped-def]
                return None

        class FakePage:
            def __init__(self) -> None:
                self.gotos: list[str] = []

            def goto(self, url: str, **kwargs: object) -> None:
                self.gotos.append(url)

        uploader = UploadUploader()
        page = FakePage()

        # Exercise the inner package loop without launching Playwright.
        section = None
        known_activities: dict[str, int] = {}
        packages = [MoodleH5PUploadPackage("animationen", "animationen", Path("animationen.h5p"))]

        def ensure_section():  # type: ignore[no-untyped-def]
            nonlocal section, known_activities
            if section is not None:
                return section
            section = uploader._find_or_create_section(page, packages)
            known_activities = uploader._collect_section_h5p_activities(page, section["selector"])
            return section

        package = packages[0]
        target_section = ensure_section()
        activity_id = uploader._resolve_existing_activity_id(package, known_activities, require_section_match=False)
        result = uploader._update_h5p_activity(page, activity_id, package, section_number=int(target_section["number"]))
        self.assertIsNone(result)
        section = None
        known_activities = {}
        uploader.existing_activity_ids.pop(package.identifier, None)
        page.goto(uploader.course_url, wait_until="domcontentloaded")
        uploader._turn_editing_on(page)
        target_section = ensure_section()
        created = uploader._create_h5p_activity(page, int(target_section["number"]), package)

        self.assertEqual(uploader.section_calls, 2)
        self.assertTrue(uploader.created)
        self.assertEqual(created.activity_id, 777)

    def test_prune_extra_course_sections_deletes_only_non_desired_h5p_sections(self) -> None:
        class CleanupUploader(MoodlePlaywrightUploader):
            def __init__(self) -> None:
                super().__init__(course_url="https://example.invalid/course/view.php?id=5")
                self.sections = [
                    {"number": 0, "title": "Allgemeines", "modules": [{"modname": "forum", "name": "Ankündigungen"}]},
                    {"number": 1, "title": "Grundlagen", "modules": [{"modname": "h5pactivity", "name": "intro"}]},
                    {"number": 2, "title": "Alt", "modules": [{"modname": "h5pactivity", "name": "old"}]},
                ]
                self.deleted: list[int] = []

            def _course_sections_snapshot(self, page):  # type: ignore[no-untyped-def]
                return list(self.sections)

            def _delete_section(self, page, section):  # type: ignore[no-untyped-def]
                self.deleted.append(int(section["number"]))
                self.sections = [item for item in self.sections if item["number"] != section["number"]]
                return True

            def _turn_editing_on(self, page):  # type: ignore[no-untyped-def]
                return None

        class FakePage:
            def goto(self, url: str, **kwargs: object) -> None:
                return None

        uploader = CleanupUploader()

        results = uploader._prune_extra_course_sections(FakePage(), ["Grundlagen"])

        self.assertEqual(uploader.deleted, [2])
        self.assertEqual([(result.section_number, result.title, result.action) for result in results], [(2, "Alt", "deleted")])

    def test_prune_extra_course_sections_aborts_for_non_h5p_modules(self) -> None:
        class CleanupUploader(MoodlePlaywrightUploader):
            def __init__(self) -> None:
                super().__init__(course_url="https://example.invalid/course/view.php?id=5")

            def _course_sections_snapshot(self, page):  # type: ignore[no-untyped-def]
                return [
                    {"number": 1, "title": "Grundlagen", "modules": []},
                    {"number": 2, "title": "Alt", "modules": [{"modname": "page", "name": "Nicht loeschen"}]},
                ]

            def _turn_editing_on(self, page):  # type: ignore[no-untyped-def]
                return None

        class FakePage:
            def goto(self, url: str, **kwargs: object) -> None:
                return None

        uploader = CleanupUploader()

        with self.assertRaisesRegex(RuntimeError, "Nicht-H5P-Inhalte: Nicht loeschen"):
            uploader._prune_extra_course_sections(FakePage(), ["Grundlagen"])

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

    def test_create_section_by_url_prefers_existing_add_section_link(self) -> None:
        class FakeLocator:
            def __init__(self, hrefs: list[str]) -> None:
                self._hrefs = hrefs
                self.first = self

            def nth(self, index: int):
                return FakeLocator([self._hrefs[index]])

            def count(self) -> int:
                return len(self._hrefs)

            def get_attribute(self, name: str) -> str | None:
                if name == "href" and self._hrefs:
                    return self._hrefs[0]
                return None

        class EmptyLocator:
            first = None

            def count(self) -> int:
                return 0

        class FakePage:
            def __init__(self) -> None:
                self.urls: list[str] = []
                self.section_number = 5

            def goto(self, url: str, **kwargs: object) -> None:
                self.urls.append(url)
                if "insertsection=0" in url:
                    self.section_number = 6

            def locator(self, selector: str):
                if selector == 'a[data-action="addSection"][href*="/course/changenumsections.php"]':
                    return FakeLocator(
                        [
                            "/course/changenumsections.php?courseid=7845&insertsection=17&sesskey=test",
                            "/course/changenumsections.php?courseid=7845&insertsection=0&sesskey=test",
                        ]
                    )
                return EmptyLocator()

        class UrlUploader(MoodlePlaywrightUploader):
            def __init__(self) -> None:
                super().__init__(course_url="https://example.invalid/course/view.php?id=7845", section_title="Funktionen")

            def _course_is_in_edit_mode(self, page):  # type: ignore[no-untyped-def]
                return True

            def _turn_editing_on(self, page):  # type: ignore[no-untyped-def]
                return None

            def _last_section_number(self, page):  # type: ignore[no-untyped-def]
                return page.section_number

            def _moodle_sesskey(self, page):  # type: ignore[no-untyped-def]
                return "test"

        page = FakePage()
        uploader = UrlUploader()

        created = uploader._create_section_by_url(page, 5)

        self.assertTrue(created)
        self.assertEqual(
            page.urls,
            [
                "https://example.invalid/course/changenumsections.php?courseid=7845&insertsection=0&sesskey=test",
                "https://example.invalid/course/view.php?id=7845",
            ],
        )

    def test_is_top_level_add_section_href_accepts_only_course_end_insert(self) -> None:
        uploader = MoodlePlaywrightUploader(
            course_url="https://example.invalid/course/view.php?id=7845",
            section_title="Funktionen",
        )

        self.assertTrue(
            uploader._is_top_level_add_section_href(
                "https://example.invalid/course/changenumsections.php?courseid=7845&insertsection=0&sesskey=test"
            )
        )
        self.assertFalse(
            uploader._is_top_level_add_section_href(
                "https://example.invalid/course/changenumsections.php?courseid=7845&insertsection=17&sesskey=test"
            )
        )

    def test_ensure_section_title_raises_if_rename_did_not_change_visible_title(self) -> None:
        class RenameMismatchUploader(MoodlePlaywrightUploader):
            def __init__(self) -> None:
                super().__init__(course_url="https://example.invalid/course/view.php?id=5", section_title="Texte")

            def _rename_section(self, page, section_number: int, title: str) -> bool:  # type: ignore[no-untyped-def]
                return True

            def _turn_editing_on(self, page):  # type: ignore[no-untyped-def]
                return None

            def _section_by_number(self, page, section_number: int):  # type: ignore[no-untyped-def]
                return {"selector": "[data-section='7']", "number": section_number, "title": "Alter Titel"}

        class FakePage:
            def goto(self, url: str, **kwargs: object) -> None:
                return None

        uploader = RenameMismatchUploader()

        with self.assertRaisesRegex(RuntimeError, "traegt aber nicht den erwarteten Titel"):
            uploader._ensure_section_title(FakePage(), {"selector": "[data-section='7']", "number": 7, "title": "Alt"}, "Texte")


if __name__ == "__main__":
    unittest.main()
