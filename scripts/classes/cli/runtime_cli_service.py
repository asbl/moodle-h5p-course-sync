from __future__ import annotations

import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from scripts.classes.models import PythonQuestionBlock


class RuntimeCliService:
    """Coordinates local H5P runtime CLI and server operations."""

    def __init__(
        self,
        *,
        workspace_lock: threading.RLock,
        runtime_dir: Path,
        runtime_content_dir: Path,
        ensure_h5p_runtime_libraries: Callable[[], None],
        get_h5p_cli_command: Callable[[], list[str]],
        run_h5p_cli: Callable[[list[str], Path], subprocess.CompletedProcess[str]],
    ) -> None:
        self._workspace_lock = workspace_lock
        self._runtime_dir = runtime_dir
        self._runtime_content_dir = runtime_content_dir
        self._ensure_h5p_runtime_libraries = ensure_h5p_runtime_libraries
        self._get_h5p_cli_command = get_h5p_cli_command
        self._run_h5p_cli = run_h5p_cli

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

    def ensure_h5p_runtime_server(self, port: int) -> subprocess.Popen[str] | None:
        self._ensure_h5p_runtime_libraries()
        if self.is_port_open("127.0.0.1", port):
            return None

        process = subprocess.Popen(
            [*self._get_h5p_cli_command(), "server", str(port)],
            cwd=self._runtime_dir,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )

        try:
            self.wait_for_port("127.0.0.1", port)
        except Exception:
            process.terminate()
            process.wait(timeout=5)
            raise

        return process

    def import_question_into_runtime(self, question: PythonQuestionBlock) -> None:
        with self._workspace_lock:
            content_dir = self._runtime_content_dir / question.runtime_content_id
            if content_dir.exists():
                shutil.rmtree(content_dir)
            self._run_h5p_cli(["import", question.runtime_content_id, str(question.package_path)], cwd=self._runtime_dir)
