"""Base class and path mixin for all H5P content types."""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import ClassVar

_DEFAULT_COURSES_DIR = Path(__file__).resolve().parent.parent.parent.parent / "courses"


class H5PContentsMixin:
    """Mixin that provides standard file-system path properties.

    Concrete subclasses must supply ``identifier``, ``course_dir``,
    and ``course_slug`` as dataclass fields (or equivalent attributes).
    """

    __slots__ = ()

    @property
    def package_path(self) -> Path:
        course_dir = self.course_dir or (_DEFAULT_COURSES_DIR / self.course_slug)  # type: ignore[attr-defined]
        build_dir = course_dir / "build" / "h5p"
        h5p_subdir = getattr(self, "h5p_subdir", "")
        return build_dir / h5p_subdir / f"{self.identifier}.h5p" if h5p_subdir else build_dir / f"{self.identifier}.h5p"  # type: ignore[attr-defined]

    @property
    def h5p_dir(self) -> Path:
        course_dir = self.course_dir or (_DEFAULT_COURSES_DIR / self.course_slug)  # type: ignore[attr-defined]
        h5p_subdir = getattr(self, "h5p_subdir", "")
        return course_dir / "h5p" / h5p_subdir if h5p_subdir else course_dir / "h5p"

    @property
    def exploded_dir(self) -> Path:
        return self.h5p_dir / self.identifier  # type: ignore[attr-defined]

    @property
    def shared_libraries_dir(self) -> Path:
        if self.course_dir is not None:  # type: ignore[attr-defined]
            return self.course_dir.parent.parent / "libraries"  # type: ignore[attr-defined]
        return _DEFAULT_COURSES_DIR.parent / "libraries"


class H5PContentType(H5PContentsMixin, ABC):
    """Abstract base class for all H5P content types.

    Subclasses declare :attr:`MACHINE_NAME` and are registered automatically.
    Use :meth:`for_machine_name` to resolve the concrete class for a library
    name at runtime.

    Adding a new content type is straightforward::

        from dataclasses import dataclass, field
        from pathlib import Path
        from typing import ClassVar

        @dataclass(slots=True)
        class MyType(H5PContentType):
            MACHINE_NAME: ClassVar[str] = "H5P.MyType"

            identifier: str
            title: str
            instructions: str
            course_dir: Path | None = None
            course_slug: str = ""

            def compute_hash(self) -> str:
                ...

            def build_editable_payload(self) -> dict[str, object]:
                ...

            def render_mdx_tag(self) -> str:
                ...

    The class is registered automatically and
    ``H5PContentType.for_machine_name("H5P.MyType")`` returns ``MyType``.
    """

    __slots__ = ()
    MACHINE_NAME: ClassVar[str] = ""

    _registry: ClassVar[dict[str, type[H5PContentType]]] = {}

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        name = cls.__dict__.get("MACHINE_NAME", "")
        if name:
            H5PContentType._registry[name] = cls

    @classmethod
    def for_machine_name(cls, name: str) -> type[H5PContentType]:
        """Return the registered class for *name*.

        Falls back to :class:`~scripts.classes.content_types.RawH5PContent`
        for any unregistered machine name.
        """
        from .raw_content import RawH5PContent  # avoid circular import at module level
        return cls._registry.get(name, RawH5PContent)

    @classmethod
    def from_block(cls, block: object) -> H5PContentType:
        """Create an instance from a ``PythonQuestionBlock``-compatible object.

        Each concrete subclass overrides this to map the block's fields to its
        own constructor arguments.  Raises :exc:`NotImplementedError` when
        called on the abstract base.
        """
        raise NotImplementedError(f"{cls.__name__}.from_block() is not implemented.")

    @abstractmethod
    def compute_hash(self) -> str:
        """Return a stable SHA-256 hex-digest of this content item for cache/change detection."""

    @abstractmethod
    def build_editable_payload(
        self,
        *,
        semantics: list[dict[str, object]] | None = None,
        source_payload: tuple[dict[str, object], dict[str, object]] | None = None,
    ) -> dict[str, object]:
        """Return the minimal payload stored in the MDX ``h5p={…}`` attribute.

        *semantics*: parsed ``semantics.json`` list — used by PythonQuestion for
        deeper default-stripping.
        *source_payload*: ``(h5p_metadata, content_json)`` from the original ``.h5p``
        archive — used by imported types (QuestionSet, RawH5PContent) to compute
        a diff rather than emitting the full payload.
        """

    @abstractmethod
    def render_mdx_tag(
        self,
        *,
        semantics: list[dict[str, object]] | None = None,
        source_payload: tuple[dict[str, object], dict[str, object]] | None = None,
    ) -> str:
        """Return the JSX/MDX fragment that embeds this item in a course file.

        The optional *semantics* / *source_payload* parameters mirror those of
        :meth:`build_editable_payload` and are forwarded to it internally so
        that the ``h5p={…}`` attribute is rendered with full compaction.
        """
