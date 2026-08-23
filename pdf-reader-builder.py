#!/usr/bin/env python3
"""
pdf-reader-builder.py
将英文 PDF 转为适合浏览器阅读的网页，并使用 PDF 内置书签生成左侧 TOC。

依赖: PyMuPDF
用法:
    python pdf-reader-builder.py book.pdf --merge
    python pdf-reader-builder.py book.pdf --merge --server --style sepia
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from book_library import LibraryBook, render_book_navigation, render_reader_controls

try:
    import fitz
except ImportError:
    print("❌ 缺少 PyMuPDF，请先运行: pip install PyMuPDF")
    sys.exit(1)


THEMES = {
    "dark": dict(
        bg="#11161d", surface="#171e27", surface_raised="#1d2631",
        text="#d7dee8", heading="#f2f5f8", accent="#78b7ff",
        accent_soft="rgba(120,183,255,.16)", muted="#8996a6", border="#2c3846",
        code_bg="#0d1218", select="rgba(120,183,255,.28)", shadow="rgba(0,0,0,.22)",
    ),
    "light": dict(
        bg="#f4f6f8", surface="#ffffff", surface_raised="#f7f9fb",
        text="#303a46", heading="#18222d", accent="#1769c2",
        accent_soft="rgba(23,105,194,.11)", muted="#718096", border="#dce3ea",
        code_bg="#f1f4f7", select="rgba(23,105,194,.18)", shadow="rgba(38,53,69,.10)",
    ),
    "sepia": dict(
        bg="#f4eee4", surface="#fbf7f0", surface_raised="#f1e8d9",
        text="#514636", heading="#2d261d", accent="#91652c",
        accent_soft="rgba(145,101,44,.13)", muted="#857663", border="#ded1bc",
        code_bg="#eee4d3", select="rgba(145,101,44,.18)", shadow="rgba(73,53,28,.10)",
    ),
}


@dataclass(frozen=True)
class TocEntry:
    level: int
    title: str
    page: int
    anchor: str
    order: int


@dataclass
class TextBlock:
    text: str
    lines: list[str]
    spans: list[dict]
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def max_font_size(self) -> float:
        return max((float(span.get("size", 0)) for span in self.spans), default=0)

    @property
    def fonts(self) -> set[str]:
        return {str(span.get("font", "")) for span in self.spans}


def css_for(theme_name: str) -> str:
    def variables(theme: dict[str, str], color_scheme: str) -> str:
        return (
            f"color-scheme:{color_scheme}; --bg:{theme['bg']}; --surface:{theme['surface']}; "
            f"--surface-raised:{theme['surface_raised']}; --text:{theme['text']}; "
            f"--heading:{theme['heading']}; --accent:{theme['accent']}; "
            f"--accent-soft:{theme['accent_soft']}; --muted:{theme['muted']}; "
            f"--border:{theme['border']}; --code-bg:{theme['code_bg']}; "
            f"--select:{theme['select']}; --shadow:{theme['shadow']};"
        )

    default_name = theme_name if theme_name in THEMES else "dark"
    t = THEMES[default_name]
    theme_rules = "\n".join(
        f':root[data-theme="{name}"] {{ {variables(theme, "dark" if name == "dark" else "light")} }}'
        for name, theme in THEMES.items()
    )
    return f"""
:root {{
    {variables(t, "dark" if default_name == "dark" else "light")}
}}
{theme_rules}
*, *::before, *::after {{ box-sizing: border-box; }}
html {{ scroll-behavior: smooth; background: var(--bg); }}
body {{
    margin: 0; background: var(--bg); color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Inter, Roboto, sans-serif;
    line-height: 1.78; font-size: 16px;
}}
a {{ color: var(--accent); text-decoration: none; }}
a:hover {{ text-decoration: underline; }}
button, input {{ font: inherit; }}
.layout {{
    display: grid; grid-template-columns: minmax(230px, 276px) minmax(0, 800px);
    gap: clamp(1.5rem, 4vw, 4rem); width: min(1400px, calc(100% - 3rem));
    margin: 0 auto; align-items: start;
}}
.toc {{
    position: sticky; top: 1rem; height: calc(100vh - 2rem); overflow: hidden;
    padding: 1.1rem .85rem .9rem; border: 1px solid var(--border);
    border-radius: 14px; background: color-mix(in srgb, var(--surface) 92%, transparent);
    box-shadow: 0 10px 30px var(--shadow); display: flex; flex-direction: column;
}}
.toc-header {{ padding: 0 .35rem .85rem; border-bottom: 1px solid var(--border); }}
.toc-title {{ color: var(--heading); font-size: .94rem; font-weight: 700; letter-spacing: .03em; }}
.toc-count {{ color: var(--muted); font-size: .72rem; margin-top: .18rem; }}
.toc-filter {{
    width: 100%; margin-top: .8rem; padding: .52rem .65rem; color: var(--text);
    background: var(--bg); border: 1px solid var(--border); border-radius: 8px; outline: none;
}}
.toc-filter:focus {{ border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-soft); }}
.toc-list {{ overflow: auto; padding: .65rem .12rem 1rem; scrollbar-width: thin; }}
.toc-list a {{ display: block; color: var(--muted); font-size: .78rem; line-height: 1.35; padding: .29rem .5rem; border-radius: 6px; }}
.toc-list a:hover, .toc-list a.active {{ color: var(--heading); background: var(--accent-soft); text-decoration: none; }}
.toc-list a.toc-level-1 {{ color: var(--heading); font-size: .84rem; font-weight: 700; margin-top: .35rem; }}
.toc-list a.toc-level-2 {{ padding-left: 1rem; }}
.toc-list a.toc-level-3 {{ padding-left: 1.65rem; font-size: .74rem; }}
.toc-group {{ margin: 0; }}
.toc-group > summary {{ list-style: none; cursor: pointer; }}
.toc-group > summary::-webkit-details-marker {{ display: none; }}
.toc-group > summary::before {{ content: "▾"; display: inline-block; width: 1rem; color: var(--muted); font-size: .7rem; }}
.toc-group:not([open]) > summary::before {{ content: "▸"; }}
.toc-group > summary a {{ display: inline-block; width: calc(100% - 1.2rem); vertical-align: top; }}
.reader {{ min-width: 0; padding: 2.5rem 0 5rem; }}
.library-nav {{ display:grid; gap:.65rem; margin-bottom:1.5rem; padding-bottom:.8rem; border-bottom:1px solid var(--border); color:var(--muted); font-size:.84rem; }}
.library-nav-main {{ display:flex; align-items:center; justify-content:space-between; gap:1rem; flex-wrap:wrap; }}
.library-nav a {{ color:var(--accent); text-decoration:none; }}
.library-nav label {{ display:flex; align-items:center; gap:.45rem; }}
.library-nav select {{ max-width:min(28rem,70vw); padding:.35rem .5rem; color:var(--text); background:var(--surface); border:1px solid var(--border); border-radius:7px; }}
.reader-header {{ margin: 0 0 2.2rem; padding-bottom: 1.35rem; border-bottom: 1px solid var(--border); }}
.eyebrow {{ color: var(--accent); font-size: .72rem; font-weight: 700; letter-spacing: .13em; text-transform: uppercase; }}
.reader-header h1 {{ margin: .45rem 0 .5rem; color: var(--heading); font-family: Georgia, "Times New Roman", serif; font-size: clamp(2rem, 4vw, 3rem); line-height: 1.15; letter-spacing: -.025em; }}
.reader-subtitle {{ margin: 0; color: var(--muted); font-size: .88rem; }}
.page-block {{
    position: relative; padding: 2rem 1.35rem 2.4rem; margin: 0 -1.35rem;
    border-bottom: 1px solid var(--border); scroll-margin-top: 1.5rem;
}}
.page-block:first-of-type {{ padding-top: .6rem; }}
.page-meta {{ display: flex; align-items: center; justify-content: space-between; gap: 1rem; margin-bottom: 1.15rem; color: var(--muted); font: .7rem/1.2 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; letter-spacing: .03em; }}
.page-number {{ padding: .27rem .55rem; border: 1px solid var(--border); border-radius: 999px; background: var(--surface); }}
.page-top {{ opacity: .7; }}
.page-top:hover {{ opacity: 1; }}
.page-content {{ max-width: 72ch; }}
.page-content p {{ margin: 0 0 1rem; font-family: Georgia, "Times New Roman", serif; font-size: 1.02rem; letter-spacing: .002em; }}
.page-content h2, .page-content h3, .page-content h4 {{ color: var(--heading); font-family: Georgia, "Times New Roman", serif; line-height: 1.25; scroll-margin-top: 1.5rem; }}
.page-content h2 {{ margin: 2.7rem 0 1rem; font-size: 1.7rem; }}
.page-content h3 {{ margin: 2rem 0 .75rem; font-size: 1.35rem; color: var(--accent); }}
.page-content h4 {{ margin: 1.6rem 0 .65rem; font-size: 1.1rem; color: var(--accent); }}
.page-content h2.chapter-opener {{ margin-top: .8rem; font-size: clamp(2rem, 5vw, 3.2rem); font-style: italic; }}
.page-content .frontmatter-title {{ text-transform: lowercase; }}
.page-content ul {{ margin: .55rem 0 1.2rem; padding-left: 1.45rem; }}
.page-content li {{ margin: .3rem 0; padding-left: .25rem; font-family: Georgia, "Times New Roman", serif; }}
.page-content li::marker {{ color: var(--accent); }}
.page-content code, .page-content pre {{ font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }}
.page-content pre {{
    overflow-x: auto; margin: 1.25rem 0; padding: 1rem 1.15rem; border: 1px solid var(--border);
    border-radius: 10px; background: var(--code-bg); color: var(--text); font-size: .82rem;
    line-height: 1.55; white-space: pre; tab-size: 4;
}}
.page-content p code {{ padding: .08em .3em; border: 1px solid var(--border); border-radius: 4px; background: var(--code-bg); font-size: .86em; color: var(--heading); }}
.caption {{ margin: .45rem 0 1.2rem; color: var(--muted); font-size: .78rem; font-weight: 700; letter-spacing: .02em; }}
.callout {{ margin: 1.4rem 0; padding: 1rem 1.15rem; border-left: 3px solid var(--accent); border-radius: 0 9px 9px 0; background: var(--accent-soft); }}
.callout-label {{ display: block; margin-bottom: .28rem; color: var(--accent); font-size: .72rem; font-weight: 800; letter-spacing: .1em; }}
.callout p {{ margin: 0; font-size: .95rem; }}
.anchor {{ display: block; position: relative; top: -1.5rem; visibility: hidden; }}
.footer-hint {{ margin-top: 3rem; padding-top: 1.2rem; border-top: 1px solid var(--border); color: var(--muted); font-size: .78rem; line-height: 1.6; }}
.reader-controls {{ display:flex; align-items:center; justify-content:flex-end; gap:.45rem; flex-wrap:wrap; width:fit-content; padding:.3rem; border:1px solid var(--border); border-radius:.55rem; background:color-mix(in srgb,var(--surface) 92%,transparent); }}
.reader-controls-inline {{ justify-self:end; }} .reader-controls-standalone {{ margin:0 0 1rem auto; }}
.reader-controls button,.reader-controls select {{ border:0; border-radius:.32rem; color:var(--muted); background:transparent; font-size:.77rem; padding:.4rem .5rem; }}
.reader-controls button:hover,.reader-controls select:hover {{ color:var(--heading); background:var(--accent-soft); }}
.reader-controls kbd {{ margin-left:.3rem; padding:.08rem .25rem; border:1px solid var(--border); border-radius:.22rem; font:.68rem ui-monospace,monospace; }}
.theme-control {{ display:flex; align-items:center; gap:.15rem; color:var(--muted); font-size:.72rem; }} .sidebar-toggle {{ display:none; }}
.search-dialog {{ width:min(42rem,calc(100vw - 2rem)); border:1px solid var(--border); border-radius:.7rem; padding:0; color:var(--text); background:var(--surface); box-shadow:0 20px 60px rgba(0,0,0,.25); }}
.search-dialog::backdrop {{ background:rgba(0,0,0,.35); }} .search-panel {{ padding:.75rem; }} .search-panel-header {{ display:flex; align-items:center; gap:.75rem; }} .search-panel-header label {{ flex:1; }} .search-panel-header input {{ width:100%; border:0; outline:0; color:var(--text); background:transparent; font-size:1rem; }} .search-panel-header button {{ border:1px solid var(--border); border-radius:.35rem; color:var(--muted); background:var(--bg); padding:.3rem .45rem; }} .search-hint {{ margin:.65rem 0 .35rem; color:var(--muted); font: .73rem/1.4 -apple-system,sans-serif; }} .search-results {{ max-height:min(55vh,28rem); overflow:auto; margin:0; padding:0; list-style:none; }} .search-results a {{ display:block; padding:.65rem .6rem; border-radius:.35rem; color:var(--text); }} .search-results a:hover,.search-results a[aria-selected="true"] {{ background:var(--accent-soft); text-decoration:none; }} .search-results small {{ display:block; color:var(--muted); }}
::selection {{ background: var(--select); }}
@media (max-width: 980px) {{
    .layout {{ display: block; width: min(800px, calc(100% - 2rem)); }}
    .toc {{ position:fixed; z-index:15; top:0; bottom:0; left:0; width:min(20rem,88vw); height:auto; max-height:none; margin:0; padding-top:5rem; border:0; border-right:1px solid var(--border); border-radius:0; background:var(--surface); box-shadow:12px 0 36px var(--shadow); transform:translateX(-105%); transition:transform .18s ease; }}
    .sidebar-open .toc {{ transform:translateX(0); }} .sidebar-toggle {{ display:inline-block; }}
    .reader {{ padding-top: 2rem; }}
}}
@media (max-width: 620px) {{
    body {{ font-size: 15px; }}
    .layout {{ width: calc(100% - 1rem); }}
    .reader {{ padding-top: 1.25rem; }}
    .page-block {{ padding-right: .65rem; padding-left: .65rem; margin-right: -.65rem; margin-left: -.65rem; }}
    .page-content p {{ font-size: 1rem; }}
    .reader-controls {{ width:100%; justify-content:space-between; }} .reader-controls-inline {{ justify-self:stretch; }} .search-trigger kbd {{ display:none; }}
}}
"""


def slugify(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    value = re.sub(r"[^a-zA-Z0-9]+", "-", value.lower()).strip("-")
    return value or "section"


def html_escape(value: str) -> str:
    return html.escape(value, quote=True)


def normalise_text(value: str) -> str:
    value = value.replace("\uf0a1", "•").replace("\u2022", "•")
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def outline_title_without_number(title: str) -> str:
    return re.sub(r"^\d+(?:\.\d+)*\s+", "", normalise_text(title))


def extract_toc(doc) -> list[TocEntry]:
    """Read the PDF's own outline; it is much more reliable than guessing headings."""
    entries: list[TocEntry] = []
    seen: dict[str, int] = {}
    for order, item in enumerate(doc.get_toc(), start=1):
        if len(item) < 3:
            continue
        level, title, page = int(item[0]), normalise_text(str(item[1])), int(item[2])
        if not title or page < 1:
            continue
        base = f"section-{slugify(title)}"
        seen[base] = seen.get(base, 0) + 1
        anchor = base if seen[base] == 1 else f"{base}-{seen[base]}"
        entries.append(TocEntry(level, title, page, anchor, order))
    return entries


def _make_block(raw_block: dict) -> TextBlock | None:
    if "lines" not in raw_block:
        return None
    lines: list[str] = []
    spans: list[dict] = []
    for line in raw_block["lines"]:
        line_spans = line.get("spans", [])
        lines.append("".join(str(span.get("text", "")) for span in line_spans))
        spans.extend(line_spans)
    text = "\n".join(lines)
    if not normalise_text(text):
        return None
    x0, y0, x1, y1 = raw_block["bbox"]
    return TextBlock(text, lines, spans, x0, y0, x1, y1)


def _is_running_header_or_footer(block: TextBlock, page_number: int, page_height: float) -> bool:
    value = normalise_text(block.text)
    if not value:
        return True
    if value.lower().startswith("licensed to "):
        return True
    # The book repeats these headers on almost every body page. They are navigation
    # noise in a web reader, not part of the chapter text.
    if block.y1 <= 45 and re.match(
        r"^\d+\s+(?:c\s*h\s*a\s*p\s*t\s*e\s*r|chapter|contents|summary|\d+(?:\.\d+)*)",
        value,
        re.IGNORECASE,
    ):
        return True
    if block.y0 >= page_height - 90 and re.fullmatch(r"(?:\d+|[ivxlcdm]+)", value, re.IGNORECASE):
        return True
    return False


def extract_page_blocks(page, page_number: int) -> list[TextBlock]:
    result: list[TextBlock] = []
    for raw_block in page.get_text("dict", sort=True).get("blocks", []):
        block = _make_block(raw_block)
        if block is None or _is_running_header_or_footer(block, page_number, page.rect.height):
            continue
        result.append(block)
    return result


def coalesce_wrapped_bullets(blocks: list[TextBlock]) -> list[TextBlock]:
    """Join bullet continuations that the PDF stores as separate positioned blocks."""
    result: list[TextBlock] = []
    for block in blocks:
        if result:
            previous = result[-1]
            continuation = (
                is_bullet_block(previous)
                and not is_bullet_block(block)
                and not is_code_block(block)
                and block.y0 - previous.y1 <= 9
                and block.x0 >= previous.x0 + 8
            )
            if continuation:
                previous.lines.extend(block.lines)
                previous.text = "\n".join(previous.lines)
                previous.spans.extend(block.spans)
                previous.y1 = block.y1
                continue
        result.append(block)
    return result


def clean_line(value: str) -> str:
    value = value.replace("\uf0a1", "•").replace("\uf0a2", "•")
    value = value.replace("\u00a0", " ").rstrip()
    return value


def joined_text(lines: list[str]) -> str:
    """Join PDF line wraps without the artificial spacing from get_text('text')."""
    cleaned = [clean_line(line).strip() for line in lines if clean_line(line).strip()]
    if not cleaned:
        return ""
    result = cleaned[0]
    for line in cleaned[1:]:
        if result.endswith("-") and not result.endswith("--"):
            result = result[:-1] + line
        else:
            result += " " + line
    return normalise_text(result)


def rich_text(value: str) -> str:
    safe = html_escape(value)
    safe = re.sub(r"`([^`]+)`", r"<code>\1</code>", safe)
    safe = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", safe)
    return safe


def is_bullet_block(block: TextBlock) -> bool:
    return bool(re.match(r"^\s*[•·*]\s*", block.text.replace("\uf0a1", "•")))


def is_code_block(block: TextBlock) -> bool:
    meaningful_fonts = [font for font in block.fonts if font not in {"ZapfDingbats", "Wingdings2"}]
    if not meaningful_fonts:
        return False
    return all("Courier" in font for font in meaningful_fonts) and block.max_font_size <= 10


def is_caption_block(block: TextBlock) -> bool:
    value = normalise_text(block.text)
    return bool(re.match(r"^(?:Table|Figure)\s+\d+(?:\.\d+)?\b", value, re.IGNORECASE))


def is_callout_block(block: TextBlock) -> bool:
    first = normalise_text(block.lines[0] if block.lines else block.text)
    return first.upper() in {"NOTE", "LINGO: ANTI-JOIN", "LINGO"} or first.lower().startswith("lingo:")


def entry_matches_prefix(block: TextBlock, entry: TocEntry) -> tuple[bool, int]:
    """Return whether a bookmark title is the heading at the start of this block."""
    target = outline_title_without_number(entry.title)
    if not target:
        return False, 0
    lines = [clean_line(line).strip() for line in block.lines if clean_line(line).strip()]
    if not lines:
        return False, 0
    accumulated: list[str] = []
    for index, line in enumerate(lines[:4]):
        accumulated.append(line)
        candidate = normalise_text(" ".join(accumulated))
        candidate = re.sub(r"^\d+(?:\.\d+)*\s+", "", candidate)
        if candidate == target:
            return True, index + 1
        if target.startswith(candidate + " "):
            continue
        if candidate.startswith(target + " "):
            return True, index + 1
        if len(candidate) > len(target) + 12:
            break
    return False, 0


def heading_for_block(block: TextBlock, page_entries: list[TocEntry]) -> tuple[TocEntry | None, int]:
    for entry in page_entries:
        matched, consumed = entry_matches_prefix(block, entry)
        if matched:
            return entry, consumed
    # Chapter-opening titles use large display type and omit the chapter number in
    # the visible text, so the outline match above is enough in most cases. This
    # fallback keeps front matter headings usable even when a font is unusual.
    if block.max_font_size >= 20:
        value = normalise_text(block.text)
        for entry in page_entries:
            target = outline_title_without_number(entry.title)
            if target in value or target.startswith(value) or value.startswith(target):
                return entry, min(2, len(block.lines))
    return None, 0


def render_code(block: TextBlock) -> str:
    code = "\n".join(clean_line(line).rstrip() for line in block.lines).strip("\n")
    return f"<pre><code>{html_escape(code)}</code></pre>"


def render_bullet(block: TextBlock) -> str:
    lines = [clean_line(line).strip() for line in block.lines if clean_line(line).strip()]
    if not lines:
        return ""
    first = re.sub(r"^[•·*]\s*", "", lines[0])
    body = joined_text([first] + lines[1:])
    return f"<ul><li>{rich_text(body)}</li></ul>"


def render_callout(block: TextBlock) -> str:
    lines = [clean_line(line).strip() for line in block.lines if clean_line(line).strip()]
    label = lines[0].rstrip(":")
    body = joined_text(lines[1:])
    if not body:
        return f'<aside class="callout"><span class="callout-label">{html_escape(label)}</span></aside>'
    return f'<aside class="callout"><span class="callout-label">{html_escape(label)}</span><p>{rich_text(body)}</p></aside>'


def render_regular(block: TextBlock) -> str:
    text = joined_text(block.lines)
    if not text:
        return ""
    # A few PDF exports contain a standalone bullet glyph in an otherwise normal block.
    text = text.replace("• ", "") if text.startswith("• ") else text
    return f"<p>{rich_text(text)}</p>"


def render_block(block: TextBlock) -> str:
    if is_code_block(block):
        return render_code(block)
    if is_bullet_block(block):
        return render_bullet(block)
    if is_callout_block(block):
        return render_callout(block)
    if is_caption_block(block):
        return f'<div class="caption">{rich_text(joined_text(block.lines))}</div>'
    return render_regular(block)


def render_page_content(page_number: int, blocks: list[TextBlock], entries: list[TocEntry]) -> tuple[str, set[str]]:
    rendered: list[str] = []
    placed: set[str] = set()
    page_entries = [entry for entry in entries if entry.page == page_number]
    blocks = coalesce_wrapped_bullets(blocks)
    for block in blocks:
        heading, consumed = heading_for_block(block, [entry for entry in page_entries if entry.anchor not in placed])
        if heading is not None:
            placed.add(heading.anchor)
            level = min(4, heading.level + 1)
            heading_class = "chapter-opener" if heading.level == 1 and block.max_font_size >= 20 else ""
            rendered.append(f'<span id="{heading.anchor}" class="anchor"></span>')
            rendered.append(f'<h{level} class="{heading_class}">{html_escape(heading.title)}</h{level}>')
            remaining = block.lines[consumed:]
            if remaining and joined_text(remaining):
                remainder = TextBlock("\n".join(remaining), remaining, block.spans, block.x0, block.y0, block.x1, block.y1)
                rendered.append(render_block(remainder))
        elif block.max_font_size >= 20 and placed:
            # Display titles are sometimes split into two adjacent PDF blocks.
            # The first block already emitted the complete bookmark title; this
            # second large-font fragment is only the continuation of that title.
            value = normalise_text(block.text)
            if len(value) < 100 and any(entry.level == 1 for entry in page_entries if entry.anchor in placed):
                continue
            rendered.append(render_block(block))
        else:
            rendered.append(render_block(block))
    # If an outline item is not visible in the extracted text, its page is still a
    # useful destination. Put the anchor at the top of that page as a safe fallback.
    missing = [entry for entry in page_entries if entry.anchor not in placed]
    if missing:
        rendered.insert(0, "".join(f'<span id="{entry.anchor}" class="anchor"></span>' for entry in missing))
        placed.update(entry.anchor for entry in missing)
    return "\n".join(item for item in rendered if item), placed


def toc_html(entries: list[TocEntry], merged: bool, current_page: int | None = None) -> str:
    def href(entry: TocEntry) -> str:
        if merged:
            return f"#{entry.anchor}"
        return f"page_{entry.page:04d}.html#{entry.anchor}"

    parts = [
        '<aside class="toc" aria-label="Table of contents">',
        '<div class="toc-header">',
        '<div class="toc-title">Table of contents</div>',
        f'<div class="toc-count">{len(entries)} sections · click to jump</div>',
        '<input class="toc-filter" type="search" placeholder="Filter sections..." aria-label="Filter sections">',
        '</div>',
        '<nav class="toc-list">',
    ]
    open_group = False
    for entry in entries:
        if entry.level == 1:
            if open_group:
                parts.append("</div></details>")
            open_group = True
            parts.append('<details class="toc-group" open>')
            parts.append("<summary>")
            current = " current-page" if current_page == entry.page else ""
            parts.append(f'<a class="toc-level-1{current}" href="{href(entry)}" data-target="{entry.anchor}">{html_escape(entry.title)} <small>p.{entry.page}</small></a>')
            parts.append("</summary><div>")
        else:
            current = " current-page" if current_page == entry.page else ""
            parts.append(f'<a class="toc-level-{min(entry.level, 3)}{current}" href="{href(entry)}" data-target="{entry.anchor}">{html_escape(entry.title)} <small>p.{entry.page}</small></a>')
    if open_group:
        parts.append("</div></details>")
    parts.extend(["</nav>", "</aside>"])
    return "\n".join(parts)


def reader_script() -> str:
    return """
<script>
(() => {
  const root = document.documentElement;
  const themeKey = 'bookweave-theme';
  const themeSelect = document.querySelector('[data-theme-select]');
  const themes = new Set(['system', 'dark', 'light', 'sepia']);
  let savedTheme = null;
  try { savedTheme = window.localStorage.getItem(themeKey); } catch (_) {}
  const setTheme = (choice) => {
    const selected = themes.has(choice) ? choice : 'system';
    root.dataset.theme = selected === 'system'
      ? (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light') : selected;
    root.dataset.themePreference = selected;
    if (themeSelect) themeSelect.value = selected;
  };
  setTheme(themes.has(savedTheme) ? savedTheme : (root.dataset.defaultTheme || 'dark'));
  themeSelect?.addEventListener('change', () => {
    setTheme(themeSelect.value);
    try { window.localStorage.setItem(themeKey, themeSelect.value); } catch (_) {}
  });
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener?.('change', () => {
    if (root.dataset.themePreference === 'system') setTheme('system');
  });
  const filter = document.querySelector('.toc-filter');
  const links = [...document.querySelectorAll('.toc-list a[data-target]')];
  const groups = [...document.querySelectorAll('.toc-group')];
  if (filter) {
    filter.addEventListener('input', () => {
      const query = filter.value.trim().toLowerCase();
      groups.forEach(group => {
        let visible = false;
        group.querySelectorAll('a').forEach(link => {
          const match = !query || link.textContent.toLowerCase().includes(query);
          link.hidden = !match;
          visible ||= match;
        });
        group.hidden = !visible;
        if (query && visible) group.open = true;
      });
    });
  }
  const targets = links.map(link => document.getElementById(link.dataset.target)).filter(Boolean);
  if ('IntersectionObserver' in window && targets.length) {
    const byId = new Map(links.map(link => [link.dataset.target, link]));
    const observer = new IntersectionObserver(entries => {
      entries.filter(entry => entry.isIntersecting).forEach(entry => {
        links.forEach(link => link.classList.remove('active'));
        const link = byId.get(entry.target.id);
        if (link) {
          link.classList.add('active');
          const group = link.closest('.toc-group');
          if (group) group.open = true;
        }
      });
    }, {rootMargin: '-12% 0px -72% 0px', threshold: 0});
    targets.forEach(target => observer.observe(target));
  }
  const switcher = document.querySelector('[data-book-switcher]');
  switcher?.addEventListener('change', () => {
    if (switcher.value) window.location.href = switcher.value;
  });
  const sidebarToggle = document.querySelector('[data-sidebar-toggle]');
  sidebarToggle?.addEventListener('click', () => {
    const open = root.classList.toggle('sidebar-open');
    sidebarToggle.setAttribute('aria-expanded', String(open));
  });
  const dialog = document.querySelector('[data-search-dialog]');
  const searchInput = document.querySelector('[data-search-input]');
  const results = document.querySelector('[data-search-results]');
  const searchItems = [...document.querySelectorAll('.toc a, .library-nav a')]
    .map(item => ({ title: item.textContent.replace(/\\s+/g, ' ').trim(), href: item.href }))
    .filter(item => item.title && item.href);
  const escapeHtml = value => value.replace(/[&<>"']/g, char => ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;' })[char]);
  let selectedResult = 0;
  const renderResults = () => {
    if (!results || !searchInput) return;
    const query = searchInput.value.trim().toLowerCase();
    const matches = searchItems.filter(item => !query || item.title.toLowerCase().includes(query)).slice(0, 12);
    selectedResult = Math.min(selectedResult, Math.max(0, matches.length - 1));
    results.innerHTML = matches.length
      ? matches.map((item, index) => `<li><a href="${escapeHtml(item.href)}" aria-selected="${index === selectedResult}">${escapeHtml(item.title)}<small>打开阅读位置</small></a></li>`).join('')
      : '<li class="search-hint">没有匹配结果</li>';
  };
  document.querySelector('[data-search-open]')?.addEventListener('click', () => {
    if (!dialog?.showModal) return;
    dialog.showModal(); searchInput?.focus(); renderResults();
  });
  searchInput?.addEventListener('input', () => { selectedResult = 0; renderResults(); });
  dialog?.addEventListener('keydown', event => {
    if (!results) return;
    const links = [...results.querySelectorAll('a')];
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      selectedResult = (selectedResult + (event.key === 'ArrowDown' ? 1 : -1) + links.length) % (links.length || 1);
      renderResults();
    } else if (event.key === 'Enter' && links[selectedResult]) {
      event.preventDefault(); links[selectedResult].click();
    }
  });
  document.addEventListener('keydown', event => {
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
      event.preventDefault(); document.querySelector('[data-search-open]')?.click();
    }
    if (event.key === 'Escape' && root.classList.contains('sidebar-open')) {
      root.classList.remove('sidebar-open'); sidebarToggle?.setAttribute('aria-expanded', 'false');
    }
  });
})();
</script>
"""


def document_shell(
    title: str,
    body: str,
    toc: str,
    css: str,
    merged: bool,
    current_page: int | None = None,
    library_books: list[LibraryBook] | None = None,
    book_id: str | None = None,
    style: str = "dark",
) -> str:
    page_hint = f" · page {current_page}" if current_page is not None else ""
    library_nav = (
        render_book_navigation(library_books, book_id or "", "../../index.html", "../")
        if library_books and book_id
        else ""
    )
    standalone_controls = (
        "" if library_books and book_id else render_reader_controls("reader-controls-standalone")
    )
    return f'''<!DOCTYPE html>
<html lang="en" data-default-theme="{html_escape(style)}">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{html_escape(title)}{html_escape(page_hint)} - Readable</title>
<style>{css}</style>
</head>
<body>
<div class="layout">
{toc}
<main class="reader" id="top">
{standalone_controls}
{library_nav}
<header class="reader-header">
  <div class="eyebrow">PDF Reader</div>
  <h1>{html_escape(title)}</h1>
  <p class="reader-subtitle">Continuous reading view · the sidebar follows the PDF bookmarks{html_escape(page_hint)}</p>
</header>
{body}
<div class="footer-hint">Generated by <b>pdf-reader-builder.py</b> · {datetime.now().strftime('%Y-%m-%d %H:%M')}<br>Use Ctrl+F / Cmd+F to search · 配合「沉浸式翻译」插件进行中英双语阅读</div>
</main>
</div>
{reader_script()}
</body>
</html>'''


def page_section(page_number: int, content: str) -> str:
    return f'''<section class="page-block" id="page-{page_number}">
<div class="page-meta"><span class="page-number">Page {page_number}</span><a class="page-top" href="#top">Back to top ↑</a></div>
<div class="page-content">{content}</div>
</section>'''


def generate_merged_html(
    title: str,
    pages: list[tuple[int, list[TextBlock]]],
    entries: list[TocEntry],
    css: str,
    style: str = "dark",
    library_books: list[LibraryBook] | None = None,
    book_id: str | None = None,
) -> str:
    sections = []
    for page_number, blocks in pages:
        content, _ = render_page_content(page_number, blocks, entries)
        sections.append(page_section(page_number, content))
    return document_shell(
        title,
        "\n".join(sections),
        toc_html(entries, merged=True),
        css,
        merged=True,
        library_books=library_books,
        book_id=book_id,
        style=style,
    )


def generate_single_html(
    title: str,
    page_number: int,
    blocks: list[TextBlock],
    entries: list[TocEntry],
    css: str,
    style: str = "dark",
    library_books: list[LibraryBook] | None = None,
    book_id: str | None = None,
) -> str:
    content, _ = render_page_content(page_number, blocks, entries)
    return document_shell(
        title,
        page_section(page_number, content),
        toc_html(entries, merged=False, current_page=page_number),
        css,
        merged=False,
        current_page=page_number,
        library_books=library_books,
        book_id=book_id,
        style=style,
    )


def process_pdf(
    pdf_path: str,
    output_dir: str,
    merge: bool,
    style: str,
    *,
    library_books: list[LibraryBook] | None = None,
    book_id: str | None = None,
) -> Path:
    pdf_path = Path(pdf_path)
    out_dir = Path(output_dir)
    if not pdf_path.exists():
        print(f"❌ File not found: {pdf_path}")
        sys.exit(1)
    out_dir.mkdir(parents=True, exist_ok=True)
    title = pdf_path.stem.replace("_", " ").replace("-", " ")
    doc = fitz.open(str(pdf_path))
    total = len(doc)
    entries = extract_toc(doc)

    print(f"📖 Input:     {pdf_path.name}")
    print(f"📂 Output:    {out_dir.resolve()}")
    print(f"📄 Pages:     {total}")
    print(f"🧭 TOC:       {len(entries)} PDF bookmarks")
    print(f"🔧 Mode:      {'MERGED' if merge else 'SINGLE-PAGE'}")
    print(f"🎨 Theme:     {style}")
    print("-" * 52)

    css = css_for(style)
    pages: list[tuple[int, list[TextBlock]]] = []
    print("✍️  Extracting structured text blocks...")
    for index in range(total):
        page_number = index + 1
        pages.append((page_number, extract_page_blocks(doc[index], page_number)))
        if page_number % 50 == 0 or page_number == total:
            print(f"   📝 Pages {page_number}/{total}...", end="\r")

    if merge:
        html_content = generate_merged_html(
            title, pages, entries, css, style, library_books=library_books, book_id=book_id
        )
        fp = out_dir / "merged_book.html"
        fp.write_text(html_content, encoding="utf-8")
        print(f"\n   ✅ Written: {fp}")
    else:
        for page_number, blocks in pages:
            fp = out_dir / f"page_{page_number:04d}.html"
            fp.write_text(
                generate_single_html(
                    title,
                    page_number,
                    blocks,
                    entries,
                    css,
                    style,
                    library_books=library_books,
                    book_id=book_id,
                ),
                encoding="utf-8",
            )
            if page_number % 100 == 0 or page_number == total:
                print(f"\n   ✅ Pages {page_number}/{total}")

    meta = {
        "title": title,
        "generated_at": datetime.now().isoformat(),
        "total_pages": total,
        "mode": "merged" if merge else "single-page",
        "theme": style,
        "toc_entries": len(entries),
        "source": str(pdf_path),
    }
    (out_dir / "_meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

    readme = f"""# {title}

Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}

## Quick Start

- **Merged mode:** open `merged_book.html` for continuous reading and the left-side TOC.
- **Single-page mode:** open any `page_XXXX.html`; every page includes the same TOC and links to the correct page.
- **Local server:** `python3 -m http.server 8080`

## Features

- Uses the PDF's built-in bookmarks as a nested, searchable table of contents.
- Click any chapter, section, or subsection to jump directly to it.
- Removes repeated running headers, page numbers, and PDF line-wrap artifacts.
- Keeps monospace SQL and terminal output in readable code blocks.

## 配合沉浸式翻译

1. Install [Immersive Translate](https://chromewebstore.google.com/detail/immersive-translate-ai-we/bpoadfkcbjbfhfodiogcnhhhpibjhbnh)
2. Enable **Bilingual** (双语对照) mode
3. Target language: **Simplified Chinese**
4. Code blocks stay in English automatically
"""
    (out_dir / "README.md").write_text(readme.lstrip(), encoding="utf-8")
    print(f"\n✨ Done!\n📂 Files saved to: {out_dir.resolve()}")
    return out_dir


def start_server(host: str, port: int, directory: str):
    import http.server
    import threading
    import time as _time
    import webbrowser

    directory = os.path.abspath(directory)

    class QuietHandler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=directory, **kwargs)

        def log_message(self, format, *args):
            pass

    server = http.server.HTTPServer((host, port), QuietHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://{host}:{port}"
    print(f"\n🌐 Server running at: {url}\n📂 Serving from: {directory}\n🛑 Press Ctrl+C to stop\n")
    try:
        webbrowser.open(url)
    except (OSError, webbrowser.Error):
        pass
    try:
        while True:
            _time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping server...")
        server.shutdown()


def main():
    parser = argparse.ArgumentParser(
        description="Convert an English PDF to a comfortable web reader with a bookmark-based TOC.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python pdf-reader-builder.py book.pdf
  python pdf-reader-builder.py book.pdf --merge --server
  python pdf-reader-builder.py book.pdf --merge --style light -o ./reading
""",
    )
    parser.add_argument("input_pdf", help="Path to the input PDF file")
    parser.add_argument("--output-dir", "-o", default="./pdf-web", help="Output directory")
    parser.add_argument("--merge", action="store_true", help="Merge all pages into merged_book.html")
    parser.add_argument("--server", "-s", action="store_true", help="Start local HTTP server after conversion")
    parser.add_argument("--port", "-p", type=int, default=8080, help="HTTP port (default: 8080)")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (default: 127.0.0.1)")
    parser.add_argument("--style", choices=["dark", "light", "sepia"], default="dark", help="Color theme")
    args = parser.parse_args()

    print("=" * 52)
    print("  PDF Reader Builder")
    print("=" * 52)
    out_dir = process_pdf(args.input_pdf, args.output_dir, args.merge, args.style)
    if args.server:
        start_server(args.host, args.port, str(out_dir))


if __name__ == "__main__":
    main()
