from __future__ import annotations

import json
import os
import re
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urljoin, urlparse


@dataclass(slots=True)
class MoodleH5PUploadPackage:
    identifier: str
    title: str
    path: Path
    points: int = 2


@dataclass(slots=True)
class MoodleH5PUploadResult:
    identifier: str
    title: str
    action: str
    activity_id: int | None = None


def normalize_moodle_identifier(value: str) -> str:
    normalized = value.strip().lower()
    for source, target in {
        "ä": "ae",
        "ö": "oe",
        "ü": "ue",
        "ß": "ss",
    }.items():
        normalized = normalized.replace(source, target)
    normalized = unicodedata.normalize("NFKD", normalized)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_value).strip("-")
    return re.sub(r"-+", "-", slug)


def normalize_moodle_section_title(value: str) -> str:
    normalized = value.strip().lower()
    for source, target in {
        "ä": "ae",
        "ö": "oe",
        "ü": "ue",
        "ß": "ss",
    }.items():
        normalized = normalized.replace(source, target)
    normalized = unicodedata.normalize("NFKD", normalized)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    ascii_value = re.sub(r"\bund\b|\band\b", " ", ascii_value)
    ascii_value = re.sub(r"[^a-z0-9]+", " ", ascii_value)
    return re.sub(r"\s+", " ", ascii_value).strip()


def read_h5p_package_title(package_path: Path) -> str:
    with package_path.open("rb") as archive_file:
        import zipfile

        with zipfile.ZipFile(archive_file) as archive:
            metadata = json.loads(archive.read("h5p.json").decode("utf-8"))
    title = str(metadata.get("title") or package_path.stem).strip()
    return title or package_path.stem


def infer_h5p_package_points(package_path: Path) -> int:
    """Infer Moodle grade points for one H5P package.

    Rule: graded questions get 2 points, non-graded questions get 0 points.
    """
    try:
        with package_path.open("rb") as archive_file:
            import zipfile

            with zipfile.ZipFile(archive_file) as archive:
                metadata = json.loads(archive.read("h5p.json").decode("utf-8"))
                content = json.loads(archive.read("content/content.json").decode("utf-8"))
    except Exception:
        # Fallback for unusual packages: keep graded default.
        return 2

    main_library = str(metadata.get("mainLibrary") or "").strip()
    grading_settings = content.get("gradingSettings")
    grading_method = ""
    if isinstance(grading_settings, dict):
        grading_method = str(grading_settings.get("gradingMethod") or "").strip()

    if main_library == "H5P.PythonQuestion":
        if grading_method in {"", "please_choose"}:
            return 0
        return 2

    if grading_method and grading_method != "please_choose":
        return 2
    if isinstance(grading_settings, dict) and "gradingMethod" in grading_settings:
        return 0
    return 2


def read_chapter_question_order(course_dir: Path, chapter: str) -> list[str]:
    chapter_slug = chapter.strip().strip("/")
    chapter_path = course_dir / "chapters" / f"{chapter_slug}.mdx"
    if not chapter_path.is_file():
        return []

    source = chapter_path.read_text(encoding="utf-8")
    identifiers = re.findall(
        r"<PythonQuestion\b[^>]*?\bidentifier\s*=\s*['\"]([^'\"]+)['\"]",
        source,
        flags=re.DOTALL,
    )
    return [identifier.strip() for identifier in identifiers if identifier.strip()]


def collect_h5p_upload_packages(course_dir: Path, chapter: str) -> list[MoodleH5PUploadPackage]:
    chapter_slug = chapter.strip().strip("/")
    if not chapter_slug:
        raise ValueError("Kapitel-Slug fehlt.")

    source_dir = course_dir / "build" / "h5p" / chapter_slug
    if not source_dir.is_dir():
        raise FileNotFoundError(f"Build-Ordner fuer Kapitel '{chapter_slug}' wurde nicht gefunden: {source_dir}")

    packages_by_identifier = {
        package_path.stem: MoodleH5PUploadPackage(
            identifier=package_path.stem,
            title=read_h5p_package_title(package_path),
            path=package_path,
            points=infer_h5p_package_points(package_path),
        )
        for package_path in source_dir.glob("*.h5p")
    }
    ordered_identifiers = read_chapter_question_order(course_dir, chapter_slug)
    if ordered_identifiers:
        packages = [
            packages_by_identifier[identifier]
            for identifier in ordered_identifiers
            if identifier in packages_by_identifier
        ]
    else:
        packages = [packages_by_identifier[identifier] for identifier in sorted(packages_by_identifier)]
    if not packages:
        raise FileNotFoundError(f"Keine H5P-Pakete fuer Kapitel '{chapter_slug}' gefunden.")
    return packages


class MoodlePlaywrightUploader:
    def __init__(
        self,
        *,
        course_url: str,
        section_title: str | None = None,
        username: str | None = None,
        password: str | None = None,
        storage_state: Path | None = None,
        existing_activity_ids: dict[str, int] | None = None,
        headless: bool = False,
        timeout_ms: int = 30_000,
    ) -> None:
        self.course_url = course_url
        self.section_title = section_title.strip() if section_title else ""
        self.username = username
        self.password = password
        self.storage_state = storage_state
        self.existing_activity_ids = existing_activity_ids or {}
        self.headless = headless
        self.timeout_ms = timeout_ms

    def upload_packages(self, packages: list[MoodleH5PUploadPackage]) -> list[MoodleH5PUploadResult]:
        try:
            from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
            from playwright.sync_api import sync_playwright
        except ModuleNotFoundError as error:
            raise RuntimeError(
                "Playwright ist nicht installiert. Installiere zuerst: "
                "python -m pip install -r requirements.txt && python -m playwright install chromium"
            ) from error

        results: list[MoodleH5PUploadResult] = []
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=self.headless)
            context_kwargs: dict[str, Any] = {}
            if self.storage_state and self.storage_state.exists():
                context_kwargs["storage_state"] = str(self.storage_state)
            context = browser.new_context(**context_kwargs)
            page = context.new_page()
            page.set_default_timeout(self.timeout_ms)
            current_package: MoodleH5PUploadPackage | None = None
            current_step = "Moodle-Kurs oeffnen"

            try:
                print(f"Oeffne Moodle-Kurs: {self.course_url}", flush=True)
                page.goto(self.course_url, wait_until="domcontentloaded")
                current_step = "Login pruefen"
                self._login_if_needed(page)
                self._wait_for_manual_login_confirmation(page)
                self._return_to_course_after_login(page)
                self._wait_for_editing_controls_after_manual_login(page)
                print("Schalte Moodle-Bearbeitungsmodus ein.", flush=True)
                current_step = "Bearbeitungsmodus einschalten"
                self._turn_editing_on(page)
                section: dict[str, str | int] | None = None
                known_activities: dict[str, int] = {}

                def ensure_section() -> dict[str, str | int]:
                    nonlocal section, known_activities, current_step
                    if section is not None:
                        return section
                    current_step = "Moodle-Section suchen"
                    print(f"Suche Moodle-Section: {self.section_title or '(erste gefundene Section)'}", flush=True)
                    section = self._find_or_create_section(page, packages)
                    print(f"Verwende Moodle-Section {section['number']}. Sammle vorhandene H5P-Aktivitaeten.", flush=True)
                    current_step = f"H5P-Aktivitaeten in Section {section['number']} sammeln"
                    known_activities = self._collect_section_h5p_activities(page, section["selector"])
                    return section

                for package in packages:
                    current_package = package
                    current_step = f"H5P-Paket verarbeiten: {package.identifier}"
                    print(f"Verarbeite H5P-Paket: {package.identifier} ({package.title})", flush=True)
                    activity_id = self.existing_activity_ids.get(package.identifier)
                    if activity_id is None:
                        ensure_section()
                        activity_id = known_activities.get(package.identifier)
                    if activity_id is None:
                        ensure_section()
                        activity_id = known_activities.get(normalize_moodle_identifier(package.title))

                    if activity_id is not None:
                        current_step = f"H5P-Aktivitaet aktualisieren: {package.identifier}"
                        print(f"Aktualisiere H5P-Aktivitaet {activity_id}: {package.title}", flush=True)
                        result = self._update_h5p_activity(page, activity_id, package)
                        if result is not None:
                            results.append(result)
                            continue
                        print(
                            f"Warnung: H5P-Aktivitaet {activity_id} konnte nicht geoeffnet werden. "
                            "Lege sie neu an.",
                            flush=True,
                        )

                    target_section = ensure_section()
                    section_number = int(target_section["number"])
                    refreshed_activity_id = known_activities.get(package.identifier) or known_activities.get(
                        normalize_moodle_identifier(package.title)
                    )
                    if refreshed_activity_id is not None:
                        current_step = f"H5P-Aktivitaet aktualisieren: {package.identifier}"
                        print(f"Aktualisiere H5P-Aktivitaet {refreshed_activity_id}: {package.title}", flush=True)
                        result = self._update_h5p_activity(page, refreshed_activity_id, package)
                        if result is not None:
                            results.append(result)
                            continue

                    print(f"Lege neue H5P-Aktivitaet an: {package.title}", flush=True)
                    current_step = f"H5P-Aktivitaet anlegen: {package.identifier}"
                    result = self._create_h5p_activity(page, section_number, package)
                    results.append(result)

                if packages:
                    current_package = None
                    current_step = "H5P-Aktivitaeten sortieren"
                    target_section = ensure_section()
                    self._sort_section_h5p_activities(page, int(target_section["number"]), packages)

                if self.storage_state:
                    current_step = "Moodle-Login-Status speichern"
                    print(f"Speichere Moodle-Login-Status: {self.storage_state}", flush=True)
                    self.storage_state.parent.mkdir(parents=True, exist_ok=True)
                    context.storage_state(path=str(self.storage_state))
            except PlaywrightTimeoutError as error:
                debug_label = normalize_moodle_identifier(current_step)
                self._write_debug_artifacts(page, f"timeout-{debug_label}")
                raise RuntimeError(
                    self._format_timeout_error(error, current_step=current_step, package=current_package)
                ) from error
            finally:
                context.close()
                browser.close()

        return results

    def _login_if_needed(self, page: Any) -> None:
        if self.username and self.password:
            self._login_with_credentials_if_needed(page)
            return

        if not self._login_is_required(page):
            return

        if self.headless:
            raise RuntimeError(
                "Moodle verlangt Login (wahrscheinlich abgelaufener SSO-Storage-State). "
                "Bitte den Storage-State im headed Browser neu erzeugen und den Upload erneut starten. "
                f"Aktuelle URL: {self._current_page_url(page) or 'unbekannt'}"
            )

        print("Moodle verlangt Login. Bitte im Browser anmelden.")
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            if not self._login_is_required(page):
                return
            page.wait_for_timeout(1000)
        raise RuntimeError("Login wurde nicht innerhalb von 5 Minuten abgeschlossen.")

    def _login_with_credentials_if_needed(self, page: Any) -> None:
        if self._looks_logged_in(page):
            return

        if self._schulportal_hessen_login_page_is_open(page):
            self._login_with_schulportal_hessen_credentials(page)
            self._finish_credential_login(page)
            return

        login_url = urljoin(self.course_url, "/login/index.php")
        print("Melde mit hinterlegten Moodle-Zugangsdaten an.", flush=True)
        if page.locator('input[name="username"]').count() == 0:
            page.goto(login_url, wait_until="domcontentloaded")

        if self._schulportal_hessen_login_page_is_open(page):
            self._login_with_schulportal_hessen_credentials(page)
            self._finish_credential_login(page)
            return

        username_input = page.locator('input[name="username"]')
        password_input = page.locator('input[name="password"]')
        if username_input.count() == 0 or password_input.count() == 0:
            if self._external_login_page_is_open(page):
                raise RuntimeError(
                    "Moodle hat auf einen externen SSO-Login umgeleitet. "
                    "Die hinterlegten MOODLE_USERNAME/MOODLE_PASSWORD koennen dort nicht automatisch verwendet werden. "
                    "Bitte den Storage-State im headed Browser neu erzeugen und den Upload erneut starten. "
                    f"Aktuelle URL: {self._current_page_url(page) or 'unbekannt'}"
                )
            raise RuntimeError("Moodle-Loginformular wurde nicht gefunden, obwohl Zugangsdaten gesetzt sind.")

        username_input.first.fill(self.username or "")
        password_input.first.fill(self.password or "")
        self._click_first_by_text(page, ["Log in", "Login", "Einloggen", "Anmelden"])
        page.wait_for_load_state("domcontentloaded")

        if page.locator('input[name="username"]').count() > 0:
            raise RuntimeError("Moodle-Login mit den hinterlegten Zugangsdaten ist fehlgeschlagen.")

        self._finish_credential_login(page)

    def _finish_credential_login(self, page: Any) -> None:
        page.goto(self.course_url, wait_until="domcontentloaded")
        if self._login_is_required(page):
            raise RuntimeError(
                "Moodle verlangt nach dem Login erneut Zugangsdaten. "
                "Bitte pruefe Benutzername/Passwort oder erneuere den SSO-Login im sichtbaren Browser. "
                f"Aktuelle URL: {self._current_page_url(page) or 'unbekannt'}"
            )

    def _login_with_schulportal_hessen_credentials(self, page: Any) -> None:
        print("Melde ueber Schulportal Hessen SSO an.", flush=True)
        schulportal_login_url = self._schulportal_hessen_login_url()
        if schulportal_login_url and not self._current_schulportal_login_url_has_instance(page):
            page.goto(schulportal_login_url, wait_until="domcontentloaded")

        username_input = self._first_visible_enabled_locator(
            page,
            [
                'input[name="username"]',
                'input[name="user"]',
                'input[name="login"]',
                'input[name="email"]',
                'input[name="kennung"]',
                'input[id*="username" i]',
                'input[id*="login" i]',
                'input[id*="email" i]',
                'input[type="email"]',
                'input[type="text"]',
            ],
        )
        if username_input is None:
            raise RuntimeError(
                "Schulportal-Hessen-Loginfeld wurde nicht gefunden. "
                f"Aktuelle URL: {self._current_page_url(page) or 'unbekannt'}"
            )
        username_input.fill(self.username or "")

        password_input = self._first_visible_enabled_locator(page, ['input[name="password"]', 'input[type="password"]'])
        if password_input is None:
            self._click_first_by_text(page, ["Weiter", "Next", "Fortfahren"])
            page.wait_for_load_state("domcontentloaded")
            password_input = self._first_visible_enabled_locator(page, ['input[name="password"]', 'input[type="password"]'])
        if password_input is None:
            raise RuntimeError(
                "Schulportal-Hessen-Passwortfeld wurde nicht gefunden. "
                f"Aktuelle URL: {self._current_page_url(page) or 'unbekannt'}"
            )
        password_input.fill(self.password or "")

        submit = self._first_existing_locator(
            page,
            [
                'button[type="submit"]',
                'input[type="submit"]',
                'button[name="submit"]',
                'button[id*="login" i]',
            ],
        )
        if submit is not None:
            submit.click()
        else:
            self._click_first_by_text(page, ["Anmelden", "Einloggen", "Login", "Log in", "Weiter"])
        page.wait_for_load_state("domcontentloaded")
        self._continue_schulportal_hessen_login(page)

    def _continue_schulportal_hessen_login(self, page: Any) -> None:
        for _ in range(3):
            if not self._schulportal_hessen_login_page_is_open(page):
                return
            try:
                self._click_first_by_text(page, ["Weiter", "Fortfahren", "Zustimmen", "Akzeptieren", "Erlauben"])
            except RuntimeError:
                return
            page.wait_for_load_state("domcontentloaded")

    def _first_existing_locator(self, page: Any, selectors: list[str]) -> Any | None:
        for selector in selectors:
            locator = page.locator(selector)
            if locator.count() > 0:
                return locator.first
        return None

    def _first_visible_enabled_locator(self, page: Any, selectors: list[str]) -> Any | None:
        for selector in selectors:
            locator = page.locator(selector)
            for index in range(locator.count()):
                candidate = locator.nth(index)
                try:
                    if not candidate.is_visible() or not candidate.is_enabled():
                        continue
                except Exception:
                    continue
                return candidate
        return None

    def _return_to_course_after_login(self, page: Any) -> None:
        if page.url == self.course_url and self._looks_logged_in(page):
            return
        page.goto(self.course_url, wait_until="domcontentloaded")
        if self.username and self.password and self._login_is_required(page):
            raise RuntimeError(
                "Moodle verlangt nach dem Login erneut Zugangsdaten auf der Kursseite. "
                f"Aktuelle URL: {self._current_page_url(page) or 'unbekannt'}"
            )
        if self.headless and not self.username and not self.password and self._login_is_required(page):
            raise RuntimeError(
                "Moodle verlangt Login nach dem Wechsel auf die Kursseite "
                "(wahrscheinlich abgelaufener SSO-Storage-State). "
                "Bitte den Storage-State im headed Browser neu erzeugen und den Upload erneut starten. "
                f"Aktuelle URL: {self._current_page_url(page) or 'unbekannt'}"
            )

    def _looks_logged_in(self, page: Any) -> bool:
        if page.locator('input[name="username"]').count() > 0:
            return False
        if page.locator(".logininfo a[href*='login/index.php'], a[href*='login/index.php']").count() > 0:
            return False
        return page.locator(
            ".usermenu, [data-region='usermenu'], a[href*='login/logout.php'], "
            ".logininfo a[href*='login/logout.php']"
        ).count() > 0

    def _login_is_required(self, page: Any) -> bool:
        return page.locator('input[name="username"]').count() > 0 or self._external_login_page_is_open(page)

    def _external_login_page_is_open(self, page: Any) -> bool:
        current_url = self._current_page_url(page)
        if not current_url:
            return False
        current = urlparse(current_url)
        course = urlparse(self.course_url)
        if not current.netloc or current.netloc == course.netloc:
            return False
        auth_markers = ("login", "saml", "sso", "singleSignOn", "oauth", "openid", "idp", "auth")
        haystack = f"{current.netloc}{current.path}".lower()
        return any(marker.lower() in haystack for marker in auth_markers)

    def _schulportal_hessen_login_page_is_open(self, page: Any) -> bool:
        current_url = self._current_page_url(page)
        if not current_url:
            return False
        host = urlparse(current_url).netloc.lower()
        return host == "login.schulportal.hessen.de"

    def _current_schulportal_login_url_has_instance(self, page: Any) -> bool:
        current_url = self._current_page_url(page)
        if not current_url:
            return False
        parsed = urlparse(current_url)
        if parsed.netloc.lower() != "login.schulportal.hessen.de":
            return False
        return bool(parse_qs(parsed.query).get("i", [""])[0])

    def _schulportal_hessen_login_url(self) -> str:
        course_host = urlparse(self.course_url).netloc.lower()
        match = re.search(r"\bmo(\d+)\.", course_host)
        if not match:
            return ""
        return f"https://login.schulportal.hessen.de/?i={match.group(1)}"

    def _current_page_url(self, page: Any) -> str:
        try:
            return str(page.url)
        except Exception:
            return ""

    def _wait_for_manual_login_confirmation(self, page: Any) -> None:
        if self.username and self.password:
            return
        if self.headless:
            return

        input(
            "Bitte im Browser anmelden. Wenn du wieder auf der Moodle-Kursseite bist, "
            "druecke hier Enter, dann startet der Upload: "
        )
        page.goto(self.course_url, wait_until="domcontentloaded")

    def _turn_editing_on(self, page: Any) -> None:
        if self._course_is_in_edit_mode(page):
            return

        if self.headless and not self.username and not self.password and self._login_is_required(page):
            raise RuntimeError(
                "Konnte den Moodle-Bearbeitungsmodus nicht einschalten, weil die Seite im Login steht "
                "(wahrscheinlich abgelaufener SSO-Storage-State). "
                "Bitte den Storage-State im headed Browser neu erzeugen und den Upload erneut starten. "
                f"Aktuelle URL: {self._current_page_url(page) or 'unbekannt'}"
            )

        if self._click_editing_switch(page):
            return

        for label in self._editing_toggle_labels():
            switch = page.get_by_role("switch", name=re.compile(label, re.IGNORECASE))
            if switch.count() > 0:
                switch.first.click()
                page.wait_for_load_state("domcontentloaded")
                if self._course_is_in_edit_mode(page):
                    return
            locator = page.get_by_role("button", name=re.compile(label, re.IGNORECASE))
            if locator.count() > 0:
                locator.first.click()
                page.wait_for_load_state("domcontentloaded")
                if self._course_is_in_edit_mode(page):
                    return
            link = page.get_by_role("link", name=re.compile(label, re.IGNORECASE))
            if link.count() > 0:
                link.first.click()
                page.wait_for_load_state("domcontentloaded")
                if self._course_is_in_edit_mode(page):
                    return

        if self._turn_editing_on_by_url(page):
            return

        if not self.headless:
            print(
                "Der Bearbeitungsmodus-Schalter wurde nicht automatisch erkannt. "
                "Bitte im Browser den Bearbeitungsmodus einschalten und danach hier Enter druecken.",
                flush=True,
            )
            input("Enter druecken, sobald der Bearbeitungsmodus aktiv ist: ")
            page.goto(self.course_url, wait_until="domcontentloaded")
            if self._course_is_in_edit_mode(page):
                return

        current_url = self._current_page_url(page)
        if self.headless and not self.username and not self.password and self._login_is_required(page):
            raise RuntimeError(
                "Konnte den Moodle-Bearbeitungsmodus nicht einschalten, weil die Seite im Login steht "
                "(wahrscheinlich abgelaufener SSO-Storage-State). "
                "Bitte den Storage-State im headed Browser neu erzeugen und den Upload erneut starten. "
                f"Aktuelle URL: {current_url or 'unbekannt'}"
            )

        raise RuntimeError(
            "Konnte den Moodle-Bearbeitungsmodus nicht einschalten. "
            f"Aktuelle URL: {current_url or 'unbekannt'}\n"
            "Hinweis: Stellen Sie sicher, dass der Playwright-Nutzer (fuer Browser-Uploads) "
            "UND der Webservice-Nutzer (fuer API-Zugriffe) Zugriff auf den Kurs haben. "
            "Es reicht nicht, nur einen der beiden einzuschreiben."
        )

    def _click_editing_switch(self, page: Any) -> bool:
        selectors = [
            '.editmode-switch-form input[type="checkbox"]',
            'input[name="setmode"]',
            '[data-action="toggle-editing"]',
            'a[href*="setmode=1"]',
            'a[href*="edit=on"]',
        ]
        for selector in selectors:
            locator = page.locator(selector)
            if locator.count() == 0:
                continue
            first = locator.first
            try:
                if selector.startswith("input") or "input" in selector:
                    if first.is_checked():
                        return self._course_is_in_edit_mode(page)
                first.click()
                page.wait_for_load_state("domcontentloaded")
            except Exception:
                continue
            if self._course_is_in_edit_mode(page):
                return True
        return False

    def _turn_editing_on_by_url(self, page: Any) -> bool:
        sesskey = self._moodle_sesskey(page)
        url_variants: list[dict[str, str]] = []
        if sesskey:
            url_variants.extend(
                [
                    {"sesskey": sesskey, "edit": "on"},
                    {"sesskey": sesskey, "setmode": "1"},
                ]
            )
        # Some Moodle setups accept edit toggles without an exposed sesskey.
        url_variants.extend(
            [
                {"edit": "on"},
                {"setmode": "1"},
            ]
        )

        for extra in url_variants:
            parsed = urlparse(self.course_url)
            query = parse_qs(parsed.query)
            for key, value in extra.items():
                query[key] = [value]
            target = parsed._replace(query=urlencode(query, doseq=True)).geturl()
            for _ in range(2):
                try:
                    page.goto(target, wait_until="domcontentloaded")
                except Exception:
                    page.wait_for_timeout(500)
                    continue
                if self._course_is_in_edit_mode(page):
                    return True
                break
        return False

    def _wait_for_editing_controls_after_manual_login(self, page: Any) -> None:
        if self.username and self.password:
            return
        if self.headless:
            return
        if self._course_is_in_edit_mode(page) or self._editing_toggle_is_visible(page):
            return

        print(
            "Bitte im Browser anmelden und zur Kursseite zurueckkehren. "
            "Das Tool wartet, bis der Bearbeitungsmodus verfuegbar ist."
            " Falls hier nichts weiter passiert, pruefe, ob du wirklich auf der Kursseite bist.",
            flush=True,
        )
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            if self._course_is_in_edit_mode(page) or self._editing_toggle_is_visible(page):
                return
            page.wait_for_timeout(1000)
        raise RuntimeError(
            "Nach 5 Minuten war kein Moodle-Bearbeitungsmodus sichtbar. "
            "Bitte pruefe, ob du eingeloggt bist und Bearbeitungsrechte fuer den Kurs hast."
        )

    def _editing_toggle_is_visible(self, page: Any) -> bool:
        if page.locator(
            '.editmode-switch-form input[type="checkbox"], input[name="setmode"], '
            '[data-action="toggle-editing"], a[href*="setmode=1"], a[href*="edit=on"]'
        ).count() > 0:
            return True
        for label in self._editing_toggle_labels():
            pattern = re.compile(label, re.IGNORECASE)
            if page.get_by_role("switch", name=pattern).count() > 0:
                return True
            if page.get_by_role("button", name=pattern).count() > 0:
                return True
            if page.get_by_role("link", name=pattern).count() > 0:
                return True
        return False

    def _editing_toggle_labels(self) -> list[str]:
        return [
            "Turn editing on",
            "Edit mode",
            "Bearbeiten einschalten",
            "Bearbeitungsmodus",
            "Bearbeiten",
        ]

    def _course_is_in_edit_mode(self, page: Any) -> bool:
        body_classes = page.locator("body").get_attribute("class") or ""
        if "editing" in body_classes:
            return True
        return page.locator(
            '[data-action="add-chooser-option"], [data-region="add-activity"], '
            '.editing_move, .activity-add, .section-modchooser'
        ).count() > 0

    def _find_or_create_section(self, page: Any, packages: list[MoodleH5PUploadPackage]) -> dict[str, str | int]:
        if hasattr(page, "goto"):
            page.goto(self.course_url, wait_until="domcontentloaded")
            self._turn_editing_on(page)
            if not self._course_is_in_edit_mode(page):
                raise RuntimeError("Bearbeitungsmodus ist nicht aktiv, obwohl die Section-Anlage gestartet wurde.")
        section = self._find_section(page)
        if section is not None:
            # If the target section exists by name, keep using it consistently.
            if self.section_title:
                return section
            if packages and not self._section_contains_packages(page, section, packages):
                package_section = self._find_section_containing_packages(page, packages)
                if package_section is not None:
                    print(
                        f"Moodle-Section '{self.section_title}' wurde gefunden, "
                        f"aber die H5Ps dieses Kapitels liegen in Section {package_section['number']}. "
                        "Verwende diese Section.",
                        flush=True,
                    )
                    if self.section_title:
                        package_section = self._ensure_section_title(page, package_section, self.section_title)
                    return package_section
            return section
        section = self._find_section_containing_packages(page, packages)
        if section is not None:
            print(
                f"Moodle-Section '{self.section_title}' wurde nicht gefunden, "
                f"aber Section {section['number']} enthaelt bereits H5Ps dieses Kapitels. Verwende diese Section.",
                flush=True,
            )
            if self.section_title:
                section = self._ensure_section_title(page, section, self.section_title)
            return section
        if not self.section_title:
            raise RuntimeError("Konnte Moodle-Section nicht finden.")

        print(f"Moodle-Section '{self.section_title}' existiert noch nicht. Lege sie am Kursende an.")
        section = self._create_section_at_end(page)
        rename_ok = False
        try:
            rename_ok = self._rename_section(page, int(section["number"]), self.section_title)
        except Exception as rename_error:
            self._write_debug_artifacts(page, f"section-rename-error-{section['number']}")
            print(
                f"Warnung: Section-Umbenennung hat eine Ausnahme ausgeloest: {rename_error}",
                flush=True,
            )
        if not rename_ok:
            raise RuntimeError(
                f"Die neue Moodle-Section {section['number']} wurde angelegt, konnte aber nicht in "
                f"'{self.section_title}' umbenannt werden."
            )
        page.goto(self.course_url, wait_until="domcontentloaded")
        self._turn_editing_on(page)
        section = self._find_section(page)
        if section is None:
            raise RuntimeError(f"Moodle-Section '{self.section_title}' wurde angelegt, aber danach nicht gefunden.")
        return section

    def _section_contains_packages(
        self,
        page: Any,
        section: dict[str, str | int],
        packages: list[MoodleH5PUploadPackage],
    ) -> bool:
        activities = self._collect_section_h5p_activities(page, str(section["selector"]))
        wanted = {normalize_moodle_identifier(package.identifier) for package in packages}
        wanted.update(normalize_moodle_identifier(package.title) for package in packages)
        return any(identifier in activities for identifier in wanted if identifier)

    def _ensure_section_title(
        self,
        page: Any,
        section: dict[str, str | int],
        title: str,
    ) -> dict[str, str | int]:
        current_title = str(section.get("title") or "")
        if normalize_moodle_section_title(current_title) == normalize_moodle_section_title(title):
            return section

        section_number = int(section["number"])
        print(f"Benenne Moodle-Section {section_number} in '{title}' um.", flush=True)
        if not self._rename_section(page, section_number, title):
            raise RuntimeError(f"Moodle-Section {section_number} konnte nicht in '{title}' umbenannt werden.")

        page.goto(self.course_url, wait_until="domcontentloaded")
        self._turn_editing_on(page)
        renamed_section = self._section_by_number(page, section_number) or self._find_section(page)
        if renamed_section is None:
            raise RuntimeError(f"Moodle-Section {section_number} wurde umbenannt, aber danach nicht gefunden.")
        renamed_title = str(renamed_section.get("title") or "")
        if normalize_moodle_section_title(renamed_title) != normalize_moodle_section_title(title):
            raise RuntimeError(
                f"Moodle-Section {section_number} existiert nach dem Umbenennen weiter, "
                f"traegt aber nicht den erwarteten Titel '{title}' (gefunden: '{renamed_title}')."
            )
        return renamed_section

    def _find_section_containing_packages(
        self,
        page: Any,
        packages: list[MoodleH5PUploadPackage],
    ) -> dict[str, str | int] | None:
        wanted = {normalize_moodle_identifier(package.identifier) for package in packages}
        wanted.update(normalize_moodle_identifier(package.title) for package in packages)
        wanted = {item for item in wanted if item}
        if not wanted:
            return None

        return page.evaluate(
            """
            (wantedValues) => {
              const wanted = new Set(wantedValues);
                            const sectionSelector = 'li.section, li.course-section, section.course-section';
                            const topLevelSection = (section) => {
                                let current = section;
                                while (current?.parentElement) {
                                    const parent = current.parentElement.closest(sectionSelector);
                                    if (!parent) {
                                        break;
                                    }
                                    current = parent;
                                }
                                return current;
                            };
              const normalize = (value) => (value || '')
                .trim()
                .toLowerCase()
                .replace(/ä/g, 'ae')
                .replace(/ö/g, 'oe')
                .replace(/ü/g, 'ue')
                .replace(/ß/g, 'ss')
                .normalize('NFKD')
                .replace(/[\\u0300-\\u036f]/g, '')
                .replace(/[^a-z0-9]+/g, '-')
                .replace(/^-+|-+$/g, '')
                .replace(/-+/g, '-');
              const activityLabel = (link) => {
                const activity = link.closest('[data-activityname], li.activity, .activity');
                const card = activity?.querySelector('[data-activityname]') || activity;
                const editable = activity?.querySelector('[data-value]');
                if (card?.getAttribute('data-activityname')) return card.getAttribute('data-activityname');
                if (editable?.getAttribute('data-value')) return editable.getAttribute('data-value');
                const clone = link.cloneNode(true);
                clone.querySelectorAll('.accesshide, .sr-only').forEach((node) => node.remove());
                return clone.textContent || link.getAttribute('aria-label') || '';
              };
                const links = [...document.querySelectorAll('a[href*="/mod/h5pactivity/view.php"]')];
              for (const link of links) {
                if (!wanted.has(normalize(activityLabel(link)))) {
                  continue;
                }
                                const section = topLevelSection(link.closest(sectionSelector));
                if (!section) continue;
                const numberValue = section.getAttribute('data-number')
                  || section.getAttribute('data-section')
                  || (section.id.match(/section-(\\d+)/) || [])[1]
                  || section.querySelector('[data-sectionnum]')?.getAttribute('data-sectionnum');
                const number = Number.parseInt(numberValue || '', 10);
                if (Number.isNaN(number)) continue;
                const marker = `course-sync-section-${Date.now()}-${Math.random().toString(16).slice(2)}`;
                section.setAttribute('data-course-sync-section', marker);
                const title = section.getAttribute('data-sectionname') || section.querySelector('.sectionname')?.textContent || '';
                const sectionDbId = section.getAttribute('data-id') || '';
                return { selector: `[data-course-sync-section="${marker}"]`, number, title, sectionDbId };
              }
              return null;
            }
            """,
            sorted(wanted),
        )

    def _find_section(self, page: Any) -> dict[str, str | int] | None:
        section_payload = page.evaluate(
            """
            (sectionTitle) => {
                            const sectionSelector = 'li.section, li.course-section, section.course-section';
              const normalize = (value) => (value || '')
                .trim()
                .toLowerCase()
                .replace(/ä/g, 'ae')
                .replace(/ö/g, 'oe')
                .replace(/ü/g, 'ue')
                .replace(/ß/g, 'ss')
                .normalize('NFKD')
                .replace(/[\\u0300-\\u036f]/g, '')
                .replace(/\\b(?:und|and)\\b/g, ' ')
                .replace(/[^a-z0-9]+/g, ' ')
                .replace(/\\s+/g, ' ')
                .trim();
              const wanted = normalize(sectionTitle);
                            const sections = [...document.querySelectorAll(sectionSelector)].filter((section) =>
                                !section.parentElement?.closest(sectionSelector)
                            );
              for (const section of sections) {
                                if (section.classList.contains('delegated-section') || section.classList.contains('subsection')) {
                  continue;
                }
                const titleNode = section.querySelector('.sectionname, .section-title, h3, h4');
                const title = normalize(titleNode ? titleNode.textContent : section.textContent);
                if (wanted && title !== wanted) {
                  continue;
                }
                const numberValue = section.getAttribute('data-number')
                  || section.getAttribute('data-section')
                  || (section.id.match(/section-(\\d+)/) || [])[1]
                  || section.querySelector('[data-sectionnum]')?.getAttribute('data-sectionnum');
                const number = Number.parseInt(numberValue || '', 10);
                if (!Number.isNaN(number)) {
                  const marker = `course-sync-section-${Date.now()}-${Math.random().toString(16).slice(2)}`;
                  section.setAttribute('data-course-sync-section', marker);
                  return { selector: `[data-course-sync-section="${marker}"]`, number, title };
                }
              }
              return null;
            }
            """,
            self.section_title,
        )
        return section_payload

    def _create_section_at_end(self, page: Any) -> dict[str, str | int]:
        before = self._last_section_number(page)
        if self._create_section_by_url(page, before):
            section = self._last_section(page)
            if section is not None and int(section["number"]) > before:
                return section

        if self._create_section_by_ui(page, before):
            section = self._last_section(page)
            if section is not None and int(section["number"]) > before:
                return section

        raise RuntimeError(
            "Moodle konnte keine neue Section am Kursende anlegen. "
            "Der Upload wird abgebrochen, damit keine Inhalte in einer falschen Section landen."
        )

    def _create_section_by_url(self, page: Any, before: int) -> bool:
        if not self._course_is_in_edit_mode(page):
            page.goto(self.course_url, wait_until="domcontentloaded")
            self._turn_editing_on(page)
        if not self._course_is_in_edit_mode(page):
            raise RuntimeError("Bearbeitungsmodus ist nicht aktiv. Moodle-Section kann nicht angelegt werden.")

        sesskey = self._moodle_sesskey(page)
        if not sesskey:
            return False

        print("Versuche Moodle-Section ueber changenumsections.php anzulegen.", flush=True)
        target_urls: list[str] = []
        add_section_link = self._add_section_href(page)
        if add_section_link:
            target_urls.append(add_section_link)
        target_urls.append(
            urljoin(self.course_url, "/course/changenumsections.php")
            + "?"
            + urlencode(
                {
                    "courseid": str(self._course_id()),
                    "insertsection": "0",
                    "sesskey": sesskey,
                }
            )
        )

        for target_url in target_urls:
            page.goto(target_url, wait_until="domcontentloaded")
            page.goto(self.course_url, wait_until="domcontentloaded")
            self._turn_editing_on(page)
            if self._last_section_number(page) > before:
                return True

        page.goto(self.course_url, wait_until="domcontentloaded")
        self._turn_editing_on(page)
        return self._last_section_number(page) > before

    def _add_section_href(self, page: Any) -> str:
        selectors = [
            'a[data-action="addSection"][href*="/course/changenumsections.php"]',
            'a.add-section[href*="/course/changenumsections.php"]',
            'a[data-add-sections][href*="/course/changenumsections.php"]',
        ]
        for selector in selectors:
            locator = page.locator(selector)
            count = locator.count()
            if count == 0:
                continue
            if hasattr(locator, "nth"):
                candidates = [locator.nth(index) for index in range(count)]
            else:
                first = getattr(locator, "first", None)
                candidates = [first] if first is not None else []
            for candidate in candidates:
                href = candidate.get_attribute("href")
                if not href:
                    continue
                absolute_href = urljoin(self.course_url, href)
                if self._is_top_level_add_section_href(absolute_href):
                    return absolute_href
        return ""

    def _is_top_level_add_section_href(self, href: str) -> bool:
        parsed = urlparse(urljoin(self.course_url, href))
        insert_sections = parse_qs(parsed.query).get("insertsection", [])
        return any(value == "0" for value in insert_sections)

    def _create_section_by_ui(self, page: Any, before: int) -> bool:
        print("Versuche Moodle-Section ueber die Kursoberflaeche anzulegen.", flush=True)
        page.goto(self.course_url, wait_until="domcontentloaded")
        self._turn_editing_on(page)
        if not self._course_is_in_edit_mode(page):
            raise RuntimeError("Bearbeitungsmodus ist nicht aktiv. Moodle-Section kann nicht ueber die UI angelegt werden.")

        selectors = [
            'a[href*="changenumsections.php"][href*="insertsection=0"]',
            '[data-action="addSection"]',
            '[data-action="add-section"]',
            '[data-add-sections]',
            '.add-sections button',
            '.add-sections a',
            'button[name="addsection"]',
            'a[href*="changenumsections.php"][href*="insertsection="]',
        ]
        for selector in selectors:
            locator = page.locator(selector)
            if locator.count() > 0 and self._click_add_section_control(page, locator.first, before):
                return True

        labels = [
            "Add section",
            "Add sections",
            "Add topic",
            "Add week",
            "Abschnitt hinzufügen",
            "Abschnitte hinzufügen",
            "Thema hinzufügen",
            "Woche hinzufügen",
        ]
        for label in labels:
            pattern = re.compile(label, re.IGNORECASE)
            for role in ["button", "link"]:
                locator = page.get_by_role(role, name=pattern)
                if locator.count() > 0 and self._click_add_section_control(page, locator.first, before):
                    return True
            text = page.get_by_text(pattern)
            if text.count() > 0 and self._click_add_section_control(page, text.first, before):
                return True

        return False

    def _click_add_section_control(self, page: Any, locator: Any, before: int) -> bool:
        try:
            href = locator.get_attribute("href") if hasattr(locator, "get_attribute") else None
            if href and "changenumsections.php" in href:
                absolute_href = urljoin(self.course_url, href)
                if not self._is_top_level_add_section_href(absolute_href):
                    return False
                page.goto(absolute_href, wait_until="domcontentloaded")
            else:
                locator.click()
                page.wait_for_timeout(750)
                self._confirm_add_section_dialog_if_needed(page)
                page.wait_for_load_state("domcontentloaded")
        except Exception:
            return False

        page.goto(self.course_url, wait_until="domcontentloaded")
        self._turn_editing_on(page)
        return self._last_section_number(page) > before

    def _confirm_add_section_dialog_if_needed(self, page: Any) -> None:
        number_input = page.locator(
            '.modal-dialog input[type="number"], .modal-dialog input[name*="num"], '
            '.modal-dialog input[name*="section"]'
        )
        if number_input.count() > 0:
            number_input.first.fill("1")

        for label in [
            "Add sections",
            "Add section",
            "Create",
            "Save changes",
            "Abschnitte hinzufügen",
            "Abschnitt hinzufügen",
            "Erstellen",
            "Änderungen speichern",
            "Aenderungen speichern",
        ]:
            button = page.get_by_role("button", name=re.compile(label, re.IGNORECASE))
            if button.count() > 0:
                button.first.click()
                page.wait_for_timeout(750)
                return

    def _last_section_number(self, page: Any) -> int:
        section = self._last_section(page)
        return int(section["number"]) if section is not None else 0

    def _last_section(self, page: Any) -> dict[str, str | int] | None:
        return page.evaluate(
            """
            () => {
                            const sectionSelector = 'li.section, li.course-section, section.course-section';
                            const sections = [...document.querySelectorAll(sectionSelector)].filter((section) =>
                                !section.parentElement?.closest(sectionSelector)
                            );
              let best = null;
              for (const section of sections) {
                                if (section.classList.contains('delegated-section') || section.classList.contains('subsection')) continue;
                const numberValue = section.getAttribute('data-number')
                  || section.getAttribute('data-section')
                  || section.getAttribute('data-sectionid')
                  || (section.id.match(/section-(\\d+)/) || [])[1]
                  || section.querySelector('[data-sectionnum]')?.getAttribute('data-sectionnum');
                const number = Number.parseInt(numberValue || '', 10);
                if (Number.isNaN(number)) continue;
                if (!best || number > best.number) {
                  const marker = `course-sync-section-${Date.now()}-${Math.random().toString(16).slice(2)}`;
                  section.setAttribute('data-course-sync-section', marker);
                                    const title = (section.getAttribute('data-sectionname') || section.querySelector('.sectionname, .section-title, h3, h4')?.textContent || '').trim();
                  const sectionDbId = section.getAttribute('data-id') || '';
                                    best = { selector: `[data-course-sync-section="${marker}"]`, number, title, sectionDbId };
                }
              }
              return best;
            }
            """
        )

    def _rename_section(self, page: Any, section_number: int, title: str) -> bool:
        try:
            if self._rename_section_from_course_page(page, section_number, title):
                return True
        except Exception as err:
            print(f"Warnung: _rename_section_from_course_page hat Ausnahme ausgeloest: {err}", flush=True)
            self._write_debug_artifacts(page, f"editsection-{section_number}-from-course-page-error")

        params = {
            "courseid": str(self._course_id()),
            "section": str(section_number),
        }
        page.goto(urljoin(self.course_url, "/course/editsection.php") + "?" + urlencode(params), wait_until="domcontentloaded")
        self._write_debug_artifacts(page, f"editsection-{section_number}-form")

        default_name_checkbox = page.locator('input[name="usedefaultname"], #id_usedefaultname')
        if default_name_checkbox.count() > 0 and default_name_checkbox.first.is_checked():
            default_name_checkbox.first.uncheck()

        name_input = page.locator('input[name="name"], #id_name')
        if name_input.count() == 0:
            return False
        name_input.first.fill(title)

        try:
            self._click_first_by_text(
                page,
                [
                    "Save changes",
                    "Save and return",
                    "Änderungen speichern",
                    "Aenderungen speichern",
                    "Speichern",
                ],
            )
        except RuntimeError:
            self._write_debug_artifacts(page, f"editsection-{section_number}-no-save-button")
            name_input.first.press("Enter")
        page.wait_for_load_state("domcontentloaded")
        return True

    def _rename_section_from_course_page(self, page: Any, section_number: int, title: str) -> bool:
        page.goto(self.course_url, wait_until="domcontentloaded")
        self._turn_editing_on(page)
        section = self._section_by_number(page, section_number)
        if section is None:
            return False

        edit_href = page.locator(section["selector"]).evaluate(
            """
            (section) => {
              const links = [...section.querySelectorAll('a[href]')];
              const byHref = links.find((link) =>
                /\\/course\\/editsection\\.php/i.test(link.href || '')
                && !/[?&]delete=1(?:&|$)/i.test(link.href || '')
              );
              if (byHref) return byHref.href;
              const byLabel = links.find((link) =>
                /Einstellungen bearbeiten|Edit settings/i.test(link.textContent || link.getAttribute('aria-label') || '')
              );
              return byLabel ? byLabel.href : '';
            }
            """
        )

        if not edit_href:
            section_db_id = str(section.get("sectionDbId", "")).strip()
            if not section_db_id:
                print(f"Warnung: Kein Edit-Link und keine sectionDbId fuer Section {section_number}. section={section}", flush=True)
                return False
            edit_href = urljoin(self.course_url, "/course/editsection.php") + "?id=" + section_db_id

        print(f"Oeffne Abschnitts-Formular: {edit_href}", flush=True)
        page.goto(str(edit_href), wait_until="domcontentloaded")
        self._write_debug_artifacts(page, f"editsection-{section_number}-form-from-course-page")
        default_name_checkbox = page.locator('input[name="usedefaultname"], #id_usedefaultname')
        if default_name_checkbox.count() > 0 and default_name_checkbox.first.is_checked():
            default_name_checkbox.first.uncheck()

        name_input = page.locator('input[name="name"], #id_name')
        if name_input.count() == 0:
            return False
        name_input.first.fill(title)
        self._click_first_by_text(
            page,
            [
                "Save changes",
                "Save and return",
                "Änderungen speichern",
                "Aenderungen speichern",
                "Speichern",
            ],
        )
        page.wait_for_load_state("domcontentloaded")
        return True

    def _section_by_number(self, page: Any, section_number: int) -> dict[str, str | int] | None:
        return page.evaluate(
            """
            (wantedNumber) => {
                            const sectionSelector = 'li.section, li.course-section, section.course-section';
                            const sections = [...document.querySelectorAll(sectionSelector)].filter((section) =>
                                !section.parentElement?.closest(sectionSelector)
                            );
              for (const section of sections) {
                                if (section.classList.contains('delegated-section') || section.classList.contains('subsection')) continue;
                const numberValue = section.getAttribute('data-number')
                  || section.getAttribute('data-section')
                  || section.getAttribute('data-sectionid')
                  || (section.id.match(/section-(\\d+)/) || [])[1]
                  || section.querySelector('[data-sectionnum]')?.getAttribute('data-sectionnum');
                const number = Number.parseInt(numberValue || '', 10);
                if (number !== wantedNumber) continue;
                const marker = `course-sync-section-${Date.now()}-${Math.random().toString(16).slice(2)}`;
                section.setAttribute('data-course-sync-section', marker);
                                const title = (section.getAttribute('data-sectionname') || section.querySelector('.sectionname, .section-title, h3, h4')?.textContent || '').trim();
                const sectionDbId = section.getAttribute('data-id') || '';
                                return { selector: `[data-course-sync-section="${marker}"]`, number, title, sectionDbId };
              }
              return null;
            }
            """,
            section_number,
        )

    def _moodle_sesskey(self, page: Any) -> str:
        sesskey = page.evaluate(
            """
            () => {
              if (window.M && window.M.cfg && window.M.cfg.sesskey) {
                return window.M.cfg.sesskey;
              }
              const input = document.querySelector('input[name="sesskey"]');
              if (input && input.value) {
                return input.value;
              }
              const match = document.documentElement.innerHTML.match(/sesskey=([^"'&<>]+)/);
              return match ? decodeURIComponent(match[1]) : '';
            }
            """
        )
        return str(sesskey or "")

    def _collect_section_h5p_activities(self, page: Any, section_selector: str) -> dict[str, int]:
        payload = page.locator(section_selector).evaluate(
            """
            (section) => {
              const activities = {};
              const normalize = (value) => (value || '')
                .trim()
                .toLowerCase()
                .replace(/ä/g, 'ae')
                .replace(/ö/g, 'oe')
                .replace(/ü/g, 'ue')
                .replace(/ß/g, 'ss')
                .normalize('NFKD')
                .replace(/[\\u0300-\\u036f]/g, '')
                .replace(/[^a-z0-9]+/g, '-')
                .replace(/^-+|-+$/g, '')
                .replace(/-+/g, '-');
              const activityLabel = (link) => {
                const activity = link.closest('[data-activityname], li.activity, .activity');
                const card = activity?.querySelector('[data-activityname]') || activity;
                const editable = activity?.querySelector('[data-value]');
                if (card?.getAttribute('data-activityname')) return card.getAttribute('data-activityname');
                if (editable?.getAttribute('data-value')) return editable.getAttribute('data-value');
                const clone = link.cloneNode(true);
                clone.querySelectorAll('.accesshide, .sr-only').forEach((node) => node.remove());
                return clone.textContent || link.getAttribute('aria-label') || '';
              };
              const links = [...section.querySelectorAll('a[href*="/mod/h5pactivity/view.php"]')];
              for (const link of links) {
                const href = link.href || '';
                const cmid = new URL(href).searchParams.get('id');
                if (!cmid) continue;
                const label = activityLabel(link);
                activities[normalize(label)] = Number.parseInt(cmid, 10);
              }
              return activities;
            }
            """
        )
        return {str(key): int(value) for key, value in payload.items() if value}

    def _collect_section_h5p_activity_order(self, page: Any, section_selector: str) -> list[dict[str, Any]]:
        payload = page.locator(section_selector).evaluate(
            """
            (section) => {
              const normalize = (value) => (value || '')
                .trim()
                .toLowerCase()
                .replace(/ä/g, 'ae')
                .replace(/ö/g, 'oe')
                .replace(/ü/g, 'ue')
                .replace(/ß/g, 'ss')
                .normalize('NFKD')
                .replace(/[\\u0300-\\u036f]/g, '')
                .replace(/[^a-z0-9]+/g, '-')
                .replace(/^-+|-+$/g, '')
                .replace(/-+/g, '-');
              const activityLabel = (link) => {
                const activity = link.closest('[data-activityname], li.activity, .activity');
                const card = activity?.querySelector('[data-activityname]') || activity;
                const editable = activity?.querySelector('[data-value]');
                if (card?.getAttribute('data-activityname')) return card.getAttribute('data-activityname');
                if (editable?.getAttribute('data-value')) return editable.getAttribute('data-value');
                const clone = link.cloneNode(true);
                clone.querySelectorAll('.accesshide, .sr-only').forEach((node) => node.remove());
                return clone.textContent || link.getAttribute('aria-label') || '';
              };
              const seen = new Set();
              const activities = [];
              const links = [...section.querySelectorAll('a[href*="/mod/h5pactivity/view.php"]')];
              for (const link of links) {
                const href = link.href || '';
                const cmid = Number.parseInt(new URL(href).searchParams.get('id') || '', 10);
                if (!cmid || seen.has(cmid)) continue;
                seen.add(cmid);
                const label = activityLabel(link);
                activities.push({ id: cmid, title: label.trim(), identifier: normalize(label) });
              }
              return activities;
            }
            """
        )
        return [
            {"id": int(item["id"]), "title": str(item.get("title") or ""), "identifier": str(item.get("identifier") or "")}
            for item in payload
            if item.get("id")
        ]

    def _sort_section_h5p_activities(
        self,
        page: Any,
        section_number: int,
        packages: list[MoodleH5PUploadPackage],
    ) -> None:
        desired_keys = [
            (normalize_moodle_identifier(package.identifier), normalize_moodle_identifier(package.title))
            for package in packages
        ]
        if len(desired_keys) < 2:
            return

        print("Pruefe Reihenfolge der H5P-Aktivitaeten in der Moodle-Section.", flush=True)
        section = self._section_by_number(page, section_number)
        if section is None:
            return

        activities = self._collect_section_h5p_activity_order(page, str(section["selector"]))
        id_by_key: dict[str, int] = {}
        for activity in activities:
            identifier = str(activity["identifier"])
            if identifier and identifier not in id_by_key:
                id_by_key[identifier] = int(activity["id"])
        available_ids = {int(activity["id"]) for activity in activities}

        desired_ids: list[int] = []
        for package, (identifier_key, title_key) in zip(packages, desired_keys, strict=True):
            activity_id = (
                id_by_key.get(identifier_key)
                or id_by_key.get(title_key)
                or self.existing_activity_ids.get(package.identifier)
                or self.existing_activity_ids.get(identifier_key)
                or self.existing_activity_ids.get(title_key)
            )
            if activity_id is not None and int(activity_id) in available_ids:
                desired_ids.append(activity_id)

        if len(desired_ids) < 2:
            return

        current_desired_ids = [int(activity["id"]) for activity in activities if int(activity["id"]) in set(desired_ids)]
        if current_desired_ids == desired_ids:
            print("H5P-Reihenfolge stimmt bereits.", flush=True)
            return

        print("Sortiere H5P-Aktivitaeten nach Kapitelreihenfolge.", flush=True)
        for index in range(len(desired_ids) - 2, -1, -1):
            self._move_activity_before(page, desired_ids[index], desired_ids[index + 1])
        for attempt in range(3):
            page.goto(self.course_url, wait_until="domcontentloaded")
            self._turn_editing_on(page)
            section = self._section_by_number(page, section_number)
            if section is None:
                raise RuntimeError("Konnte Moodle-Section nach dem Sortieren nicht erneut finden.")
            activities = self._collect_section_h5p_activity_order(page, str(section["selector"]))
            current_desired_ids = [int(activity["id"]) for activity in activities if int(activity["id"]) in set(desired_ids)]
            if current_desired_ids == desired_ids:
                return

            if attempt < 2:
                page.wait_for_timeout(1500)
                for index in range(len(desired_ids) - 2, -1, -1):
                    self._move_activity_before(page, desired_ids[index], desired_ids[index + 1])

        print(
            "Warnung: Moodle-H5P-Reihenfolge stimmt nach mehreren Sortier-Versuchen noch nicht. "
            "Upload wird trotzdem fortgesetzt.",
            flush=True,
        )

    def _move_activity_before(self, page: Any, activity_id: int, before_activity_id: int) -> None:
        sesskey = self._moodle_sesskey(page)
        if not sesskey:
            raise RuntimeError("Moodle-Sesskey fehlt; Aktivitaeten koennen nicht sortiert werden.")

        page.goto(
            urljoin(self.course_url, "/course/mod.php") + "?" + urlencode({"sesskey": sesskey, "copy": str(activity_id)}),
            wait_until="domcontentloaded",
        )
        page.goto(
            urljoin(self.course_url, "/course/mod.php")
            + "?"
            + urlencode({"moveto": str(before_activity_id), "sesskey": sesskey}),
            wait_until="domcontentloaded",
        )

    def _create_h5p_activity(self, page: Any, section_number: int, package: MoodleH5PUploadPackage) -> MoodleH5PUploadResult:
        params = {
            "add": "h5pactivity",
            "type": "",
            "course": str(self._course_id()),
            "section": str(section_number),
            "return": "0",
            "sr": str(section_number),
        }
        page.goto(urljoin(self.course_url, "/course/modedit.php") + "?" + urlencode(params), wait_until="domcontentloaded")
        self._fill_h5p_form(page, package)
        activity_id = self._activity_id_from_current_page(page)
        return MoodleH5PUploadResult(package.identifier, package.title, "created", activity_id)

    def _update_h5p_activity(self, page: Any, activity_id: int, package: MoodleH5PUploadPackage) -> MoodleH5PUploadResult | None:
        params = {"update": str(activity_id), "return": "0"}
        page.goto(urljoin(self.course_url, "/course/modedit.php") + "?" + urlencode(params), wait_until="domcontentloaded")
        if not self._h5p_form_is_available(page):
            self._write_debug_artifacts(page, f"invalid-update-{activity_id}")
            return None
        self._fill_h5p_form(page, package)
        return MoodleH5PUploadResult(package.identifier, package.title, "updated", activity_id)

    def _h5p_form_is_available(self, page: Any) -> bool:
        if page.locator('input[name="name"], #id_name').count() > 0:
            return True
        if page.locator("#fitem_id_packagefile, input[name='packagefile']").count() > 0:
            return True
        return False

    def _fill_h5p_form(self, page: Any, package: MoodleH5PUploadPackage) -> None:
        if not self._upload_h5p_package_file(page, package):
            self._write_debug_artifacts(page, "missing-file-input")
            raise RuntimeError("Kein Datei-Uploadfeld im H5P-Formular gefunden.")
        self._confirm_file_overwrite_if_needed(page)
        self._close_open_moodle_dialogues(page)

        # Set the activity title after package upload because Moodle may prefill
        # the title from package metadata during upload processing.
        name_input = page.locator('input[name="name"], #id_name')
        if name_input.count() > 0:
            name_input.first.fill(package.title)

        self._set_activity_points(page, package.points)
        self._set_completion_tracking(page, gradepass=1)

        self._click_first_by_text(
            page,
            [
                "Speichern und anzeigen",
                "Save and display",
                "Speichern und zum Kurs",
                "Save and return to course",
                "Speichern",
            ],
        )
        page.wait_for_load_state("domcontentloaded")

    def _set_activity_points(self, page: Any, points: int) -> None:
        value = str(max(0, int(points)))

        # Moodle variants use either a plain numeric field or a select.
        for selector in (
            'input[name="grade"]',
            '#id_grade',
            'input[name="maxgrade"]',
            'select[name="grade"]',
            '#id_grademethod + select',
        ):
            control = page.locator(selector)
            if control.count() == 0:
                continue
            field = control.first
            try:
                self._set_form_control_value(field, value)
                return
            except Exception:
                pass
            try:
                field.select_option(value=value)
                return
            except Exception:
                continue

    def _set_completion_tracking(self, page: Any, gradepass: int = 1) -> None:
        # Set the passing grade in the grade section (gradepass field).
        gradepass_input = page.locator('input[name="gradepass"], #id_gradepass')
        if gradepass_input.count() > 0:
            self._set_form_control_value(gradepass_input.first, str(gradepass))

        # Enable completion tracking if not already set to "show activity as complete
        # when conditions are met" (value 2).
        completion_select = page.locator('select[name="completion"], #id_completion')
        if completion_select.count() > 0:
            try:
                completion_select.first.select_option(value="2")
            except Exception:
                pass

        # Condition: student must receive a grade (completionusegrade = 1).
        grade_condition = page.locator(
            'input[name="completionusegrade"], #id_completionusegrade'
        )
        if grade_condition.count() > 0 and not grade_condition.first.is_checked():
            self._set_checkbox_checked(grade_condition.first)

        # Condition: student must reach the passing grade (completionpassgrade = 1).
        # Moodle 4.x exposes this as a separate checkbox that only appears after
        # completionusegrade is checked, so wait briefly for it to become visible.
        page.wait_for_timeout(300)
        passgrade_condition = page.locator(
            'input[name="completionpassgrade"], #id_completionpassgrade'
        )
        if passgrade_condition.count() > 0 and not passgrade_condition.first.is_checked():
            self._set_checkbox_checked(passgrade_condition.first)

    def _set_form_control_value(self, locator: Any, value: str) -> None:
        try:
            if locator.is_visible():
                locator.fill(value)
                return
        except Exception:
            pass
        locator.evaluate(
            """(element, value) => {
                element.value = value;
                element.dispatchEvent(new Event('input', { bubbles: true }));
                element.dispatchEvent(new Event('change', { bubbles: true }));
            }""",
            value,
        )

    def _set_checkbox_checked(self, locator: Any) -> None:
        try:
            if locator.is_visible():
                locator.check()
                return
        except Exception:
            pass
        locator.evaluate(
            """(element) => {
                element.checked = true;
                element.dispatchEvent(new Event('input', { bubbles: true }));
                element.dispatchEvent(new Event('change', { bubbles: true }));
            }"""
        )

    def _upload_h5p_package_file(self, page: Any, package: MoodleH5PUploadPackage) -> bool:
        def debug(message: str) -> None:
            if os.environ.get("COURSE_SYNC_DEBUG_MOODLE_FILEMANAGER"):
                print(f"Filemanager-Debug: {message}", flush=True)

        direct_input = page.locator('input[type="file"]')
        debug(f"direct file inputs: {direct_input.count()}")
        if direct_input.count() > 0:
            direct_input.first.set_input_files(str(package.path))
            return True

        try:
            page.wait_for_selector("#fitem_id_packagefile .filemanager", timeout=10_000)
            page.wait_for_function(
                """
                () => {
                  const manager = document.querySelector('#fitem_id_packagefile .filemanager');
                  return manager && !manager.classList.contains('fm-loading');
                }
                """,
                timeout=20_000,
            )
        except Exception:
            pass
        page.wait_for_timeout(1500)

        add_button = page.locator(
            '#fitem_id_packagefile .fp-btn-add a, '
            '#fitem_id_packagefile a[title*="Datei hinzufügen"], '
            '#fitem_id_packagefile a[title*="Add file"], '
            '#fitem_id_packagefile button[title*="Datei hinzufügen"], '
            '#fitem_id_packagefile button[title*="Add file"]'
        )
        existing_file = page.locator("#fitem_id_packagefile .fp-file")
        debug(
            "before delete: "
            f"files={existing_file.count()} file_visible={self._locator_has_visible_item(existing_file)} "
            f"add={add_button.count()} add_visible={self._locator_has_visible_item(add_button)}"
        )
        if existing_file.count() > 0 and self._locator_has_visible_item(existing_file):
            self._delete_existing_h5p_package_file_if_needed(page)
            add_button = page.locator(
                '#fitem_id_packagefile .fp-btn-add a, '
                '#fitem_id_packagefile a[title*="Datei hinzufügen"], '
                '#fitem_id_packagefile a[title*="Add file"], '
                '#fitem_id_packagefile button[title*="Datei hinzufügen"], '
                '#fitem_id_packagefile button[title*="Add file"]'
            )
            try:
                add_button.first.wait_for(state="visible", timeout=8_000)
            except Exception:
                pass
            debug(
                "after delete existing: "
                f"files={page.locator('#fitem_id_packagefile .fp-file').count()} "
                f"file_visible={self._locator_has_visible_item(page.locator('#fitem_id_packagefile .fp-file'))} "
                f"add={add_button.count()} add_visible={self._locator_has_visible_item(add_button)}"
            )
        elif add_button.count() > 0 and not self._locator_has_visible_item(add_button):
            self._delete_existing_h5p_package_file_if_needed(page)
            add_button = page.locator(
                '#fitem_id_packagefile .fp-btn-add a, '
                '#fitem_id_packagefile a[title*="Datei hinzufügen"], '
                '#fitem_id_packagefile a[title*="Add file"], '
                '#fitem_id_packagefile button[title*="Datei hinzufügen"], '
                '#fitem_id_packagefile button[title*="Add file"]'
            )
            try:
                add_button.first.wait_for(state="visible", timeout=8_000)
            except Exception:
                pass
            debug(
                "after hidden add delete: "
                f"files={page.locator('#fitem_id_packagefile .fp-file').count()} "
                f"file_visible={self._locator_has_visible_item(page.locator('#fitem_id_packagefile .fp-file'))} "
                f"add={add_button.count()} add_visible={self._locator_has_visible_item(add_button)}"
            )
        if add_button.count() == 0:
            debug("no add button")
            return False
        if not self._locator_has_visible_item(add_button):
            # The filemanager toolbar can briefly re-render and hide the add
            # button although no file is present. Wait once and re-check.
            try:
                add_button.first.wait_for(state="visible", timeout=8_000)
            except Exception:
                pass
            add_button = page.locator(
                '#fitem_id_packagefile .fp-btn-add a, '
                '#fitem_id_packagefile a[title*="Datei hinzufügen"], '
                '#fitem_id_packagefile a[title*="Add file"], '
                '#fitem_id_packagefile button[title*="Datei hinzufügen"], '
                '#fitem_id_packagefile button[title*="Add file"]'
            )
            if not self._locator_has_visible_item(add_button):
                self._close_open_moodle_dialogues(page)
                debug("add button not visible")
                return False

        try:
            self._click_first_visible(add_button)
        except Exception:
            if page.locator("#fitem_id_packagefile .fp-file").count() > 0:
                self._close_open_moodle_dialogues(page)
                debug("click add failed while file exists")
                return False
            raise
        page.wait_for_timeout(1000)
        debug(
            "after add click: "
            f"dialogs={page.locator('.modal.show, .moodle-dialogue-base[aria-hidden=\"false\"]').count()} "
            f"file_inputs={page.locator('input[type=\"file\"]').count()}"
        )

        upload_repo_labels = ["Datei hochladen", "Upload a file"]
        for label in upload_repo_labels:
            repo = page.get_by_text(re.compile(label, re.IGNORECASE))
            if repo.count() > 0:
                repo.first.click()
                page.wait_for_timeout(500)
                break

        picker_input = page.locator('input[type="file"]')
        try:
            picker_input.first.wait_for(state="attached", timeout=15_000)
        except Exception:
            debug("picker input missing")
            return False
        picker_input.first.set_input_files(str(package.path))

        for label in ["Datei hochladen", "Upload this file", "Upload", "Hochladen"]:
            button = page.get_by_role("button", name=re.compile(label, re.IGNORECASE))
            if button.count() > 0:
                button.first.click()
                page.wait_for_timeout(1500)
                self._close_open_moodle_dialogues(page)
                return True

        return True

    def _delete_existing_h5p_package_file_if_needed(self, page: Any) -> None:
        file_locator = page.locator("#fitem_id_packagefile .fp-file")
        try:
            page.wait_for_selector("#fitem_id_packagefile .fp-file:visible", timeout=20_000)
        except Exception:
            return

        # Moodle's filemanager can need one click to select a file and a second
        # one to open its action dialogue, depending on whether the YUI
        # filemanager has finished binding handlers. Try a few gentle variants
        # before giving up; maxfiles=1 keeps the upload button hidden until this
        # existing file is really deleted.
        opened_dialogue = False
        click_targets = page.locator(
            "#fitem_id_packagefile .fp-file a.d-block, "
            "#fitem_id_packagefile .fp-file, "
            "#fitem_id_packagefile .fp-filename, "
            "#fitem_id_packagefile .fp-contextmenu"
        )
        for index in range(click_targets.count()):
            click_target = click_targets.nth(index)
            try:
                if not self._locator_item_is_clickable(click_target):
                    continue
                click_target.click()
                page.locator(
                    ".moodle-dialogue-base[aria-hidden='false'] .fp-file-delete, "
                    ".modal.show .fp-file-delete"
                ).first.wait_for(state="visible", timeout=2_500)
                opened_dialogue = True
                break
            except Exception:
                page.wait_for_timeout(500)
                continue
        if not opened_dialogue:
            return
        page.locator(
            ".moodle-dialogue-base[aria-hidden='false'] .fp-file-delete, "
            ".modal.show .fp-file-delete"
        ).first.click()
        confirmed = self._confirm_file_delete_if_needed(page)
        if confirmed:
            page.wait_for_timeout(1500)

        self._close_open_moodle_dialogues(page)
        try:
            page.locator("#fitem_id_packagefile .fp-btn-add a").first.wait_for(state="visible", timeout=15_000)
        except Exception:
            page.wait_for_timeout(1500)

    def _confirm_file_delete_if_needed(self, page: Any) -> bool:
        confirmation_targets = [
            page.locator(".modal.show .btn-primary"),
            page.locator(".modal.show [data-action='save']"),
            page.locator(".moodle-dialogue-base[aria-hidden='false'] .fp-dlg-butconfirm"),
        ]
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            for target in confirmation_targets:
                visible_button = self._first_clickable(target)
                if visible_button is not None:
                    visible_button.click()
                    return True
            for dialog_selector in [".modal.show", ".moodle-dialogue-base[aria-hidden='false']"]:
                dialog = page.locator(dialog_selector)
                if dialog.count() == 0:
                    continue
                for label in ["Ja", "Yes", "OK", "Löschen", "Delete"]:
                    visible_button = self._first_clickable(
                        dialog.last.get_by_role("button", name=re.compile(label, re.IGNORECASE))
                    )
                    if visible_button is not None:
                        visible_button.click()
                        return True
            page.wait_for_timeout(250)
        return False

    def _close_open_moodle_dialogues(self, page: Any) -> None:
        for _ in range(3):
            open_dialogs = page.locator('.moodle-dialogue-base[aria-hidden="false"], .modal.show')
            if open_dialogs.count() == 0:
                return
            close_button = page.locator(
                '.moodle-dialogue-base[aria-hidden="false"] .closebutton, '
                '.moodle-dialogue-base[aria-hidden="false"] .fp-file-cancel, '
                '.modal.show button[aria-label="Schließen"], '
                '.modal.show button[aria-label="Close"], '
                '.modal.show .btn-secondary'
            )
            clicked = False
            for index in range(close_button.count()):
                candidate = close_button.nth(index)
                try:
                    if candidate.is_visible():
                        candidate.click()
                        clicked = True
                        break
                except Exception:
                    continue
            if not clicked:
                page.keyboard.press("Escape")
            page.wait_for_timeout(500)

    def _locator_has_visible_item(self, locator: Any) -> bool:
        for index in range(locator.count()):
            try:
                if self._locator_item_is_clickable(locator.nth(index)):
                    return True
            except Exception:
                continue
        return False

    def _click_first_visible(self, locator: Any) -> None:
        candidate = self._first_clickable(locator)
        if candidate is not None:
            candidate.click()
            return
        locator.first.click(force=True)

    def _first_clickable(self, locator: Any) -> Any | None:
        for index in range(locator.count()):
            candidate = locator.nth(index)
            try:
                if self._locator_item_is_clickable(candidate):
                    return candidate
            except Exception:
                continue
        return None

    def _locator_item_is_clickable(self, locator: Any) -> bool:
        if not locator.is_visible():
            return False
        box = locator.bounding_box()
        if not box:
            return False
        return bool(box.get("width", 0) > 0 and box.get("height", 0) > 0)

    def _confirm_file_overwrite_if_needed(self, page: Any) -> None:
        for label in ["Overwrite", "Ersetzen", "Überschreiben"]:
            button = page.get_by_role("button", name=re.compile(label, re.IGNORECASE))
            if button.count() > 0:
                button.first.click()
                page.wait_for_timeout(500)
                return

    def _write_debug_artifacts(self, page: Any, label: str) -> None:
        debug_dir = Path("temp") / "moodle-upload-debug"
        debug_dir.mkdir(parents=True, exist_ok=True)
        safe_label = re.sub(r"[^a-zA-Z0-9_-]+", "-", label).strip("-") or "page"
        html_path = debug_dir / f"{safe_label}.html"
        screenshot_path = debug_dir / f"{safe_label}.png"
        try:
            html_path.write_text(page.content(), encoding="utf-8")
            page.screenshot(path=str(screenshot_path), full_page=True)
            print(f"Debug gespeichert: {html_path} und {screenshot_path}", flush=True)
        except Exception as error:
            print(f"Debug konnte nicht gespeichert werden: {error}", flush=True)

    def _format_timeout_error(
        self,
        error: Exception,
        *,
        current_step: str,
        package: MoodleH5PUploadPackage | None,
    ) -> str:
        raw_lines = [line.strip() for line in str(error).splitlines() if line.strip()]
        summary = raw_lines[0] if raw_lines else error.__class__.__name__
        details = [
            "Moodle-Upload hat zu lange gewartet.",
            f"Schritt: {current_step}",
            f"Kurs: {self.course_url}",
        ]
        if package is not None:
            details.extend(
                [
                    f"H5P-Paket: {package.identifier}",
                    f"Titel: {package.title}",
                    f"Datei: {package.path}",
                ]
            )
        details.extend(
            [
                f"Playwright: {summary}",
                "Debug: temp/moodle-upload-debug/timeout-*.html und .png",
            ]
        )
        return "\n".join(details)

    def _click_first_by_text(self, page: Any, labels: list[str]) -> None:
        for label in labels:
            pattern = re.compile(label, re.IGNORECASE)
            button = page.get_by_role("button", name=pattern)
            if button.count() > 0:
                button.first.click()
                return
            link = page.get_by_role("link", name=pattern)
            if link.count() > 0:
                link.first.click()
                return
            text = page.get_by_text(pattern)
            if text.count() > 0:
                text.first.click()
                return
        raise RuntimeError(f"Keinen passenden Moodle-Button gefunden: {', '.join(labels)}")

    def _course_id(self) -> int:
        parsed = urlparse(self.course_url)
        course_ids = parse_qs(parsed.query).get("id", [])
        if not course_ids:
            raise RuntimeError(f"Course-ID fehlt in Moodle-URL: {self.course_url}")
        return int(course_ids[0])

    def _activity_id_from_current_page(self, page: Any) -> int | None:
        parsed = urlparse(page.url)
        ids = parse_qs(parsed.query).get("id", [])
        if ids:
            return int(ids[0])
        return None
