from __future__ import annotations

import subprocess
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Callable

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
    load_course_preview_state: Callable,
    preview_controller: Callable,
    resolve_runtime_question_from_path: Callable,
    ensure_runtime_question_ready: Callable,
    rewrite_runtime_html: Callable,
    escape_inline: Callable,
    start_runtime_question_preparation: Callable,
    template_renderer: Callable,
) -> None:
    runtime_process = ensure_h5p_runtime_server()
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
            template_renderer=template_renderer,
        )
    )
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    print(f"Preview läuft auf http://127.0.0.1:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        if runtime_process is not None:
            runtime_process.terminate()
            runtime_process.wait(timeout=5)
