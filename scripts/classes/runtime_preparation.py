from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable, Protocol


class QuestionLike(Protocol):
    runtime_content_id: str
    package_path: Path


class RuntimePreparationService:
    """Encapsulates runtime import/warmup state for preview requests."""

    def __init__(self, content_root: Path):
        self._content_root = content_root
        self._import_cache: dict[str, str] = {}
        self._lock = threading.Lock()
        self._threads: dict[str, threading.Thread] = {}
        self._errors: dict[str, str] = {}

    def is_ready(self, question: QuestionLike, compute_hash: Callable[[QuestionLike], str]) -> bool:
        question_hash = compute_hash(question)
        cached_hash = self._import_cache.get(question.runtime_content_id)
        content_dir = self._content_root / question.runtime_content_id
        return cached_hash == question_hash and content_dir.exists() and question.package_path.exists()

    def ensure_ready(
        self,
        question: QuestionLike,
        *,
        compute_hash: Callable[[QuestionLike], str],
        write_package: Callable[[QuestionLike], Path],
        import_into_runtime: Callable[[QuestionLike], None],
    ) -> None:
        if self.is_ready(question, compute_hash):
            return
        write_package(question)
        import_into_runtime(question)
        self._import_cache[question.runtime_content_id] = compute_hash(question)

    def start_preparation(
        self,
        question: QuestionLike,
        *,
        compute_hash: Callable[[QuestionLike], str],
        write_package: Callable[[QuestionLike], Path],
        import_into_runtime: Callable[[QuestionLike], None],
    ) -> None:
        if self.is_ready(question, compute_hash):
            return

        content_id = question.runtime_content_id
        with self._lock:
            existing = self._threads.get(content_id)
            if existing is not None and existing.is_alive():
                return
            self._errors.pop(content_id, None)

            def worker() -> None:
                try:
                    self.ensure_ready(
                        question,
                        compute_hash=compute_hash,
                        write_package=write_package,
                        import_into_runtime=import_into_runtime,
                    )
                    with self._lock:
                        self._errors.pop(content_id, None)
                except Exception as exc:  # noqa: BLE001
                    with self._lock:
                        self._errors[content_id] = str(exc)
                finally:
                    with self._lock:
                        current = self._threads.get(content_id)
                        if current is threading.current_thread():
                            self._threads.pop(content_id, None)

            thread = threading.Thread(target=worker, name=f"h5p-prep-{content_id}", daemon=True)
            self._threads[content_id] = thread
            thread.start()

    def state(self, question: QuestionLike, compute_hash: Callable[[QuestionLike], str]) -> dict[str, str]:
        if self.is_ready(question, compute_hash):
            return {"status": "ready", "error": ""}

        content_id = question.runtime_content_id
        with self._lock:
            error = self._errors.get(content_id, "")
            thread = self._threads.get(content_id)
            is_running = thread is not None and thread.is_alive()

        if error:
            return {"status": "error", "error": error}
        if is_running:
            return {"status": "preparing", "error": ""}
        return {"status": "idle", "error": ""}
