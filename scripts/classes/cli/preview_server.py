from __future__ import annotations

import subprocess
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Protocol

from scripts.classes.preview_controller import PreviewController


class QuestionLike(Protocol):
    identifier: str


class TemplateRendererLike(Protocol):
    def render_index(self, course_dirs: list[Path]) -> str: ...

from scripts.classes.preview_http_handler import (
    PreviewHandlerContext,
    build_course_preview_handler,
)


def serve_preview(
    port: int,
    *,
    courses_dir: Path,
    runtime_proxy_prefix: str,
    h5p_runtime_port: int,
    ensure_h5p_runtime_server: Callable[[], subprocess.Popen[str] | None],
    load_course_preview_state: Callable[[Path], tuple[list[QuestionLike], str]],
    preview_controller: Callable[[], PreviewController],
    resolve_runtime_question_from_path: Callable[[str], QuestionLike | None],
    ensure_runtime_question_ready: Callable[[QuestionLike], None],
    rewrite_runtime_html: Callable[[str, str, str], str],
    escape_inline: Callable[[str], str],
    start_runtime_question_preparation: Callable[[QuestionLike], None],
    prepare_preview_runtime: Callable[[], None],
    template_renderer: Callable[[], TemplateRendererLike],
    rebuild_runtime_question: Callable[[QuestionLike], None] | None = None,
) -> None:
    print(
        "Preview wird vorbereitet. Der Server ist erst nach Abschluss der H5P-Vorbereitung erreichbar.",
        flush=True,
    )
    runtime_process = ensure_h5p_runtime_server()
    server: ThreadingHTTPServer | None = None
    try:
        if runtime_process is not None:
            exit_code = runtime_process.poll()
            if exit_code is not None:
                raise RuntimeError(f"H5P-Runtime ist unerwartet beendet (Exit-Code {exit_code}).")

        prepare_preview_runtime()

        handler = build_course_preview_handler(
            PreviewHandlerContext(
                courses_dir=courses_dir,
                runtime_proxy_prefix=runtime_proxy_prefix,
                h5p_runtime_port=h5p_runtime_port,
                load_course_preview_state=load_course_preview_state,
                preview_controller=preview_controller,
                resolve_runtime_question_from_path=resolve_runtime_question_from_path,
                ensure_runtime_question_ready=ensure_runtime_question_ready,
                ensure_h5p_runtime_server=ensure_h5p_runtime_server,
                rewrite_runtime_html=rewrite_runtime_html,
                escape_inline=escape_inline,
                start_runtime_question_preparation=start_runtime_question_preparation,
                rebuild_runtime_question=rebuild_runtime_question or (lambda _question: None),
                template_renderer=template_renderer,
            )
        )

        server = ThreadingHTTPServer(("127.0.0.1", port), handler)
        print(f"Preview läuft auf http://127.0.0.1:{port}", flush=True)
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if server is not None:
            server.server_close()
        if runtime_process is not None:
            try:
                runtime_process.terminate()
                runtime_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                runtime_process.kill()
                runtime_process.wait(timeout=5)
            except ProcessLookupError:
                pass
