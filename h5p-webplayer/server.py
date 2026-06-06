from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import socket
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urlsplit
from urllib.request import Request, urlopen
from zipfile import ZipFile


ROOT_DIR = Path(__file__).resolve().parents[1]
RUNTIME_DIR = ROOT_DIR / ".h5p-runtime"
RUNTIME_CONTENT_DIR = RUNTIME_DIR / "content"
DEFAULT_PACKAGE = ROOT_DIR / "courses" / "h5p-demo" / "build" / "h5p" / "004-python-tests" / "python-tests.h5p"
SERVER_CONFIG = """\
module.exports = {
  port: 8080,
  mediaTypes: ['images', 'audios', 'videos'],
  folders: {
    assets: 'assets',
    libraries: 'libraries',
    temp: 'temp'
  },
  files: {
    watch: false,
    watchExclusions: [/node_modules\\//],
    patterns: {
      allowed: /\\.(json|png|jpg|jpeg|gif|bmp|tif|tiff|eot|ttf|woff|woff2|otf|webm|mp4|ogg|mp3|m4a|wav|txt|pdf|rtf|doc|docx|xls|xlsx|ppt|pptx|odt|ods|odp|csv|diff|patch|swf|md|textile|vtt|webvtt|gltf|glb|js|css|svg|xml)$/,
      ignored: /^\\.|~$/gi
    }
  },
  registry: 'libraryRegistry.json'
};
if (process.argv[3] && process.argv[2] === 'server') {
  module.exports.port = +process.argv[3];
}
module.exports.api = `http://localhost:${module.exports.port}`;
"""


def slugify(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip()).strip("-").lower()
    return slug or "h5p-content"


def is_port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def wait_for_port(port: int, timeout_seconds: float = 30.0) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if is_port_open(port):
            return
        time.sleep(0.2)
    raise RuntimeError(f"H5P runtime on port {port} did not become reachable.")


def h5p_cli_command() -> list[str]:
    h5p_binary = shutil.which("h5p")
    if h5p_binary:
        return [h5p_binary]
    npx_binary = shutil.which("npx")
    if npx_binary:
        return [npx_binary, "--yes", "h5p-cli"]
    raise RuntimeError("Need either 'h5p' or 'npx' in PATH to run h5p-cli.")


def read_h5p_metadata(package_path: Path) -> dict[str, Any]:
    with ZipFile(package_path) as archive:
        return json.loads(archive.read("h5p.json").decode("utf-8"))


def short_name_for_library(machine_name: str) -> str:
    registry_path = RUNTIME_DIR / "libraryRegistry.json"
    if registry_path.exists():
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        entry = registry.get(machine_name)
        if isinstance(entry, dict) and entry.get("shortName"):
            return str(entry["shortName"])
    return machine_name


def ensure_runtime_server(runtime_port: int) -> subprocess.Popen[str] | None:
    if is_port_open(runtime_port):
        return None

    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    (RUNTIME_DIR / "config.js").write_text(SERVER_CONFIG, encoding="utf-8")
    log_path = RUNTIME_DIR / "h5p-webplayer-runtime.log"
    log_file = log_path.open("w", encoding="utf-8")
    process = subprocess.Popen(
        [*h5p_cli_command(), "server", str(runtime_port)],
        cwd=RUNTIME_DIR,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        wait_for_port(runtime_port)
        return process
    except Exception:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        output = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
        raise RuntimeError(f"Could not start h5p-cli runtime.\n{output[-4000:]}")


def import_package(package_path: Path, content_id: str) -> None:
    if not package_path.is_file():
        raise FileNotFoundError(package_path)
    RUNTIME_CONTENT_DIR.mkdir(parents=True, exist_ok=True)
    content_dir = RUNTIME_CONTENT_DIR / content_id
    if content_dir.exists():
        shutil.rmtree(content_dir)
    subprocess.run(
        [*h5p_cli_command(), "import", content_id, str(package_path.resolve())],
        cwd=RUNTIME_DIR,
        check=True,
        text=True,
    )


def build_player_html(title: str, runtime_path: str) -> str:
    escaped_title = html.escape(title)
    escaped_runtime_path = html.escape(runtime_path, quote=True)
    return f"""<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escaped_title}</title>
  <style>
    html, body {{
      width: 100%;
      height: 100%;
      margin: 0;
      overflow: hidden;
      background: #111;
    }}
    #player {{
      position: fixed;
      inset: 0;
      width: 100vw;
      height: 100vh;
      border: 0;
      background: #fff;
    }}
  </style>
</head>
<body>
  <iframe id="player" src="{escaped_runtime_path}" allowfullscreen="allowfullscreen"></iframe>
</body>
</html>
"""


def rewrite_runtime_html(document: str, runtime_port: int) -> str:
    document = re.sub(
        rf"https?://(?:localhost|127\.0\.0\.1):{runtime_port}(?=/|[\"'])",
        "/runtime",
        document,
    )
    document = re.sub(
        r'([\'"`])/(?!runtime(?:/|[\'"`]|$))',
        lambda match: f"{match.group(1)}/runtime/",
        document,
    )
    fullscreen_css = """
<style>
  html, body {
    width: 100% !important;
    min-height: 100% !important;
    margin: 0 !important;
    padding: 0 !important;
    overflow: hidden !important;
    background: #fff !important;
  }
  #status,
  #sessions,
  #newSessionButton,
  #newSession,
  #resetSessionButton,
  #menu,
  .submenu,
  .menu-holder,
  .theme-controls {
    display: none !important;
  }
  .holder,
  .h5p-cli-view,
  .h5p-cli-iframe-wrapper {
    width: 100vw !important;
    height: 100vh !important;
    min-height: 100vh !important;
    margin: 0 !important;
    padding: 0 !important;
    border: 0 !important;
    box-shadow: none !important;
  }
  .h5p-iframe,
  iframe {
    display: block !important;
    width: 100vw !important;
    height: 100vh !important;
    min-height: 100vh !important;
    border: 0 !important;
  }
</style>
<script>
window.addEventListener('load', () => {
  document.querySelectorAll('#status,#sessions,#newSessionButton,#newSession,#resetSessionButton,#menu,.submenu,.menu-holder,.theme-controls').forEach((element) => element.remove());
});
</script>
""".strip()
    return document.replace("</head>", f"{fullscreen_css}\n</head>", 1)


class PlayerHandler(BaseHTTPRequestHandler):
    runtime_port: int
    runtime_path: str
    title: str

    def do_GET(self) -> None:
        if self.path == "/" or self.path.startswith("/player"):
            self.send_html(build_player_html(self.title, self.runtime_path))
            return
        if self.path.startswith("/runtime/") or self.path == "/runtime":
            self.proxy_runtime()
            return
        self.send_error(404)

    def do_POST(self) -> None:
        self.proxy_runtime()

    def do_PUT(self) -> None:
        self.proxy_runtime()

    def do_DELETE(self) -> None:
        self.proxy_runtime()

    def send_html(self, body: str) -> None:
        payload = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def proxy_runtime(self) -> None:
        if not self.path.startswith("/runtime"):
            self.send_error(404)
            return
        split = urlsplit(self.path)
        runtime_target_path = unquote(split.path[len("/runtime"):]) or "/"
        quoted_path = quote(runtime_target_path, safe="/._~!$&'()*+,;=:@")
        target = f"http://127.0.0.1:{self.runtime_port}{quoted_path}"
        if split.query:
            target += f"?{split.query}"

        body = None
        if self.command in {"POST", "PUT", "PATCH"}:
            length = int(self.headers.get("Content-Length", "0") or "0")
            body = self.rfile.read(length) if length else b""

        headers = {
            key: value
            for key, value in self.headers.items()
            if key.lower() not in {"host", "content-length", "accept-encoding", "connection"}
        }
        request = Request(target, data=body, headers=headers, method=self.command)
        try:
            with urlopen(request, timeout=60) as response:
                payload = response.read()
                content_type = response.headers.get("Content-Type", "")
                if "text/html" in content_type:
                    payload = rewrite_runtime_html(payload.decode("utf-8", errors="replace"), self.runtime_port).encode("utf-8")
                self.send_response(response.status)
                for key, value in response.headers.items():
                    if key.lower() in {"content-length", "content-encoding", "transfer-encoding", "connection"}:
                        continue
                    self.send_header(key, value)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
        except HTTPError as error:
            payload = error.read()
            self.send_response(error.code)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        except URLError as error:
            self.send_error(502, f"Runtime proxy failed: {error}")

    def log_message(self, format: str, *args: object) -> None:
        sys.stderr.write(f"[webplayer] {self.address_string()} - {format % args}\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Local fullscreen H5P webplayer")
    parser.add_argument("--package", default=str(DEFAULT_PACKAGE), help="Path to the .h5p package to play")
    parser.add_argument("--content-id", default="", help="Runtime content id. Defaults to the package filename.")
    parser.add_argument("--port", type=int, default=8091, help="Webplayer port")
    parser.add_argument("--runtime-port", type=int, default=8080, help="h5p-cli runtime port")
    parser.add_argument("--no-import", action="store_true", help="Do not re-import the package")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    package_path = Path(args.package)
    if not package_path.is_absolute():
        package_path = ROOT_DIR / package_path
    content_id = args.content_id.strip() or slugify(package_path.stem)

    metadata = read_h5p_metadata(package_path)
    main_library = str(metadata.get("mainLibrary") or "")
    if not main_library:
        raise RuntimeError("Package h5p.json has no mainLibrary.")

    if not args.no_import:
        import_package(package_path, content_id)
    runtime_process = ensure_runtime_server(args.runtime_port)

    short_name = short_name_for_library(main_library)
    runtime_path = f"/runtime/view/{quote(short_name, safe='._~-')}/{quote(content_id, safe='._~-')}"
    title = str(metadata.get("title") or content_id)

    PlayerHandler.runtime_port = args.runtime_port
    PlayerHandler.runtime_path = runtime_path
    PlayerHandler.title = title

    server = ThreadingHTTPServer(("127.0.0.1", args.port), PlayerHandler)
    print(f"H5P Webplayer: http://127.0.0.1:{args.port}", flush=True)
    print(f"Runtime content: {content_id} ({main_library})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        if runtime_process is not None:
            runtime_process.terminate()
            try:
                runtime_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                runtime_process.kill()
                runtime_process.wait(timeout=5)


if __name__ == "__main__":
    main()
