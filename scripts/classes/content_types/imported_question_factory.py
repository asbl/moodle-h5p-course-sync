from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Callable, Protocol

from scripts.classes.models import MoodleH5PActivity, PythonQuestionBlock, TestCase
from scripts.classes.python_runner_policy import DEFAULT_PYTHON_RUNNER


class H5PImportMapperProtocol(Protocol):
    def summarize_instructions(self, activity: MoodleH5PActivity, content_payload: dict[str, object]) -> str: ...

    def extract_editor_instructions(self, content_payload: dict[str, object]) -> str: ...

    def extract_test_case_values(self, raw_values: object, *, field_name: str) -> list[str]: ...

    def extract_packages(self, content_payload: dict[str, object]) -> list[str]: ...

    def extract_source_files(self, editor_options: dict[str, object]): ...


class ImportedQuestionFactory:
    """Factory for mapping imported H5P package payloads to PythonQuestionBlock."""

    def __init__(
        self,
        *,
        courses_dir: Path,
        python_question_machine_name: str,
        normalize_whitespace: Callable[[str], str],
        strip_html: Callable[[str], str],
        import_mapper: H5PImportMapperProtocol,
    ) -> None:
        self._courses_dir = courses_dir
        self._python_question_machine_name = python_question_machine_name
        self._normalize_whitespace = normalize_whitespace
        self._strip_html = strip_html
        self._import_mapper = import_mapper

    def _summarize_questionset(self, content_payload: dict[str, object]) -> str:
        questions = content_payload.get("questions", [])
        if not isinstance(questions, list) or not questions:
            return "Importiertes Quiz aus Moodle."

        prompts: list[str] = []
        for entry in questions[:5]:
            if not isinstance(entry, dict):
                continue
            params = entry.get("params", {})
            if not isinstance(params, dict):
                continue
            prompt = self._normalize_whitespace(self._strip_html(str(params.get("question") or ""))).strip()
            if prompt:
                prompts.append(prompt)

        if not prompts:
            return f"Importiertes Quiz aus Moodle mit {len(questions)} Teilfragen."
        prompt_summary = " | ".join(prompts)
        return f"Importiertes Quiz aus Moodle mit {len(questions)} Teilfragen: {prompt_summary}"

    def create_from_h5p_package(
        self,
        *,
        course_slug: str,
        activity: MoodleH5PActivity,
        metadata_payload: dict[str, object],
        content_payload: dict[str, object],
    ) -> PythonQuestionBlock | None:
        main_library = str(metadata_payload.get("mainLibrary") or "").strip()
        content_type = str(content_payload.get("contentType") or "").strip()
        if not main_library:
            return None

        metadata_copy = json.loads(json.dumps(metadata_payload, ensure_ascii=False))
        content_copy = json.loads(json.dumps(content_payload, ensure_ascii=False))
        title = activity.title or str(metadata_payload.get("title") or activity.identifier)

        if main_library == "H5P.QuestionSet":
            return PythonQuestionBlock(
                identifier=activity.identifier,
                title=title,
                instructions=self._summarize_questionset(content_payload),
                preview_url=activity.url,
                main_library=main_library,
                package_url=getattr(activity, "package_url", ""),
                h5p_metadata=metadata_copy,
                h5p_content=content_copy,
                h5p_subdir=getattr(activity, "h5p_subdir", ""),
                runner=DEFAULT_PYTHON_RUNNER,
                course_slug=course_slug,
                course_dir=self._courses_dir / course_slug,
            )

        if main_library in ("H5P.JavaQuestion", "H5P.SQLQuestion", "H5P.AutomataQuestion"):
            editor_settings = content_payload.get("editorSettings", {})
            grading_settings = content_payload.get("gradingSettings", {})
            advanced_options = content_payload.get("advancedOptions", {})
            if not isinstance(editor_settings, dict):
                editor_settings = {}
            if not isinstance(grading_settings, dict):
                grading_settings = {}
            if not isinstance(advanced_options, dict):
                advanced_options = {}

            if main_library == "H5P.AutomataQuestion":
                solution_key = "solutionAutomaton"
                default_show_console = False
            else:
                solution_key = "solution"
                default_show_console = True

            return PythonQuestionBlock(
                identifier=activity.identifier,
                title=title,
                instructions=self._normalize_whitespace(
                    self._strip_html(str(editor_settings.get("instructions") or activity.intro or f"Importiert aus Moodle: {activity.title}"))
                ).strip() or f"Importiert aus Moodle: {activity.title}",
                preview_url=activity.url,
                main_library=main_library,
                package_url=getattr(activity, "package_url", ""),
                h5p_metadata=metadata_copy,
                h5p_content=content_copy,
                h5p_subdir=getattr(activity, "h5p_subdir", ""),
                runner=DEFAULT_PYTHON_RUNNER,
                starter_code=self._normalize_whitespace(html.unescape(str(editor_settings.get("startingCode") or ""))),
                solution_code=self._normalize_whitespace(html.unescape(str(grading_settings.get(solution_key) or ""))),
                grading_method=str(grading_settings.get("gradingMethod") or "please_choose"),
                show_console=bool(advanced_options.get("showConsole", default_show_console)),
                course_slug=course_slug,
                course_dir=self._courses_dir / course_slug,
            )

        if main_library != self._python_question_machine_name:
            return PythonQuestionBlock(
                identifier=activity.identifier,
                title=title,
                instructions=activity.intro or f"Importiert aus Moodle: {activity.title}",
                preview_url=activity.url,
                main_library=main_library,
                package_url=getattr(activity, "package_url", ""),
                raw_package=True,
                h5p_metadata=metadata_copy,
                h5p_content=content_copy,
                h5p_subdir=getattr(activity, "h5p_subdir", ""),
                runner=DEFAULT_PYTHON_RUNNER,
                course_slug=course_slug,
                course_dir=self._courses_dir / course_slug,
            )

        if content_type and content_type != "ide_only":
            return PythonQuestionBlock(
                identifier=activity.identifier,
                title=title,
                instructions=self._import_mapper.summarize_instructions(activity, content_payload),
                preview_url=activity.url,
                main_library=main_library,
                package_url=getattr(activity, "package_url", ""),
                h5p_metadata=metadata_copy,
                h5p_content=content_copy,
                h5p_subdir=getattr(activity, "h5p_subdir", ""),
                runner=str(content_payload.get("pythonRunner") or DEFAULT_PYTHON_RUNNER).strip() or DEFAULT_PYTHON_RUNNER,
                course_slug=course_slug,
                course_dir=self._courses_dir / course_slug,
            )

        editor_settings = content_payload.get("editorSettings", {})
        grading_settings = content_payload.get("gradingSettings", {})
        advanced_options = content_payload.get("advancedOptions", {})
        if not isinstance(editor_settings, dict) or not isinstance(grading_settings, dict):
            return None
        if not isinstance(advanced_options, dict):
            advanced_options = {}

        editor_options = editor_settings.get("options", {})
        if not isinstance(editor_options, dict):
            editor_options = {}

        test_cases: list[TestCase] = []
        for raw_test_case in grading_settings.get("testCases", []) or []:
            if not isinstance(raw_test_case, dict):
                continue
            test_cases.append(
                TestCase(
                    hidden=bool(raw_test_case.get("hidden", False)),
                    inputs=self._import_mapper.extract_test_case_values(raw_test_case.get("inputs", []), field_name="input"),
                    outputs=self._import_mapper.extract_test_case_values(raw_test_case.get("outputs", []), field_name="output"),
                )
            )

        return PythonQuestionBlock(
            identifier=activity.identifier,
            title=title,
            instructions=self._import_mapper.extract_editor_instructions(content_payload)
            or self._import_mapper.summarize_instructions(activity, content_payload),
            preview_url=activity.url,
            main_library=main_library,
            package_url=getattr(activity, "package_url", ""),
            h5p_metadata=metadata_copy,
            h5p_content=content_copy,
            h5p_subdir=getattr(activity, "h5p_subdir", ""),
            runner=str(content_payload.get("pythonRunner") or DEFAULT_PYTHON_RUNNER).strip() or DEFAULT_PYTHON_RUNNER,
            packages=self._import_mapper.extract_packages(content_payload),
            starter_code=self._normalize_whitespace(html.unescape(str(editor_settings.get("startingCode") or ""))),
            solution_code=self._normalize_whitespace(html.unescape(str(grading_settings.get("targetCode") or ""))),
            pre_code=self._normalize_whitespace(html.unescape(str(editor_settings.get("preCode") or ""))),
            post_code=self._normalize_whitespace(html.unescape(str(editor_settings.get("postCode") or ""))),
            grading_method=str(grading_settings.get("gradingMethod") or "please_choose"),
            show_console=bool(advanced_options.get("showConsole", True)),
            allow_adding_files=bool(editor_options.get("allowAddingFiles", False)),
            source_files=self._import_mapper.extract_source_files(editor_options),
            test_cases=test_cases,
            course_slug=course_slug,
            course_dir=self._courses_dir / course_slug,
        )
