from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from scripts.classes.h5p_runtime_manager.runtime_ids import build_runtime_content_id


DEFAULT_COURSES_DIR = Path(__file__).resolve().parent.parent.parent / "courses"
PYTHON_QUESTION_MACHINE_NAME = "H5P.PythonQuestion"



@dataclass(slots=True)
class TestCase:
    hidden: bool = False
    inputs: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)


@dataclass(slots=True)
class SourceFile:
    file_name: str
    code: str
    visible_to_learner: bool = True
    learner_editable: bool = True


@dataclass(slots=True)
class PythonQuestionBlock:
    identifier: str
    title: str
    instructions: str
    preview_url: str = ""
    main_library: str = PYTHON_QUESTION_MACHINE_NAME
    package_url: str = ""
    raw_package: bool = False
    h5p_metadata: dict[str, object] | None = None
    h5p_content: dict[str, object] | None = None
    h5p_metadata_path: str = ""
    h5p_content_path: str = ""
    source_package_path: str = ""
    runner: str = "pyodide"
    packages: list[str] = field(default_factory=list)
    starter_code: str = ""
    solution_code: str = ""
    pre_code: str = ""
    post_code: str = ""
    grading_method: str = "please_choose"
    show_console: bool = True
    allow_adding_files: bool = False
    source_files: list[SourceFile] = field(default_factory=list)
    test_cases: list[TestCase] = field(default_factory=list)
    course_dir: Path | None = None
    course_slug: str = ""

    @property
    def package_path(self) -> Path:
        course_dir = self.course_dir or (DEFAULT_COURSES_DIR / self.course_slug)
        return course_dir / "h5p" / f"{self.identifier}.h5p"

    @property
    def h5p_dir(self) -> Path:
        course_dir = self.course_dir or (DEFAULT_COURSES_DIR / self.course_slug)
        return course_dir / "h5p"

    @property
    def exploded_dir(self) -> Path:
        return self.h5p_dir / self.identifier

    @property
    def shared_libraries_dir(self) -> Path:
        if self.course_dir is not None:
            return self.course_dir.parent.parent / "libraries"
        return DEFAULT_COURSES_DIR.parent / "libraries"

    @property
    def runtime_content_id(self) -> str:
        return build_runtime_content_id(self.course_slug, self.identifier)


@dataclass(slots=True)
class MoodleH5PActivity:
    identifier: str
    title: str
    course_id: int
    activity_id: int
    instance_id: int | None
    section_title: str = ""
    section_index: int = 0
    module_index: int = 0
    intro: str = ""
    url: str = ""
    visible: bool = True
    package_url: str = ""
    imported_question: PythonQuestionBlock | None = None


@dataclass(slots=True)
class SyncMetadataEntry:
    identifier: str
    remote_activity_id: int
    remote_instance_id: int | None
    remote_title: str
    remote_url: str
    remote_visible: bool
    local_hash: str = ""
    remote_hash: str = ""
    status: str = "tracked"

    def to_dict(self) -> dict[str, object]:
        return {
            "identifier": self.identifier,
            "remoteActivityId": self.remote_activity_id,
            "remoteInstanceId": self.remote_instance_id,
            "remoteTitle": self.remote_title,
            "remoteUrl": self.remote_url,
            "remoteVisible": self.remote_visible,
            "localHash": self.local_hash,
            "remoteHash": self.remote_hash,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> SyncMetadataEntry:
        return cls(
            identifier=str(payload["identifier"]),
            remote_activity_id=int(payload["remoteActivityId"]),
            remote_instance_id=int(payload["remoteInstanceId"]) if payload.get("remoteInstanceId") is not None else None,
            remote_title=str(payload.get("remoteTitle", "")),
            remote_url=str(payload.get("remoteUrl", "")),
            remote_visible=bool(payload.get("remoteVisible", True)),
            local_hash=str(payload.get("localHash", "")),
            remote_hash=str(payload.get("remoteHash", "")),
            status=str(payload.get("status", "tracked")),
        )


@dataclass(slots=True)
class SyncMetadata:
    course_slug: str
    remote_course_id: int
    moodle_base_url: str
    entries: dict[str, SyncMetadataEntry] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "version": 1,
            "course": self.course_slug,
            "remoteCourseId": self.remote_course_id,
            "moodleBaseUrl": self.moodle_base_url,
            "entries": [entry.to_dict() for entry in sorted(self.entries.values(), key=lambda item: item.identifier)],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> SyncMetadata:
        entries = {
            entry.identifier: entry
            for entry in [SyncMetadataEntry.from_dict(item) for item in payload.get("entries", [])]
        }
        return cls(
            course_slug=str(payload.get("course", "")),
            remote_course_id=int(payload.get("remoteCourseId", 0)),
            moodle_base_url=str(payload.get("moodleBaseUrl", "")),
            entries=entries,
        )
