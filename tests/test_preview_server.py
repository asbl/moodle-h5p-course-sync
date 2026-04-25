from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from scripts.classes.cli.preview_server import serve_preview


class PreviewServerTests(unittest.TestCase):
    @patch("scripts.classes.cli.preview_server.build_course_preview_handler")
    @patch("scripts.classes.cli.preview_server.ThreadingHTTPServer")
    def test_serve_preview_closes_server_without_runtime_process(
        self,
        server_cls: Mock,
        build_handler: Mock,
    ) -> None:
        server = Mock()
        server_cls.return_value = server
        build_handler.return_value = object()

        serve_preview(
            8877,
            courses_dir=Path("courses"),
            runtime_proxy_prefix="/runtime",
            h5p_runtime_port=8820,
            ensure_h5p_runtime_server=lambda: None,
            load_course_preview_state=lambda _course_dir: ([], ""),
            preview_controller=lambda *args, **kwargs: None,
            resolve_runtime_question_from_path=lambda _path: None,
            ensure_runtime_question_ready=lambda _question: None,
            rewrite_runtime_html=lambda document, _runtime_path, _query="": document,
            escape_inline=lambda value: value,
            start_runtime_question_preparation=lambda _question: None,
            prepare_preview_runtime=lambda: None,
            template_renderer=lambda: None,
        )

        server.serve_forever.assert_called_once_with()
        server.server_close.assert_called_once_with()

    @patch("scripts.classes.cli.preview_server.build_course_preview_handler")
    @patch("scripts.classes.cli.preview_server.ThreadingHTTPServer")
    def test_serve_preview_terminates_runtime_process_on_shutdown(
        self,
        server_cls: Mock,
        build_handler: Mock,
    ) -> None:
        server = Mock()
        server.serve_forever.side_effect = KeyboardInterrupt()
        server_cls.return_value = server
        build_handler.return_value = object()

        runtime_process = Mock()
        runtime_process.poll.return_value = None

        serve_preview(
            8877,
            courses_dir=Path("courses"),
            runtime_proxy_prefix="/runtime",
            h5p_runtime_port=8820,
            ensure_h5p_runtime_server=lambda: runtime_process,
            load_course_preview_state=lambda _course_dir: ([], ""),
            preview_controller=lambda *args, **kwargs: None,
            resolve_runtime_question_from_path=lambda _path: None,
            ensure_runtime_question_ready=lambda _question: None,
            rewrite_runtime_html=lambda document, _runtime_path, _query="": document,
            escape_inline=lambda value: value,
            start_runtime_question_preparation=lambda _question: None,
            prepare_preview_runtime=lambda: None,
            template_renderer=lambda: None,
        )

        server.server_close.assert_called_once_with()
        runtime_process.terminate.assert_called_once_with()
        runtime_process.wait.assert_called_once_with(timeout=5)

    @patch("scripts.classes.cli.preview_server.build_course_preview_handler")
    @patch("scripts.classes.cli.preview_server.ThreadingHTTPServer")
    def test_serve_preview_raises_when_runtime_process_already_exited(
        self,
        server_cls: Mock,
        build_handler: Mock,
    ) -> None:
        runtime_process = Mock()
        runtime_process.poll.return_value = 1

        with self.assertRaisesRegex(RuntimeError, "H5P-Runtime ist unerwartet beendet"):
            serve_preview(
                8877,
                courses_dir=Path("courses"),
                runtime_proxy_prefix="/runtime",
                h5p_runtime_port=8820,
                ensure_h5p_runtime_server=lambda: runtime_process,
                load_course_preview_state=lambda _course_dir: ([], ""),
                preview_controller=lambda *args, **kwargs: None,
                resolve_runtime_question_from_path=lambda _path: None,
                ensure_runtime_question_ready=lambda _question: None,
                rewrite_runtime_html=lambda document, _runtime_path, _query="": document,
                escape_inline=lambda value: value,
                start_runtime_question_preparation=lambda _question: None,
                prepare_preview_runtime=lambda: None,
                template_renderer=lambda: None,
            )

        server_cls.assert_not_called()
        build_handler.assert_not_called()

    @patch("scripts.classes.cli.preview_server.build_course_preview_handler")
    @patch("scripts.classes.cli.preview_server.ThreadingHTTPServer")
    def test_serve_preview_prepares_runtime_before_serving(
        self,
        server_cls: Mock,
        build_handler: Mock,
    ) -> None:
        server = Mock()
        server_cls.return_value = server
        build_handler.return_value = object()
        calls: list[str] = []

        serve_preview(
            8877,
            courses_dir=Path("courses"),
            runtime_proxy_prefix="/runtime",
            h5p_runtime_port=8820,
            ensure_h5p_runtime_server=lambda: calls.append("runtime") or None,
            load_course_preview_state=lambda _course_dir: ([], ""),
            preview_controller=lambda *args, **kwargs: None,
            resolve_runtime_question_from_path=lambda _path: None,
            ensure_runtime_question_ready=lambda _question: None,
            rewrite_runtime_html=lambda document, _runtime_path, _query="": document,
            escape_inline=lambda value: value,
            start_runtime_question_preparation=lambda _question: None,
            prepare_preview_runtime=lambda: calls.append("prepare"),
            template_renderer=lambda: None,
        )

        self.assertEqual(calls, ["runtime", "prepare"])
        server.serve_forever.assert_called_once_with()
