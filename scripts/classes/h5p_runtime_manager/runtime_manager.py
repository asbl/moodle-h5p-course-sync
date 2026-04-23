from __future__ import annotations

from pathlib import Path
from typing import Callable
from urllib.parse import quote, unquote

from scripts.classes import PreviewViewBuilder, PythonQuestionBlock, RuntimePreparationService


class H5PRuntimeManager:
    """Encapsulates local H5P runtime URL building and preparation state."""

    def __init__(
        self,
        *,
        runtime_dir: Path,
        runtime_port: int,
        runtime_proxy_prefix: str,
        custom_h5p_library_short_names: dict[str, str],
        runtime_preparation: RuntimePreparationService,
        get_preview_view_builder: Callable[[], PreviewViewBuilder],
        compute_question_hash: Callable[[PythonQuestionBlock], str],
        write_h5p_package: Callable[[PythonQuestionBlock], Path],
        import_question_into_runtime: Callable[[PythonQuestionBlock], None],
        read_json_or_default: Callable[[Path, dict], dict],
    ) -> None:
        self._runtime_dir = runtime_dir
        self._runtime_port = runtime_port
        self._runtime_proxy_prefix = runtime_proxy_prefix
        self._custom_h5p_library_short_names = custom_h5p_library_short_names
        self._runtime_preparation = runtime_preparation
        self._get_preview_view_builder = get_preview_view_builder
        self._compute_question_hash = compute_question_hash
        self._write_h5p_package = write_h5p_package
        self._import_question_into_runtime = import_question_into_runtime
        self._read_json_or_default = read_json_or_default

    def build_runtime_preview_url(self, question: PythonQuestionBlock) -> str:
        return f"http://127.0.0.1:{self._runtime_port}{self.build_runtime_route_path(question, 'view')}"

    def resolve_runtime_short_name(self, machine_name: str) -> str:
        registry_path = self._runtime_dir / "libraryRegistry.json"
        if registry_path.exists():
            registry = self._read_json_or_default(registry_path, {})
            return registry.get(machine_name, {}).get("shortName", machine_name)
        return self._custom_h5p_library_short_names.get(machine_name, machine_name)

    def quote_path_segment(self, value: str) -> str:
        return quote(value, safe="._~-")

    def build_runtime_route_path(self, question: PythonQuestionBlock, mode: str, *, simple: bool = False) -> str:
        short_name = self.resolve_runtime_short_name(question.main_library)
        path = f"/{mode}/{self.quote_path_segment(short_name)}/{self.quote_path_segment(question.runtime_content_id)}"
        if simple:
            path = f"{path}?simple=1"
        return path

    def build_runtime_proxy_path(self, question: PythonQuestionBlock, mode: str, *, simple: bool = False) -> str:
        return f"{self._runtime_proxy_prefix}{self.build_runtime_route_path(question, mode, simple=simple)}"

    def ensure_runtime_question_ready(self, question: PythonQuestionBlock) -> None:
        self._runtime_preparation.ensure_ready(
            question,
            compute_hash=self._compute_question_hash,
            write_package=self._write_h5p_package,
            import_into_runtime=self._import_question_into_runtime,
        )

    def is_runtime_question_ready(self, question: PythonQuestionBlock) -> bool:
        return self._runtime_preparation.is_ready(question, compute_hash=self._compute_question_hash)

    def start_runtime_question_preparation(self, question: PythonQuestionBlock) -> None:
        self._runtime_preparation.start_preparation(
            question,
            compute_hash=self._compute_question_hash,
            write_package=self._write_h5p_package,
            import_into_runtime=self._import_question_into_runtime,
        )

    def get_runtime_preparation_state(self, question: PythonQuestionBlock) -> dict[str, str]:
        return self._runtime_preparation.state(question, compute_hash=self._compute_question_hash)

    def render_preview_waiting_page(self, question: PythonQuestionBlock, *, mode: str = "view", simple: bool = False) -> str:
        return self._get_preview_view_builder().render_preview_waiting_page(question, mode=mode, simple=simple)

    def resolve_runtime_content_id_from_path(self, runtime_path: str) -> str | None:
        parts = [part for part in runtime_path.strip("/").split("/") if part]
        if len(parts) >= 3 and parts[0] in {"view", "edit", "split"}:
            return unquote(parts[2])
        if len(parts) >= 2 and parts[0] == "remove":
            return unquote(parts[1])
        return None
