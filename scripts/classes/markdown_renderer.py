from __future__ import annotations

import re
from typing import Callable


class MarkdownRenderer:
    def __init__(self, *, escape_inline: Callable[[str], str]) -> None:
        self._escape_inline = escape_inline

    def render(self, markdown_text: str, question_html: dict[str, str]) -> str:
        lines = markdown_text.splitlines()
        blocks: list[str] = []
        paragraph: list[str] = []
        list_items: list[str] = []
        in_code = False
        code_lines: list[str] = []
        code_language = ""
        current_section: dict[str, object] | None = None

        def emit_block(block_html: str) -> None:
            if current_section is None:
                blocks.append(block_html)
                return
            current_blocks = current_section.setdefault("blocks", [])
            if isinstance(current_blocks, list):
                current_blocks.append(block_html)

        def flush_section() -> None:
            nonlocal current_section
            if current_section is None:
                return
            title = str(current_section.get("title") or "")
            level = str(current_section.get("level") or "h2")
            current_blocks = current_section.get("blocks") or []
            body_html = "\n".join(str(item) for item in current_blocks if str(item).strip())
            blocks.append(
                f'<section class="course-section course-section-{level}">'
                f'<header class="course-section-header"><{level}>{self._escape_inline(title)}</{level}></header>'
                f'<div class="course-section-body">{body_html}</div>'
                f'</section>'
            )
            current_section = None

        def flush_paragraph() -> None:
            if paragraph:
                emit_block(f"<p>{self._escape_inline(' '.join(paragraph))}</p>")
                paragraph.clear()

        def flush_list() -> None:
            if list_items:
                items = "".join(f"<li>{self._escape_inline(item)}</li>" for item in list_items)
                emit_block(f"<ul>{items}</ul>")
                list_items.clear()

        for raw_line in lines:
            line = raw_line.rstrip()

            if in_code:
                if line.startswith("```"):
                    code_html = self._escape_inline("\n".join(code_lines))
                    language_class = f" language-{self._escape_inline(code_language)}" if code_language else ""
                    emit_block(f"<pre><code class=\"{language_class.strip()}\">{code_html}</code></pre>")
                    code_lines.clear()
                    code_language = ""
                    in_code = False
                else:
                    code_lines.append(raw_line)
                continue

            if line.startswith("```"):
                flush_paragraph()
                flush_list()
                in_code = True
                code_language = line[3:].strip().split()[0] if line[3:].strip() else ""
                continue

            placeholder_match = re.fullmatch(r"\[\[\[PYTHON_QUESTION:(.+?)\]\]\]", line.strip())
            if placeholder_match:
                flush_paragraph()
                flush_list()
                identifier = placeholder_match.group(1)
                emit_block(question_html.get(identifier, ""))
                continue

            if not line.strip():
                flush_paragraph()
                flush_list()
                continue

            if line.startswith("# "):
                flush_paragraph()
                flush_list()
                flush_section()
                current_section = {"level": "h1", "title": line[2:].strip(), "blocks": []}
                continue

            if line.startswith("## "):
                flush_paragraph()
                flush_list()
                flush_section()
                current_section = {"level": "h2", "title": line[3:].strip(), "blocks": []}
                continue

            if line.startswith("### "):
                flush_paragraph()
                flush_list()
                flush_section()
                current_section = {"level": "h3", "title": line[4:].strip(), "blocks": []}
                continue

            if line.startswith("- "):
                flush_paragraph()
                list_items.append(line[2:].strip())
                continue

            paragraph.append(line.strip())

        flush_paragraph()
        flush_list()
        flush_section()

        return "\n".join(blocks)
