from __future__ import annotations

import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from scripts.classes.models import PythonQuestionBlock


class H5PRuntimeCliBackend(Protocol):
    def ensure_h5p_runtime_libraries(self) -> None: ...

    def get_h5p_cli_command(self) -> list[str]: ...

    def run_h5p_cli(self, args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]: ...


class RuntimeCliService:
    """Coordinates local H5P runtime CLI and server operations."""

    _SERVER_CONFIG = """\
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
  urls: {
    registry: 'https://raw.githubusercontent.com/h5p/h5p-registry/main/libraries.json',
    library: {
      language: 'https://raw.githubusercontent.com/{org}/{dep}/{version}/language/en.json',
      semantics: 'https://raw.githubusercontent.com/{org}/{dep}/{version}/semantics.json',
      list: 'https://raw.githubusercontent.com/{org}/{dep}/{version}/library.json',
      clone: 'https://github.com/{org}/{repo}.git',
      sshClone: 'git@github.com:{org}/{repo}.git',
      zip: 'https://github.com/{org}/{repo}/archive/refs/heads/{version}.zip'
    }
  },
  core: {
    clone: ['h5p-editor-php-library', 'h5p-php-library'],
    setup: ['h5p-math-display']
  },
  registry: 'libraryRegistry.json',
  saveFreq: 30
};
if (process.argv[3] && process.argv[2] === 'server') {
  module.exports.port = +process.argv[3];
}
module.exports.api = `http://localhost:${module.exports.port}`;
module.exports.files.patterns.allowed = process.env.h5p_cli_allowed_files ? new RegExp(process.env.h5p_cli_allowed_files, process.env.h5p_cli_allowed_modifiers) : module.exports.files.patterns.allowed;
module.exports.files.patterns.ignored = process.env.h5p_cli_ignored_files ? new RegExp(process.env.h5p_cli_ignored_files, process.env.h5p_cli_ignored_modifiers) : module.exports.files.patterns.ignored;
"""

    def __init__(
        self,
        *,
        workspace_lock: threading.RLock,
        runtime_dir: Path,
        runtime_content_dir: Path,
        backend: H5PRuntimeCliBackend,
    ) -> None:
        self._workspace_lock = workspace_lock
        self._runtime_dir = runtime_dir
        self._runtime_content_dir = runtime_content_dir
        self._backend = backend

    def is_port_open(self, host: str, port: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.2)
            return sock.connect_ex((host, port)) == 0

    def wait_for_port(self, host: str, port: int, timeout_seconds: float = 30.0) -> None:
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            if self.is_port_open(host, port):
                return
            time.sleep(0.2)
        raise TimeoutError(f"Der H5P-Preview-Server auf Port {port} wurde nicht rechtzeitig erreichbar.")

    def _read_server_log_tail(self, log_path: Path, max_chars: int = 4000) -> str:
        try:
            output = log_path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            return ""
        if len(output) <= max_chars:
            return output
        return output[-max_chars:]

    def _runtime_start_error(self, port: int, exit_code: int | None, log_path: Path) -> RuntimeError:
        message = f"Der H5P-Preview-Server auf Port {port} konnte nicht gestartet werden."
        if exit_code is not None:
            message += f" Exit-Code: {exit_code}."
        output = self._read_server_log_tail(log_path)
        if output:
            message += f"\n\nAusgabe von h5p-cli:\n{output}"
        return RuntimeError(message)

    def ensure_h5p_server_config(self) -> None:
        config_path = self._runtime_dir / "config.js"
        config_path.write_text(self._SERVER_CONFIG, encoding="utf-8")

    def ensure_h5p_runtime_server(self, port: int) -> subprocess.Popen[str] | None:
        self._backend.ensure_h5p_runtime_libraries()
        if self.is_port_open("127.0.0.1", port):
            return None

        self.ensure_h5p_server_config()
        log_path = self._runtime_dir / "h5p-server.log"
        with log_path.open("w", encoding="utf-8") as server_log:
            process = subprocess.Popen(
                [*self._backend.get_h5p_cli_command(), "server", str(port)],
                cwd=self._runtime_dir,
                stdout=server_log,
                stderr=subprocess.STDOUT,
                text=True,
            )

        try:
            deadline = time.time() + 30.0
            while time.time() < deadline:
                exit_code = process.poll()
                if exit_code is not None:
                    raise self._runtime_start_error(port, exit_code, log_path)
                if self.is_port_open("127.0.0.1", port):
                    return process
                time.sleep(0.2)
        except Exception:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)
            raise

        process.terminate()
        process.wait(timeout=5)
        raise self._runtime_start_error(port, None, log_path)

    def import_question_into_runtime(self, question: PythonQuestionBlock) -> None:
        with self._workspace_lock:
            content_dir = self._runtime_content_dir / question.runtime_content_id
            if content_dir.exists():
                shutil.rmtree(content_dir)
            self._backend.run_h5p_cli(["import", question.runtime_content_id, str(question.package_path)], cwd=self._runtime_dir)
