# Unit tests for ComponentSyncer.
#
# Covers:
#   - apply_editable_h5p_payload (3 branches: PythonQuestion flat, source-package, fallback)
#   - HTML escaping applied to payloads
#   - build_editable_h5p_payload (empty guard)
#   - _build_default_python_question_content (field mappings)
#   - _build_default_imported_h5p_metadata (error handling)
#   - normalize_template_literal (escape sequences)
#   - jsx_expression_to_json / parse_jsx_expression

from __future__ import annotations

import json
import unittest

from scripts.classes.component_sync.component_syncer import ComponentSyncer
from scripts.classes.models import PythonQuestionBlock

MACHINE_NAME = "H5P.PythonQuestion"

# Minimal semantics stub that mirrors the real PythonQuestion schema structure.
MINIMAL_SEMANTICS: list[dict[str, object]] = [
    {"name": "pythonRunner", "type": "select", "default": "pyodide"},
    {
        "name": "advancedOptions",
        "type": "group",
        "fields": [
            {"name": "showConsole", "type": "boolean", "default": True},
        ],
    },
    {
        "name": "pyodideOptions",
        "type": "group",
        "fields": [
            {"name": "packages", "type": "list"},
        ],
    },
    {
        "name": "editorSettings",
        "type": "group",
        "fields": [
            {"name": "instructions", "type": "text", "default": ""},
            {
                "name": "options",
                "type": "group",
                "fields": [
                    {"name": "allowAddingFiles", "type": "boolean", "default": False},
                ],
            },
        ],
    },
    {
        "name": "gradingSettings",
        "type": "group",
        "fields": [
            {"name": "gradingMethod", "type": "select", "default": "please_choose"},
        ],
    },
]


def _make_syncer(
    source_payload: tuple[dict, dict] | None = None,
    h5p_metadata: dict | None = None,
    raise_on_build_metadata: bool = False,
) -> ComponentSyncer:
    def _load_source(q: PythonQuestionBlock) -> tuple[dict, dict] | None:
        return source_payload

    def _build_metadata(q: PythonQuestionBlock) -> dict:
        if raise_on_build_metadata:
            raise FileNotFoundError("no metadata")
        return dict(h5p_metadata) if h5p_metadata else {"author": "test"}

    return ComponentSyncer(
        python_question_machine_name=MACHINE_NAME,
        load_python_question_semantics=lambda: MINIMAL_SEMANTICS,
        load_h5p_payload_from_source_package=_load_source,
        build_h5p_metadata=_build_metadata,
    )


def _make_question(**kwargs: object) -> PythonQuestionBlock:
    defaults: dict[str, object] = dict(
        identifier="test-q",
        title="Test Frage",
        instructions="Schreibe etwas.",
        main_library=MACHINE_NAME,
        runner="pyodide",
        packages=[],
        grading_method="ioTestCases",
        show_console=True,
        allow_adding_files=False,
    )
    defaults.update(kwargs)
    return PythonQuestionBlock(**defaults)  # type: ignore[arg-type]


class TestBuildDefaultPythonQuestionContent(unittest.TestCase):
    # _build_default_python_question_content field mapping

    def test_runner_overrides_semantic_default(self) -> None:
        syncer = _make_syncer()
        q = _make_question(runner="skulpt")
        content = syncer._build_default_python_question_content(q)
        self.assertEqual(content["pythonRunner"], "skulpt")

    def test_packages_mapped_to_h5p_object_format(self) -> None:
        syncer = _make_syncer()
        q = _make_question(packages=["miniworlds", "numpy"])
        content = syncer._build_default_python_question_content(q)
        # miniworlds must be emitted as an H5P package object with remote:false
        # so that the PythonRunner uses the locally-bundled version (not the CDN).
        # sqlite3 is no longer auto-added.
        self.assertEqual(
            content["pyodideOptions"]["packages"],  # type: ignore[index]
            [{"package": "miniworlds", "remote": False}, "numpy"],
        )

    def test_miniworlds_extensions_include_core_and_use_distribution_names(self) -> None:
        syncer = _make_syncer()
        q = _make_question(packages=["miniworlds_robot", "miniworlds_turtle"])

        content = syncer._build_default_python_question_content(q)

        self.assertEqual(
            content["pyodideOptions"]["packages"],  # type: ignore[index]
            [
                "miniworlds-robot",
                "miniworlds-turtle",
                {"package": "miniworlds", "remote": False},
            ],
        )

    def test_show_console_propagated(self) -> None:
        syncer = _make_syncer()
        q = _make_question(show_console=False)
        content = syncer._build_default_python_question_content(q)
        self.assertFalse(content["advancedOptions"]["showConsole"])  # type: ignore[index]

    def test_grading_method_propagated(self) -> None:
        syncer = _make_syncer()
        q = _make_question(grading_method="manual_grading")
        content = syncer._build_default_python_question_content(q)
        self.assertEqual(
            content["gradingSettings"]["gradingMethod"],  # type: ignore[index]
            "manual_grading",
        )

    def test_instructions_propagated(self) -> None:
        syncer = _make_syncer()
        q = _make_question(instructions="Erklaere Variablen.")
        content = syncer._build_default_python_question_content(q)
        self.assertEqual(
            content["editorSettings"]["instructions"],  # type: ignore[index]
            "Erklaere Variablen.",
        )

    def test_allow_adding_files_propagated(self) -> None:
        syncer = _make_syncer()
        q = _make_question(allow_adding_files=True)
        content = syncer._build_default_python_question_content(q)
        self.assertTrue(
            content["editorSettings"]["options"]["allowAddingFiles"]  # type: ignore[index]
        )


class TestBuildDefaultImportedH5PMetadata(unittest.TestCase):
    # _build_default_imported_h5p_metadata error handling

    def test_returns_empty_dict_when_build_h5p_metadata_raises(self) -> None:
        syncer = _make_syncer(raise_on_build_metadata=True)
        q = _make_question()
        result = syncer._build_default_imported_h5p_metadata(q)
        self.assertEqual(result, {})

    def test_strips_title_and_main_library(self) -> None:
        syncer = _make_syncer(
            h5p_metadata={"title": "Alt", "mainLibrary": "H5P.OldLib", "author": "Hans"}
        )
        q = _make_question()
        result = syncer._build_default_imported_h5p_metadata(q)
        self.assertNotIn("title", result)
        self.assertNotIn("mainLibrary", result)
        self.assertEqual(result["author"], "Hans")


class TestApplyEditableH5PPayload(unittest.TestCase):
    # apply_editable_h5p_payload - all three branches

    # Branch 1: PythonQuestion flat payload (no "metadata"/"content" keys)

    def test_python_question_flat_sets_title_and_main_library_on_metadata(self) -> None:
        syncer = _make_syncer()
        q = _make_question()
        syncer.apply_editable_h5p_payload(q, {})
        self.assertIsNotNone(q.h5p_metadata)
        assert q.h5p_metadata is not None
        self.assertEqual(q.h5p_metadata["title"], "Test Frage")
        self.assertEqual(q.h5p_metadata["mainLibrary"], MACHINE_NAME)

    def test_python_question_flat_payload_values_override_defaults(self) -> None:
        syncer = _make_syncer()
        q = _make_question(runner="pyodide")
        syncer.apply_editable_h5p_payload(q, {"pythonRunner": "skulpt"})
        assert q.h5p_content is not None
        self.assertEqual(q.h5p_content["pythonRunner"], "skulpt")

    def test_python_question_flat_html_in_payload_is_escaped(self) -> None:
        syncer = _make_syncer()
        q = _make_question()
        syncer.apply_editable_h5p_payload(
            q,
            {"editorSettings": {"instructions": "<script>alert(1)</script>"}},
        )
        assert q.h5p_content is not None
        instructions = q.h5p_content["editorSettings"]["instructions"]  # type: ignore[index]
        self.assertIn("&lt;script&gt;", instructions)
        self.assertNotIn("<script>", instructions)

    def test_python_question_stays_in_flat_branch_even_with_source_payload(self) -> None:
        # Even if source payload is available, flat PQ without metadata/content key uses branch 1
        syncer = _make_syncer(source_payload=({"author": "src"}, {"key": "val"}))
        q = _make_question()  # main_library == MACHINE_NAME
        syncer.apply_editable_h5p_payload(q, {"pythonRunner": "skulpt"})
        assert q.h5p_content is not None
        self.assertNotIn("key", q.h5p_content)

    # Branch 2: source package payload

    def test_source_package_branch_merges_overrides(self) -> None:
        src_meta = {"author": "original", "license": "CC"}
        src_content = {"task": "old task", "extra": "keep"}
        syncer = _make_syncer(source_payload=(src_meta, src_content))
        q = _make_question(main_library="H5P.QuestionSet", title="Neuer Titel")
        syncer.apply_editable_h5p_payload(
            q,
            {"metadata": {"author": "overridden"}, "content": {"task": "new task"}},
        )
        assert q.h5p_metadata is not None and q.h5p_content is not None
        self.assertEqual(q.h5p_metadata["author"], "overridden")
        self.assertEqual(q.h5p_metadata["license"], "CC")
        self.assertEqual(q.h5p_content["task"], "new task")
        self.assertEqual(q.h5p_content["extra"], "keep")

    def test_source_package_branch_strips_and_resets_title_and_main_library(self) -> None:
        src_meta = {"title": "Alter Titel", "mainLibrary": "H5P.OldLib", "author": "X"}
        src_content: dict[str, object] = {}
        syncer = _make_syncer(source_payload=(src_meta, src_content))
        q = _make_question(main_library="H5P.QuestionSet", title="Neuer Titel")
        syncer.apply_editable_h5p_payload(q, {"metadata": {}, "content": {}})
        assert q.h5p_metadata is not None
        self.assertEqual(q.h5p_metadata["title"], "Neuer Titel")
        self.assertEqual(q.h5p_metadata["mainLibrary"], "H5P.QuestionSet")

    def test_source_package_branch_raises_on_non_dict_metadata_override(self) -> None:
        syncer = _make_syncer(source_payload=({"a": 1}, {"b": 2}))
        q = _make_question(main_library="H5P.QuestionSet")
        with self.assertRaises(ValueError):
            syncer.apply_editable_h5p_payload(q, {"metadata": "nicht-dict", "content": {}})

    # Branch 3: fallback (no source payload)

    def test_fallback_branch_non_python_content_is_empty(self) -> None:
        syncer = _make_syncer(source_payload=None)
        q = _make_question(main_library="H5P.QuestionSet")
        syncer.apply_editable_h5p_payload(q, {"metadata": {}, "content": {}})
        assert q.h5p_content is not None
        self.assertEqual(q.h5p_content, {})

    def test_fallback_branch_python_question_sets_runner_default(self) -> None:
        # PythonQuestion with metadata/content keys and no source -> sets pythonRunner default
        syncer = _make_syncer(source_payload=None)
        q = _make_question(runner="skulpt")
        syncer.apply_editable_h5p_payload(q, {"metadata": {}, "content": {}})
        assert q.h5p_content is not None
        self.assertEqual(q.h5p_content.get("pythonRunner"), "skulpt")

    def test_fallback_branch_sets_title_and_main_library(self) -> None:
        syncer = _make_syncer(source_payload=None)
        q = _make_question(main_library="H5P.QuestionSet", title="Quiz Titel")
        syncer.apply_editable_h5p_payload(q, {"metadata": {}, "content": {}})
        assert q.h5p_metadata is not None
        self.assertEqual(q.h5p_metadata["title"], "Quiz Titel")
        self.assertEqual(q.h5p_metadata["mainLibrary"], "H5P.QuestionSet")

    def test_fallback_branch_raises_on_non_dict_content_override(self) -> None:
        syncer = _make_syncer(source_payload=None)
        q = _make_question(main_library="H5P.QuestionSet")
        with self.assertRaises(ValueError):
            syncer.apply_editable_h5p_payload(q, {"metadata": {}, "content": ["kein-dict"]})


class TestBuildEditableH5PPayload(unittest.TestCase):
    # build_editable_h5p_payload guard condition

    def test_returns_empty_dict_when_no_h5p_data(self) -> None:
        syncer = _make_syncer()
        q = _make_question()
        # Defaults: h5p_metadata=None, h5p_content=None
        self.assertEqual(syncer.build_editable_h5p_payload(q), {})

    def test_returns_empty_dict_when_only_metadata_missing(self) -> None:
        syncer = _make_syncer()
        q = _make_question()
        q.h5p_content = {"pythonRunner": "pyodide"}
        self.assertEqual(syncer.build_editable_h5p_payload(q), {})


class TestNormalizeTemplateLiteral(unittest.TestCase):
    # normalize_template_literal escape handling

    def setUp(self) -> None:
        self.syncer = _make_syncer()

    def test_strips_leading_newline(self) -> None:
        result = self.syncer.normalize_template_literal("\nhello")
        self.assertFalse(result.startswith("\n"))
        self.assertEqual(result, "hello")

    def test_dedents(self) -> None:
        result = self.syncer.normalize_template_literal("\n    line1\n    line2\n")
        self.assertNotIn("    ", result)
        self.assertIn("line1", result)

    def test_unescapes_backtick(self) -> None:
        result = self.syncer.normalize_template_literal("x = \\`hello\\`")
        self.assertEqual(result, "x = `hello`")

    def test_unescapes_template_expression(self) -> None:
        result = self.syncer.normalize_template_literal("a = \\${x + 1}")
        self.assertEqual(result, "a = ${x + 1}")

    def test_unescapes_newline_and_tab(self) -> None:
        result = self.syncer.normalize_template_literal("a\\nb\\tc")
        self.assertEqual(result, "a\nb\tc")

    def test_unescapes_backslash(self) -> None:
        # "\\\\x" in Python source = the two-char string "\\x" (double-backslash + x).
        # normalize_template_literal replaces "\\" with "\", yielding "\x" (single-backslash + x).
        result = self.syncer.normalize_template_literal("\\\\x")
        self.assertEqual(result, "\\x")


class TestJsxExpressionToJson(unittest.TestCase):
    # jsx_expression_to_json / parse_jsx_expression

    def setUp(self) -> None:
        self.syncer = _make_syncer()

    def test_backtick_string_converted_to_json_string(self) -> None:
        result = self.syncer.jsx_expression_to_json('{"code": `hello world`}')
        self.assertEqual(result, '{"code": "hello world"}')

    def test_multiline_template_literal(self) -> None:
        result = self.syncer.jsx_expression_to_json('{"code": `line1\nline2`}')
        parsed = json.loads(result)
        self.assertIn("line1", parsed["code"])

    def test_regular_double_quoted_string_unchanged(self) -> None:
        result = self.syncer.jsx_expression_to_json('{"key": "value"}')
        self.assertEqual(result, '{"key": "value"}')

    def test_mixed_template_and_regular_string(self) -> None:
        result = self.syncer.jsx_expression_to_json('{"a": `tmpl`, "b": "reg"}')
        parsed = json.loads(result)
        self.assertEqual(parsed["a"], "tmpl")
        self.assertEqual(parsed["b"], "reg")

    def test_raises_on_unclosed_template_literal(self) -> None:
        with self.assertRaises(ValueError):
            self.syncer.jsx_expression_to_json('{"code": `unclosed}')

    def test_escaped_backtick_inside_template_preserved(self) -> None:
        result = self.syncer.jsx_expression_to_json('{' + '"code": `x = \\`y\\``' + '}')
        parsed = json.loads(result)
        self.assertIn("`", parsed["code"])

    def test_parse_jsx_expression_returns_python_object(self) -> None:
        parsed = self.syncer.parse_jsx_expression('{"runner": `pyodide`}')
        self.assertIsInstance(parsed, dict)
        self.assertEqual(parsed["runner"], "pyodide")  # type: ignore[index]


if __name__ == "__main__":
    unittest.main()
