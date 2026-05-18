from __future__ import annotations

import http.client
import json
import mimetypes
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Callable, Iterable
from urllib.parse import parse_qs, unquote, urlparse

from .preview_controller import PreviewController


@dataclass(frozen=True)
class PreviewHandlerContext:
    courses_dir: Path
    runtime_proxy_prefix: str
    h5p_runtime_port: int
    load_course_preview_state: Callable[[Path], tuple[list[object], str]]
    preview_controller: Callable[[], PreviewController]
    resolve_runtime_question_from_path: Callable[[str], object | None]
    ensure_runtime_question_ready: Callable[[object], None]
    ensure_h5p_runtime_server: Callable[[], object]
    rewrite_runtime_html: Callable[[str, str, str], str]
    escape_inline: Callable[[str], str]
    start_runtime_question_preparation: Callable[[object], None]
    rebuild_runtime_question: Callable[[object], None]
    template_renderer: Callable[[], object]


def build_course_preview_handler(context: PreviewHandlerContext) -> type[BaseHTTPRequestHandler]:
    class CoursePreviewHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            path = unquote(parsed.path)

            if path.startswith(f"{context.runtime_proxy_prefix}/"):
                self.proxy_runtime_request("GET", parsed)
                return

            if path in {"/", ""}:
                course_dirs = sorted([item for item in context.courses_dir.iterdir() if item.is_dir()])
                if not course_dirs:
                    self.send_error(HTTPStatus.NOT_FOUND, "Kein Kurs gefunden.")
                    return
                self.respond_html(context.template_renderer().render_index(course_dirs))
                return

            if path.startswith("/courses/"):
                parts = path.strip("/").split("/")
                if len(parts) != 2:
                    self.send_error(HTTPStatus.NOT_FOUND, "Unbekannter Kurs-Pfad.")
                    return
                course_dir = context.courses_dir / parts[1]
                if not course_dir.exists():
                    self.send_error(HTTPStatus.NOT_FOUND, "Kurs nicht gefunden.")
                    return
                questions, html_content = context.load_course_preview_state(course_dir)
                for question in questions:
                    context.start_runtime_question_preparation(question)
                self.respond_html(html_content)
                return

            if path.startswith("/preview-status/"):
                parts = path.strip("/").split("/")
                if len(parts) != 3:
                    self.send_error(HTTPStatus.NOT_FOUND, "Unbekannter Preview-Status-Pfad.")
                    return

                result = context.preview_controller().preview_status(parts[1], parts[2])
                if result.status_code != HTTPStatus.OK or result.payload is None:
                    self.send_error(result.status_code, result.error_message or "Unbekannter Fehler.")
                    return

                self.respond_json(result.payload)
                return

            if path.startswith("/preview/"):
                parts = path.strip("/").split("/")
                if len(parts) != 3:
                    self.send_error(HTTPStatus.NOT_FOUND, "Unbekannter Preview-Pfad.")
                    return

                query = parse_qs(parsed.query)
                mode = str(query.get("mode", ["view"])[0]).strip().lower() or "view"
                if mode not in {"view", "edit", "split"}:
                    mode = "view"
                simple = str(query.get("simple", [""])[0]).strip().lower() in {"1", "true", "yes", "on"}

                result = context.preview_controller().preview_route(parts[1], parts[2], mode=mode, simple=simple)
                if result.status_code == HTTPStatus.FOUND and result.redirect_url:
                    self.respond_redirect(result.redirect_url)
                    return
                if result.status_code == HTTPStatus.OK and result.waiting_page_html is not None:
                    self.respond_html(result.waiting_page_html)
                    return

                self.send_error(result.status_code, result.error_message or "Unbekannter Fehler.")
                return

            if path.startswith("/files/"):
                relative = path.removeprefix("/files/")
                file_path = context.courses_dir / relative
                if not file_path.exists() or not file_path.is_file():
                    self.send_error(HTTPStatus.NOT_FOUND, "Datei nicht gefunden.")
                    return
                self.respond_file(file_path)
                return

            self.send_error(HTTPStatus.NOT_FOUND, "Route nicht gefunden.")

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            path = unquote(parsed.path)
            if path.startswith(f"{context.runtime_proxy_prefix}/"):
                self.proxy_runtime_request("POST", parsed)
                return
            if path.startswith("/preview-rebuild/"):
                parts = path.strip("/").split("/")
                if len(parts) != 3:
                    self.send_error(HTTPStatus.NOT_FOUND, "Unbekannter Preview-Rebuild-Pfad.")
                    return

                result = context.preview_controller().rebuild_preview(parts[1], parts[2])
                if result.status_code != HTTPStatus.OK or result.payload is None:
                    self.send_error(result.status_code, result.error_message or "Unbekannter Fehler.")
                    return

                self.respond_json(result.payload)
                return
            self.send_error(HTTPStatus.NOT_FOUND, "Route nicht gefunden.")

        def do_DELETE(self) -> None:
            parsed = urlparse(self.path)
            path = unquote(parsed.path)
            if path.startswith(f"{context.runtime_proxy_prefix}/"):
                self.proxy_runtime_request("DELETE", parsed)
                return
            self.send_error(HTTPStatus.NOT_FOUND, "Route nicht gefunden.")

        def log_message(self, format: str, *args: object) -> None:
            return

        def proxy_runtime_request(self, method: str, parsed) -> None:
            context.ensure_h5p_runtime_server()

            runtime_path = parsed.path.removeprefix(context.runtime_proxy_prefix) or "/"
            question = context.resolve_runtime_question_from_path(runtime_path)
            if question is not None and method != "DELETE":
                context.ensure_runtime_question_ready(question)
            connection = http.client.HTTPConnection("127.0.0.1", context.h5p_runtime_port, timeout=30)
            body = self.read_request_body()
            headers = self.build_runtime_proxy_headers(body)
            target = runtime_path
            if parsed.query:
                target = f"{target}?{parsed.query}"

            try:
                connection.request(method, target, body=body, headers=headers)
                response = connection.getresponse()
                payload = response.read()
            except OSError as exc:
                self.send_error(HTTPStatus.BAD_GATEWAY, f"H5P-Runtime nicht erreichbar: {exc}")
                return
            finally:
                connection.close()

            self.send_response(response.status)
            for header, value in response.getheaders():
                lower = header.lower()
                if lower in {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers", "transfer-encoding", "upgrade"}:
                    continue
                if lower == "location" and value.startswith("/"):
                    value = f"{context.runtime_proxy_prefix}{value}"
                if lower == "content-length":
                    continue
                self.send_header(header, value)
            content_type = response.getheader("Content-Type", "")
            rewritten_payload = payload
            if "text/html" in content_type:
                document = payload.decode("utf-8")
                document = context.rewrite_runtime_html(document, runtime_path, parsed.query)
                rewritten_payload = document.encode("utf-8")
            self.send_header("Content-Length", str(len(rewritten_payload)))
            self.end_headers()
            self.wfile.write(rewritten_payload)

        def build_runtime_proxy_headers(self, body: bytes) -> dict[str, str]:
            headers: dict[str, str] = {}
            for header, value in self.headers.items():
                lower = header.lower()
                if lower in {"host", "connection", "content-length"}:
                    continue
                headers[header] = value
            if body:
                headers["Content-Length"] = str(len(body))
            return headers

        def read_request_body(self) -> bytes:
            length = self.headers.get("Content-Length")
            if not length:
                return b""
            return self.rfile.read(int(length))

        def respond_html(self, content: str) -> None:
            payload = content.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def respond_redirect(self, location: str) -> None:
            self.send_response(HTTPStatus.FOUND)
            self.send_header("Location", location)
            self.end_headers()

        def respond_json(self, payload: dict[str, str]) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def respond_file(self, file_path: Path) -> None:
            data = file_path.read_bytes()
            content_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    return CoursePreviewHandler
