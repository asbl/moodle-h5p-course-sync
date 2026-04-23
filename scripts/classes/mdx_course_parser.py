from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Callable, Pattern

from .models import PythonQuestionBlock, SourceFile, TestCase


class MdxCourseParser:
    """Parses MDX course files into strongly typed question blocks."""

    def __init__(
        self,
        *,
        tag_re: Pattern[str],
        fence_re: Pattern[str],
        placeholder_template: str,
        python_question_machine_name: str,
        parse_jsx_expression: Callable[[str], object],
        normalize_whitespace: Callable[[str], str],
        infer_source_package_sidecar_path: Callable[[PythonQuestionBlock], str],
        build_imported_question_from_sidecar: Callable[[Path, str, str], PythonQuestionBlock | None],
        load_h5p_sidecar_file: Callable[[Path, str], dict[str, object]],
        apply_editable_h5p_payload: Callable[[PythonQuestionBlock, dict[str, object]], None],
    ) -> None:
        self._tag_re = tag_re
        self._fence_re = fence_re
        self._placeholder_template = placeholder_template
        self._python_question_machine_name = python_question_machine_name
        self._parse_jsx_expression = parse_jsx_expression
        self._normalize_whitespace = normalize_whitespace
        self._infer_source_package_sidecar_path = infer_source_package_sidecar_path
        self._build_imported_question_from_sidecar = build_imported_question_from_sidecar
        self._load_h5p_sidecar_file = load_h5p_sidecar_file
        self._apply_editable_h5p_payload = apply_editable_h5p_payload

    def parse_bool(self, value: str, default: bool = False) -> bool:
        if value == "":
            return default
        return value.strip().lower() in {"1", "true", "yes", "on"}

    def split_csv(self, value: str) -> list[str]:
        return [item.strip() for item in value.split(",") if item.strip()]

    def parse_braced_attribute(self, raw: str, start_index: int) -> tuple[str, int]:
        depth = 0
        in_string = False
        in_template = False
        escaped = False
        for index in range(start_index, len(raw)):
            char = raw[index]
            if in_template:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == "`":
                    in_template = False
                continue
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == "`":
                in_template = True
                continue
            if char == '"':
                in_string = True
                continue
            if char == "{":
                depth += 1
                continue
            if char == "}":
                depth -= 1
                if depth == 0:
                    return raw[start_index:index + 1], index + 1
        raise ValueError("Unvollstaendiger JSX-Ausdruck im PythonQuestion-Tag.")

    def parse_tag_attributes(self, raw_attrs: str) -> dict[str, object]:
        attrs: dict[str, object] = {}
        index = 0
        while index < len(raw_attrs):
            while index < len(raw_attrs) and raw_attrs[index].isspace():
                index += 1
            if index >= len(raw_attrs):
                break

            key_match = re.match(r"([A-Za-z_:][A-Za-z0-9_:-]*)", raw_attrs[index:])
            if key_match is None:
                index += 1
                continue
            key = key_match.group(1)
            index += len(key)
            while index < len(raw_attrs) and raw_attrs[index].isspace():
                index += 1
            if index >= len(raw_attrs) or raw_attrs[index] != "=":
                attrs[key] = ""
                continue
            index += 1
            while index < len(raw_attrs) and raw_attrs[index].isspace():
                index += 1
            if index >= len(raw_attrs):
                break

            if raw_attrs[index] == '"':
                index += 1
                value_start = index
                while index < len(raw_attrs):
                    if raw_attrs[index] == '"' and raw_attrs[index - 1] != "\\":
                        break
                    index += 1
                attrs[key] = html.unescape(raw_attrs[value_start:index].strip())
                index += 1
                continue

            if raw_attrs[index] == "{":
                expression, index = self.parse_braced_attribute(raw_attrs, index)
                attrs[key] = self._parse_jsx_expression(expression[1:-1].strip())
                continue

            value_start = index
            while index < len(raw_attrs) and not raw_attrs[index].isspace():
                index += 1
            attrs[key] = raw_attrs[value_start:index]
        return attrs

    def build_question_from_attrs(self, course_dir: Path, attrs: dict[str, object]) -> PythonQuestionBlock:
        course_slug = course_dir.name
        identifier = str(attrs.get("identifier", "")).strip()
        if not identifier:
            raise ValueError("PythonQuestion benötigt ein identifier-Attribut.")

        title = str(attrs.get("title", identifier))
        instructions = str(attrs.get("instructions", ""))
        preview_url = str(attrs.get("previewUrl", attrs.get("preview-url", "")))
        main_library = str(
            attrs.get("h5pLibrary", attrs.get("h5p-library", self._python_question_machine_name))
        ).strip() or self._python_question_machine_name
        package_url = str(attrs.get("packageUrl", attrs.get("package-url", ""))).strip()
        raw_package = self.parse_bool(str(attrs.get("rawPackage", attrs.get("raw-package", "false"))), default=False)
        h5p_metadata_path = str(attrs.get("h5pMetadataPath", attrs.get("h5p-metadata-path", ""))).strip()
        h5p_content_path = str(attrs.get("h5pContentPath", attrs.get("h5p-content-path", ""))).strip()
        source_package_path = str(attrs.get("sourcePackagePath", attrs.get("source-package-path", ""))).strip()
        runner = str(attrs.get("runner", "pyodide")).strip() or "pyodide"
        grading_method = str(attrs.get("gradingMethod", attrs.get("grading-method", "please_choose")))
        packages = self.split_csv(str(attrs.get("packages", "")))
        show_console = self.parse_bool(str(attrs.get("showConsole", "true")), default=True)
        allow_adding_files = self.parse_bool(str(attrs.get("allowAddingFiles", "false")), default=False)
        editable_h5p_payload = attrs.get("h5p")

        if not source_package_path:
            source_package_path = self._infer_source_package_sidecar_path(
                PythonQuestionBlock(
                    identifier=identifier,
                    title=identifier,
                    instructions="",
                    course_slug=course_slug,
                    course_dir=course_dir,
                )
            )

        question = (
            self._build_imported_question_from_sidecar(course_dir, identifier, source_package_path)
            if source_package_path
            else None
        )

        if question is None:
            question = PythonQuestionBlock(
                identifier=identifier,
                title=title,
                instructions=instructions,
                preview_url=preview_url,
                main_library=main_library,
                package_url=package_url,
                raw_package=raw_package,
                h5p_metadata_path=h5p_metadata_path,
                h5p_content_path=h5p_content_path,
                source_package_path=source_package_path,
                runner=runner,
                packages=packages,
                grading_method=grading_method,
                show_console=show_console,
                allow_adding_files=allow_adding_files,
                course_slug=course_slug,
                course_dir=course_dir,
            )

        if "title" in attrs:
            question.title = title
        if "instructions" in attrs:
            question.instructions = instructions
        if "previewUrl" in attrs or "preview-url" in attrs:
            question.preview_url = preview_url
        if "h5pLibrary" in attrs or "h5p-library" in attrs:
            question.main_library = main_library
        if "packageUrl" in attrs or "package-url" in attrs:
            question.package_url = package_url
        if "rawPackage" in attrs or "raw-package" in attrs:
            question.raw_package = raw_package
        if source_package_path:
            question.source_package_path = source_package_path
        if "runner" in attrs:
            question.runner = runner
        if "packages" in attrs:
            question.packages = packages
        if "gradingMethod" in attrs or "grading-method" in attrs:
            question.grading_method = grading_method
        if "showConsole" in attrs:
            question.show_console = show_console
        if "allowAddingFiles" in attrs:
            question.allow_adding_files = allow_adding_files

        if h5p_metadata_path:
            question.h5p_metadata = self._load_h5p_sidecar_file(
                question.course_dir,
                h5p_metadata_path,
            )
        if h5p_content_path:
            question.h5p_content = self._load_h5p_sidecar_file(
                question.course_dir,
                h5p_content_path,
            )
        if isinstance(editable_h5p_payload, dict):
            self._apply_editable_h5p_payload(question, editable_h5p_payload)
        return question

    def parse_test_case(self, raw: str) -> TestCase:
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("Testfall muss ein JSON-Objekt sein.")

        inputs = payload.get("inputs", []) or []
        outputs = payload.get("outputs", []) or []

        return TestCase(
            hidden=bool(payload.get("hidden", False)),
            inputs=[str(item) for item in inputs],
            outputs=[str(item) for item in outputs],
        )

    def parse_json_object(self, raw: str, *, description: str) -> dict[str, object]:
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError(f"{description} muss ein JSON-Objekt sein.")
        return payload

    def parse_source_file(self, spec_parts: list[str], body: str) -> SourceFile:
        file_token = next((part for part in spec_parts if part.startswith("file:")), "")
        if not file_token:
            raise ValueError("Datei-Codeblock benötigt ein file:NAME.py-Token.")

        file_name = file_token.split(":", 1)[1].strip()
        if not file_name:
            raise ValueError("Datei-Codeblock benötigt einen Dateinamen.")

        visible = True
        editable = True
        for part in spec_parts:
            if part == "hidden-file":
                visible = False
            if part == "readonly-file":
                editable = False

        return SourceFile(
            file_name=file_name,
            code=self._normalize_whitespace(body),
            visible_to_learner=visible,
            learner_editable=editable,
        )

    def parse_course(self, course_dir: Path) -> tuple[str, list[PythonQuestionBlock], str]:
        mdx_path = course_dir / "index.mdx"
        source = mdx_path.read_text(encoding="utf-8")

        questions: dict[str, PythonQuestionBlock] = {}
        rendered_source = source

        for match in self._tag_re.finditer(source):
            attrs = self.parse_tag_attributes(match.group("attrs"))
            question = self.build_question_from_attrs(course_dir, attrs)
            question.course_dir = course_dir
            if question.identifier in questions:
                raise ValueError(
                    f"PythonQuestion-Identifier '{question.identifier}' ist in {mdx_path} mehrfach vergeben."
                )
            questions[question.identifier] = question
            rendered_source = rendered_source.replace(
                match.group(0),
                self._placeholder_template.format(identifier=question.identifier),
                1,
            )

        for fence in self._fence_re.finditer(source):
            spec = fence.group("spec").strip()
            if not spec:
                continue

            spec_parts = spec.split()
            if len(spec_parts) < 3 or not spec_parts[1].startswith("question:"):
                continue

            identifier = spec_parts[1].split(":", 1)[1].strip()
            question = questions.get(identifier)
            if question is None:
                raise ValueError(f"Codeblock referenziert unbekannte PythonQuestion '{identifier}'.")

            role = spec_parts[2]
            body = self._normalize_whitespace(fence.group("body"))

            if role == "starter":
                question.starter_code = body
            elif role == "solution":
                question.solution_code = body
            elif role == "pre":
                question.pre_code = body
            elif role == "post":
                question.post_code = body
            elif role == "testcase":
                question.test_cases.append(self.parse_test_case(body))
            elif role == "h5p-metadata":
                question.h5p_metadata = self.parse_json_object(body, description="H5P-Metadaten")
            elif role == "h5p-content":
                question.h5p_content = self.parse_json_object(body, description="H5P-Content")
            elif role == "h5p":
                self._apply_editable_h5p_payload(
                    question,
                    self.parse_json_object(body, description="H5P-Daten"),
                )
            elif role.startswith("file:"):
                question.source_files.append(self.parse_source_file(spec_parts[2:], fence.group("body")))
            else:
                raise ValueError(f"Unbekannte PythonQuestion-Rolle '{role}' in {mdx_path}.")

        def strip_question_fence(match: re.Match[str]) -> str:
            spec = match.group("spec").strip()
            spec_parts = spec.split()
            if len(spec_parts) >= 2 and spec_parts[1].startswith("question:"):
                return ""
            return match.group(0)

        rendered_source = self._fence_re.sub(strip_question_fence, rendered_source)

        return source, list(questions.values()), rendered_source
