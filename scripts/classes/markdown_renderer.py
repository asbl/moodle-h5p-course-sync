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

        def flush_paragraph() -> None:
            if paragraph:
                blocks.append(f"<p>{self._escape_inline(' '.join(paragraph))}</p>")
                paragraph.clear()

        def flush_list() -> None:
            if list_items:
                items = "".join(f"<li>{self._escape_inline(item)}</li>" for item in list_items)
                blocks.append(f"<ul>{items}</ul>")
                list_items.clear()

        for raw_line in lines:
            line = raw_line.rstrip()

            if in_code:
                if line.startswith("```"):
                    code_html = self._escape_inline("\n".join(code_lines))
                    language_class = f" language-{self._escape_inline(code_language)}" if code_language else ""
                    blocks.append(f"<pre><code class=\"{language_class.strip()}\">{code_html}</code></pre>")
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
                blocks.append(question_html.get(identifier, ""))
                continue

            if not line.strip():
                flush_paragraph()
                flush_list()
                continue

            if line.startswith("# "):
                flush_paragraph()
                flush_list()
                blocks.append(f"<h1>{self._escape_inline(line[2:].strip())}</h1>")
                continue

            if line.startswith("## "):
                flush_paragraph()
                flush_list()
                blocks.append(f"<h2>{self._escape_inline(line[3:].strip())}</h2>")
                continue

            if line.startswith("### "):
                flush_paragraph()
                flush_list()
                blocks.append(f"<h3>{self._escape_inline(line[4:].strip())}</h3>")
                continue

            if line.startswith("- "):
                flush_paragraph()
                list_items.append(line[2:].strip())
                continue

            paragraph.append(line.strip())

        flush_paragraph()
        flush_list()

        return "\n".join(blocks)
