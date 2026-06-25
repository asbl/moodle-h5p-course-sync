from __future__ import annotations

from pathlib import Path
from typing import Callable, Iterable

_TEMPLATES_DIR = Path(__file__).parent / "templates"


def _fill(template: str, **kwargs: str) -> str:
    """Replace ``${KEY}`` (uppercase) placeholders in *template*."""
    for key, value in kwargs.items():
        template = template.replace(f"${{{key.upper()}}}", value)
    return template


class TemplateRenderer:
    def __init__(self, *, escape_inline: Callable[[str], str]) -> None:
        self._escape_inline = escape_inline
        self._course_page_tpl = (_TEMPLATES_DIR / "course_page.html").read_text("utf-8")
        self._index_tpl = (_TEMPLATES_DIR / "index.html").read_text("utf-8")

    def render_course_page(self, *, title: str, content_html: str) -> str:
        return _fill(
            self._course_page_tpl,
            title=self._escape_inline(title),
            content_html=content_html,
        ).strip()

    def render_index(self, course_dirs: Iterable[Path], *, static: bool = False) -> str:
        links = "".join(
            (
                f'<li><a href="courses/{self._escape_inline(d.name)}/index.html">{self._escape_inline(d.name)}</a></li>'
                if static
                else f'<li><a href="/courses/{self._escape_inline(d.name)}">{self._escape_inline(d.name)}</a></li>'
            )
            for d in course_dirs
        )
        return _fill(self._index_tpl, links=links).strip()
