"""A deliberately tiny Markdown renderer for CASE-STUDY.md.

Supports exactly what the case study uses — headings, bold/italic, links,
inline code, fenced code blocks, tables, lists, blockquotes, hr — and
nothing else. Zero dependencies, output styled by the report's own CSS.
Relative links are rewritten to the GitHub blob URL so they work on the
published page.
"""

from __future__ import annotations

import html
import re

REPO_BLOB = "https://github.com/evanderpool/gridpulse/blob/main/"


def _inline(text: str) -> str:
    """Inline markdown on an already HTML-escaped line."""
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)

    def link(m: re.Match) -> str:
        label, url = m.group(1), m.group(2)
        if not url.startswith(("http", "#", "mailto:")):
            url = REPO_BLOB + url
        return f'<a href="{url}" target="_blank" rel="noopener">{label}</a>'

    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", link, text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<i>\1</i>", text)
    return text


def render(md: str) -> str:
    """Render the supported Markdown subset to HTML."""
    out: list[str] = []
    lines = md.splitlines()
    i = 0
    in_list = in_quote = False

    def close_blocks() -> None:
        nonlocal in_list, in_quote
        if in_list:
            out.append("</ul>")
            in_list = False
        if in_quote:
            out.append("</blockquote>")
            in_quote = False

    while i < len(lines):
        raw = lines[i]
        line = raw.rstrip()

        if line.startswith("```"):
            close_blocks()
            block: list[str] = []
            i += 1
            while i < len(lines) and not lines[i].startswith("```"):
                block.append(html.escape(lines[i]))
                i += 1
            out.append("<pre>" + "\n".join(block) + "</pre>")
            i += 1
            continue

        if line.startswith("|"):
            close_blocks()
            rows: list[str] = []
            while i < len(lines) and lines[i].startswith("|"):
                rows.append(lines[i])
                i += 1
            header = [c.strip() for c in rows[0].strip("|").split("|")]
            body = rows[2:] if len(rows) > 1 and set(rows[1].replace("|", "")
                                                    .strip()) <= set("-: ") else rows[1:]
            out.append('<div class="tablewrap"><table>')
            out.append("<tr>" + "".join(
                f"<th>{_inline(html.escape(h))}</th>" for h in header) + "</tr>")
            for row in body:
                cells = [c.strip() for c in row.strip("|").split("|")]
                out.append("<tr>" + "".join(
                    f"<td>{_inline(html.escape(c))}</td>" for c in cells) + "</tr>")
            out.append("</table></div>")
            continue

        heading = re.match(r"^(#{1,3}) +(.*)$", line)
        if heading:
            close_blocks()
            level = len(heading.group(1))
            out.append(f"<h{level}>{_inline(html.escape(heading.group(2)))}</h{level}>")
            i += 1
            continue

        if line in ("---", "***"):
            close_blocks()
            out.append("<hr>")
            i += 1
            continue

        if line.startswith("- "):
            if not in_list:
                close_blocks()
                out.append("<ul>")
                in_list = True
            item = [line[2:]]
            i += 1
            while i < len(lines) and lines[i].startswith("  ") and lines[i].strip():
                item.append(lines[i].strip())
                i += 1
            out.append(f"<li>{_inline(html.escape(' '.join(item)))}</li>")
            continue

        if line.startswith("> "):
            if not in_quote:
                close_blocks()
                out.append("<blockquote>")
                in_quote = True
            out.append(_inline(html.escape(line[2:])) + " ")
            i += 1
            continue

        if not line:
            close_blocks()
            i += 1
            continue

        # Paragraph: gather continuation lines.
        para = [line]
        i += 1
        while i < len(lines) and lines[i].strip() and not re.match(
                r"^(#{1,3} |[-|>]|```|\*\*\*$|---$)", lines[i]):
            para.append(lines[i].strip())
            i += 1
        out.append(f"<p>{_inline(html.escape(' '.join(para)))}</p>")

    close_blocks()
    return "\n".join(out)
