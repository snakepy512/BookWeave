"""Render EPUB-first book bundles into the browser and Sphinx outputs."""

from __future__ import annotations

import hashlib
import html
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from book_sources import SourceBundle, validate_output_dir
from epub_parser import EpubBlock, EpubPublication, extract_epub, rewrite_asset_refs


BOOK_TOOL_VERSION = "0.1.0"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_sources(bundle: SourceBundle, static_dir: Path) -> None:
    static_dir.mkdir(parents=True, exist_ok=True)
    if bundle.epub:
        shutil.copy2(bundle.epub, static_dir / "source.epub")
        extracted = static_dir / "epub-source"
        # Rebuilds must not retain assets removed from a newer EPUB. This path
        # is generated output, so replacing it keeps builds deterministic.
        if extracted.is_symlink() or extracted.is_file():
            extracted.unlink()
        elif extracted.is_dir():
            shutil.rmtree(extracted)
        extract_epub(bundle.epub, extracted)
    if bundle.pdf:
        shutil.copy2(bundle.pdf, static_dir / "source.pdf")


def _epub_href(uri: str, prefix: str) -> str:
    return rewrite_asset_refs(uri, prefix)


def _epub_source_link(block: EpubBlock, prefix: str) -> str:
    href = _epub_href(block.source_uri, prefix)
    return (
        f'<span class="source-ref" translate="no"><a href="{html.escape(href, quote=True)}" '
        f'title="source={html.escape(block.source_uri, quote=True)}">'
        "原 EPUB 片段</a></span>"
    )


def _render_reader_block(block: EpubBlock, source_prefix: str) -> str:
    content = rewrite_asset_refs(block.html, source_prefix)
    source = _epub_source_link(block, source_prefix)
    anchor = html.escape(f"rendered-{block.block_id}", quote=True)
    return f'<div class="epub-block" id="{anchor}">{content}{source}</div>'


def _render_reader_heading(block: EpubBlock, source_prefix: str) -> str:
    level = max(1, min(6, block.heading_level or 2))
    anchor = html.escape(f"rendered-{block.block_id}", quote=True)
    return (
        f'<h{level} id="{anchor}" class="reader-heading">{html.escape(block.text)}</h{level}>'
        f"{_epub_source_link(block, source_prefix)}"
    )


def _render_reader_chapter_content(chapter, source_prefix: str) -> str:
    content: list[str] = []
    for block in chapter.blocks:
        if block.kind == "heading":
            content.append(_render_reader_heading(block, source_prefix))
        else:
            content.append(_render_reader_block(block, source_prefix))
    return "\n".join(content)


def _chapter_slug(title: str, index: int) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", title.casefold()).strip("-")
    return f"{index:03d}-{value[:56] or 'chapter'}"


def _chapter_filename(chapter) -> str:
    return f"{_chapter_slug(chapter.title or chapter.href, chapter.index)}.html"


def _is_caption_heading(text: str) -> bool:
    """Return whether a heading-looking EPUB block is a figure/table caption."""
    return bool(re.match(r"^\s*(?:figure|table)\s+\d+(?:\.\d+)*\b", text, re.IGNORECASE))


READER_THEMES = {
    "dark": {
        "color_scheme": "dark",
        "bg": "#11161d", "surface": "#171e27", "text": "#d7dee8",
        "heading": "#f2f5f8", "accent": "#78b7ff", "muted": "#8996a6",
        "border": "#2c3846", "code": "#0d1218", "surface_hover": "#253244",
        "banner": "#1d2b3a",
    },
    "light": {
        "color_scheme": "light",
        "bg": "#f4f6f8", "surface": "#ffffff", "text": "#303a46",
        "heading": "#18222d", "accent": "#1769c2", "muted": "#718096",
        "border": "#dce3ea", "code": "#f1f4f7", "surface_hover": "#e8f0f8",
        "banner": "#eaf3fb",
    },
    "sepia": {
        "color_scheme": "light",
        "bg": "#f4eee4", "surface": "#fbf7f0", "text": "#514636",
        "heading": "#2d261d", "accent": "#91652c", "muted": "#857663",
        "border": "#ded1bc", "code": "#eee4d3", "surface_hover": "#eee4d3",
        "banner": "#f1e8d9",
    },
}


def reader_css(style: str = "dark") -> str:
    theme = READER_THEMES.get(style, READER_THEMES["dark"])
    root = (
        f":root {{ color-scheme: {theme['color_scheme']}; "
        f"--bg:{theme['bg']}; --surface:{theme['surface']}; --text:{theme['text']}; "
        f"--heading:{theme['heading']}; --accent:{theme['accent']}; --muted:{theme['muted']}; "
        f"--border:{theme['border']}; --code:{theme['code']}; "
        f"--surface-hover:{theme['surface_hover']}; --banner:{theme['banner']}; }}"
    )
    css = """
:root { color-scheme: dark; --bg:#11161d; --surface:#171e27; --text:#d7dee8; --heading:#f2f5f8; --accent:#78b7ff; --muted:#8996a6; --border:#2c3846; --code:#0d1218; }
* { box-sizing: border-box; }
html { scroll-behavior: smooth; background: var(--bg); }
body { margin:0; background:var(--bg); color:var(--text); font:16px/1.78 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }
a { color:var(--accent); }
.layout { display:grid; grid-template-columns:minmax(220px,280px) minmax(0,1fr); gap:clamp(1.5rem,4vw,3rem); width:min(1800px,calc(100% - 3rem)); margin:auto; align-items:start; }
.layout > .reader:only-child { grid-column:1 / -1; }
.toc { position:sticky; top:1rem; height:calc(100vh - 2rem); overflow:auto; padding:1rem; border:1px solid var(--border); border-radius:12px; background:var(--surface); }
.toc-header { margin-bottom:.8rem; padding-bottom:.7rem; border-bottom:1px solid var(--border); color:var(--heading); }
.toc-list { display:grid; gap:.15rem; }
.toc-group { margin:0; }
.toc-group > summary { list-style:none; cursor:pointer; }
.toc-group > summary::-webkit-details-marker { display:none; }
.toc-sublist { margin:.15rem 0 .4rem; }
.toc-level-2 { padding-left:1rem !important; }
.toc-level-3 { padding-left:1.6rem !important; font-size:.78rem !important; }
.toc a { display:block; padding:.3rem .45rem; color:var(--muted); text-decoration:none; font-size:.84rem; }
.toc a:hover,.toc a.active { color:var(--heading); background:var(--surface-hover); }
.reader { min-width:0; padding:2rem 0 6rem; }
.reader-nav { display:flex; justify-content:space-between; gap:1rem; margin-bottom:2rem; color:var(--muted); font-size:.84rem; }
.reader-nav a { color:var(--accent); text-decoration:none; }
.reader-nav a:hover { text-decoration:underline; }
.reader-nav-links { display:flex; gap:1rem; flex-wrap:wrap; }
.reader-header { margin-bottom:2rem; padding-bottom:1rem; border-bottom:1px solid var(--border); }
.reader-header h1 { margin:.4rem 0 .5rem; }
.reader-subtitle { margin:0; color:var(--muted); font-size:.86rem; }
.chapter-index { margin-top:2rem; }
.chapter-filter { width:100%; max-width:34rem; margin:0 0 1rem; padding:.6rem .75rem; color:var(--text); background:var(--bg); border:1px solid var(--border); border-radius:8px; }
.chapter-list { display:grid; gap:.55rem; padding:0; list-style:none; }
.chapter-list li { margin:0; }
.chapter-list a { display:flex; align-items:baseline; gap:.8rem; padding:.8rem .9rem; color:var(--text); background:var(--surface); border:1px solid var(--border); border-radius:9px; text-decoration:none; }
.chapter-list a:hover { border-color:var(--accent); background:var(--surface-hover); }
.chapter-number { min-width:2.2rem; color:var(--accent); font: .78rem ui-monospace,SFMono-Regular,Menlo,monospace; }
.chapter-meta { margin-left:auto; color:var(--muted); font-size:.72rem; }
.chapter-content { min-width:0; width:100%; max-width:1200px; }
.chapter-content > h1:first-child { margin-top:0; }
.chapter-nav { display:flex; gap:1rem; flex-wrap:wrap; margin:2rem 0; padding-top:1rem; border-top:1px solid var(--border); }
.chapter-nav a { color:var(--accent); }
.reader h1,.reader h2,.reader h3,.reader h4 { color:var(--heading); line-height:1.25; }
.reader h1 { font:italic 2.6rem/1.15 Georgia,serif; }
.reader h2 { margin-top:3rem; padding-top:1rem; border-top:1px solid var(--border); }
.reader p,.reader li { font-family:Georgia,"Times New Roman",serif; }
.reader img { max-width:100%; height:auto; }
.reader pre { overflow-x:auto; padding:1rem; border:1px solid var(--border); border-radius:8px; background:var(--code); font: .84rem/1.55 ui-monospace,SFMono-Regular,Menlo,monospace; white-space:pre; }
.reader table { width:100%; border-collapse:collapse; overflow:auto; display:block; }
.reader th,.reader td { padding:.5rem .7rem; border:1px solid var(--border); text-align:left; }
.epub-block { position:relative; }
.source-ref { float:right; margin-left:1rem; font-size:.72rem; opacity:.65; }
.source-ref a { text-decoration:none; }
.source-banner { padding:.7rem 1rem; border-left:3px solid var(--accent); background:var(--banner); color:var(--muted); }
@media (max-width:900px) { .layout { display:block; width:min(820px,calc(100% - 2rem)); } .toc { position:relative; height:auto; max-height:35vh; margin-top:1rem; } .chapter-content { max-width:none; } }
"""
    return css.replace(
        ":root { color-scheme: dark; --bg:#11161d; --surface:#171e27; --text:#d7dee8; --heading:#f2f5f8; --accent:#78b7ff; --muted:#8996a6; --border:#2c3846; --code:#0d1218; }",
        root,
    )


def reader_script() -> str:
    return """
(() => {
  const filter = document.querySelector('[data-chapter-filter]');
  if (!filter) return;
  const items = [...document.querySelectorAll('[data-chapter-item]')];
  filter.addEventListener('input', () => {
    const query = filter.value.trim().toLowerCase();
    items.forEach(item => {
      item.hidden = Boolean(query) && !item.textContent.toLowerCase().includes(query);
    });
  });
})();
"""


def _reader_source_links(bundle: SourceBundle, prefix: str) -> str:
    links = [f'<a href="{prefix}source.epub">下载原始 EPUB</a>']
    if bundle.pdf:
        links.append(f'<a href="{prefix}source.pdf">打开原始 PDF</a>')
    return " · ".join(links)


def _reader_document(
    title: str,
    language: str,
    body: str,
    css_href: str,
    js_href: str,
) -> str:
    escaped_language = html.escape(language or "en", quote=True)
    return f'''<!doctype html>
<html lang="{escaped_language}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title>
<link rel="stylesheet" href="{html.escape(css_href, quote=True)}">
</head>
<body>{body}
<script src="{html.escape(js_href, quote=True)}" defer></script>
</body>
</html>
'''


def _render_index_document(
    bundle: SourceBundle,
    publication: EpubPublication,
    chapters: list[tuple[object, str]],
) -> str:
    items = []
    for chapter, filename in chapters:
        items.append(
            f'<li data-chapter-item><a href="chapters/{html.escape(filename, quote=True)}">'
            f'<span class="chapter-number">{chapter.index:03d}</span>'
            f'<span>{html.escape(chapter.title or chapter.href)}</span>'
            f'<span class="chapter-meta">{len(chapter.blocks)} blocks</span></a></li>'
        )
    body = f'''<div class="layout"><main class="reader">
<header class="reader-header">
<div class="eyebrow">EPUB Reader</div>
<h1>{html.escape(publication.title)}</h1>
<p class="reader-subtitle">按章节阅读；每个页面都可单独交给翻译插件处理。</p>
</header>
<p class="source-banner" translate="no">正文优先来自 EPUB；{_reader_source_links(bundle, "")}。原 EPUB 文件位于本目录的 <code>epub-source/</code>。</p>
<section class="chapter-index" aria-labelledby="chapter-index-title">
<h2 id="chapter-index-title">目录</h2>
<input class="chapter-filter" data-chapter-filter type="search" placeholder="筛选章节" aria-label="筛选章节">
<ul class="chapter-list">{"".join(items)}</ul>
</section>
</main></div>'''
    return _reader_document(
        publication.title,
        publication.language,
        body,
        "assets/reader.css",
        "assets/reader.js",
    )


def _render_chapter_toc(chapters: list[tuple[object, str]], current_chapter) -> str:
    items: list[str] = []
    for item_chapter, item_filename in chapters:
        is_current = item_chapter.index == current_chapter.index
        chapter_href = "#top" if is_current else html.escape(item_filename, quote=True)
        chapter_link = (
            f'<a class="toc-level-1{" active" if is_current else ""}" '
            f'href="{chapter_href}">{item_chapter.index:03d} · '
            f'{html.escape(item_chapter.title or item_chapter.href)}</a>'
        )
        if not is_current:
            items.append(chapter_link)
            continue
        subitems = []
        for block in item_chapter.blocks:
            if (
                block.kind == "heading"
                and (block.heading_level or 0) >= 2
                and block.text.strip() != (item_chapter.title or "").strip()
                and not _is_caption_heading(block.text)
            ):
                anchor = html.escape(f"rendered-{block.block_id}", quote=True)
                level = min(3, max(2, block.heading_level or 2))
                subitems.append(
                    f'<a class="toc-level-{level}" href="#{anchor}">'
                    f'{html.escape(block.text)}</a>'
                )
        if subitems:
            rendered_subitems = "".join(subitems)
            items.append(
                '<details class="toc-group" open><summary>'
                f'{chapter_link}</summary><div class="toc-sublist">'
                f'{rendered_subitems}</div></details>'
            )
        else:
            items.append(chapter_link)
    return (
        '<aside class="toc chapter-toc" aria-label="章节目录">'
        '<div class="toc-header"><a href="../index.html">返回目录</a></div>'
        f'<nav class="toc-list">{"".join(items)}</nav></aside>'
    )


def _render_chapter_document(
    bundle: SourceBundle,
    publication: EpubPublication,
    chapters: list[tuple[object, str]],
    position: int,
) -> str:
    chapter, _ = chapters[position]
    previous = chapters[position - 1] if position > 0 else None
    following = chapters[position + 1] if position + 1 < len(chapters) else None
    navigation = []
    if previous:
        navigation.append(f'<a href="{html.escape(previous[1], quote=True)}">← 上一章</a>')
    if following:
        navigation.append(f'<a href="{html.escape(following[1], quote=True)}">下一章 →</a>')
    nav_links = "".join(navigation)
    chapter_toc = _render_chapter_toc(chapters, chapter)
    body = f'''<div class="layout">
{chapter_toc}
<main class="reader">
<nav class="reader-nav" aria-label="章节导航">
<a href="../index.html">← 返回目录</a><span class="reader-nav-links">{nav_links}</span>
</nav>
<header class="reader-header">
<div class="eyebrow">Chapter {chapter.index:03d}</div>
<h1>{html.escape(chapter.title or chapter.href)}</h1>
<p class="reader-subtitle">{len(chapter.blocks)} content blocks · {_reader_source_links(bundle, "../")}</p>
</header>
<section class="chapter-content" data-translate="main">
{_render_reader_chapter_content(chapter, "../epub-source")}
</section>
<nav class="chapter-nav" aria-label="章节导航">{nav_links}<a href="../index.html">返回目录</a></nav>
</main></div>'''
    return _reader_document(
        chapter.title or publication.title,
        publication.language,
        body,
        "../assets/reader.css",
        "../assets/reader.js",
    )


def _render_merged_document(bundle: SourceBundle, publication: EpubPublication) -> str:
    toc: list[str] = []
    content: list[str] = []
    for chapter in publication.chapters:
        chapter_anchor = f"chapter-{chapter.index}"
        title = chapter.title or chapter.href
        toc.append(f'<a href="#{chapter_anchor}">{html.escape(title)}</a>')
        content.append(f'<section id="{chapter_anchor}"><h2>{html.escape(title)}</h2>')
        content.append(_render_reader_chapter_content(chapter, "epub-source"))
        content.append("</section>")
    body = f'''<div class="layout"><aside class="toc"><strong>目录</strong>{"".join(toc)}</aside>
<main class="reader"><header class="reader-header"><div class="eyebrow">EPUB Reader</div>
<h1>{html.escape(publication.title)}</h1></header>
<p class="source-banner" translate="no">正文优先来自 EPUB；{_reader_source_links(bundle, "")}。</p>
<section class="chapter-content" data-translate="main">{"".join(content)}</section>
</main></div>'''
    return _reader_document(
        publication.title,
        publication.language,
        body,
        "assets/reader.css",
        "assets/reader.js",
    )


def render_reader(
    bundle: SourceBundle,
    publication: EpubPublication,
    output_dir: Path,
    style: str = "dark",
    layout: str = "chapter",
    chapter_number: int | None = None,
) -> Path:
    """Write chapter HTML pages, optionally alongside a merged compatibility page."""
    if layout not in {"chapter", "merged", "both"}:
        raise ValueError(f"不支持的 EPUB 输出布局: {layout}")
    if chapter_number is not None and layout != "chapter":
        raise ValueError("--chapter 只能和 --layout chapter 一起使用")
    if chapter_number is not None and not 1 <= chapter_number <= len(publication.chapters):
        raise ValueError(f"章节编号超出范围: {chapter_number}（可用范围 1-{len(publication.chapters)}）")
    output_dir = validate_output_dir(output_dir, bundle)
    output_dir.mkdir(parents=True, exist_ok=True)
    _copy_sources(bundle, output_dir)
    assets_dir = output_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    (assets_dir / "reader.css").write_text(reader_css(style), encoding="utf-8")
    (assets_dir / "reader.js").write_text(reader_script(), encoding="utf-8")

    selected = publication.chapters
    if chapter_number is not None:
        selected = [publication.chapters[chapter_number - 1]]
    chapters = [(chapter, _chapter_filename(chapter)) for chapter in selected]
    if layout in {"chapter", "both"}:
        chapters_dir = output_dir / "chapters"
        chapters_dir.mkdir(parents=True, exist_ok=True)
        for position, (_, filename) in enumerate(chapters):
            (chapters_dir / filename).write_text(
                _render_chapter_document(bundle, publication, chapters, position),
                encoding="utf-8",
            )
        (output_dir / "index.html").write_text(
            _render_index_document(bundle, publication, chapters), encoding="utf-8"
        )
    if layout in {"merged", "both"}:
        (output_dir / "merged_book.html").write_text(
            _render_merged_document(bundle, publication), encoding="utf-8"
        )
    (output_dir / "README.md").write_text(
        f"# {publication.title}\n\n"
        "正文由 EPUB 生成。默认入口是 `index.html`，每章位于 `chapters/`；"
        "如启用兼容模式，整本内容位于 `merged_book.html`。\n"
        "原始 EPUB 位于 `source.epub`，可检查的 EPUB 内部文件位于 `epub-source/`。\n",
        encoding="utf-8",
    )
    return output_dir / ("merged_book.html" if layout == "merged" else "index.html")


def _myst_source_link(block: EpubBlock, source_prefix: str) -> str:
    href = _epub_href(block.source_uri, source_prefix)
    return (
        f'<span class="source-ref" translate="no"><a href="{html.escape(href, quote=True)}" '
        f'title="source={html.escape(block.source_uri, quote=True)}">原 EPUB 片段</a></span>'
    )


def _render_myst_block(
    block: EpubBlock,
    heading_level: int | None = None,
    source_prefix: str = "../_static/epub-source",
) -> str:
    if block.kind == "heading":
        level = heading_level or max(2, min(6, (block.heading_level or 2) + 1))
        return f"{'#' * level} {block.text}\n\n{_myst_source_link(block, source_prefix)}\n\n"
    content = rewrite_asset_refs(block.html, source_prefix)
    return f'{content}\n\n{_myst_source_link(block, source_prefix)}\n\n'


def _render_myst_chapter(chapter, source_prefix: str) -> str:
    lines = [f"# {chapter.title or chapter.href}", ""]
    last_heading_level = 1
    for block in chapter.blocks:
        if block.kind == "heading":
            if (
                (block.heading_level or 0) == 1
                and block.text.strip() == (chapter.title or "").strip()
            ):
                lines.extend([_myst_source_link(block, source_prefix), ""])
                continue
            raw_level = max(2, min(6, block.heading_level or 2))
            rendered_level = max(2, min(6, raw_level, last_heading_level + 1))
            last_heading_level = rendered_level
            lines.append(_render_myst_block(block, rendered_level, source_prefix))
        else:
            lines.append(_render_myst_block(block, source_prefix=source_prefix))
    return "\n".join(lines).rstrip() + "\n"


def _render_myst_merged(publication: EpubPublication) -> str:
    lines = [
        f"# {publication.title}",
        "",
        "> 正文阅读层来自 EPUB；每个内容块保留指向原始 EPUB XHTML 的 source ref。",
        "",
    ]
    for chapter in publication.chapters:
        lines.extend([f"## {chapter.title or chapter.href}", ""])
        last_heading_level = 2
        for block in chapter.blocks:
            if block.kind == "heading":
                if (
                    (block.heading_level or 0) == 1
                    and block.text.strip()
                    in {publication.title.strip(), (chapter.title or "").strip()}
                ):
                    lines.extend([_myst_source_link(block, "../_static/epub-source"), ""])
                    continue
                raw_level = max(2, min(6, (block.heading_level or 2) + 1))
                rendered_level = max(3, min(6, raw_level, last_heading_level + 1))
                last_heading_level = rendered_level
                lines.append(
                    _render_myst_block(
                        block,
                        rendered_level,
                        "../_static/epub-source",
                    )
                )
            else:
                lines.append(_render_myst_block(block, source_prefix="../_static/epub-source"))
    return "\n".join(lines).rstrip() + "\n"


def _document_json(bundle: SourceBundle, publication: EpubPublication) -> dict[str, object]:
    source: dict[str, object] = {
        "preferred": "epub",
        "paired": bundle.paired,
        "epub": {
            "path": str(bundle.epub.resolve()) if bundle.epub else None,
            "sha256": sha256_file(bundle.epub) if bundle.epub else None,
        },
        "pdf": {
            "path": str(bundle.pdf.resolve()) if bundle.pdf else None,
            "sha256": sha256_file(bundle.pdf) if bundle.pdf else None,
        },
    }
    return {
        "schema_version": "2.0",
        "tool": {"name": "book-to-sphinx", "version": BOOK_TOOL_VERSION},
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "epub": publication.to_json(),
        "blocks": [block.to_json() for block in publication.blocks],
        "warnings": [],
    }


def render_sphinx(
    bundle: SourceBundle,
    publication: EpubPublication,
    output_dir: Path,
    build: bool = False,
    layout: str = "chapter",
) -> int:
    """Write an EPUB-first Sphinx/MyST project and optionally build it."""
    if layout not in {"chapter", "merged", "both"}:
        raise ValueError(f"不支持的 Sphinx 输出布局: {layout}")
    output_dir = validate_output_dir(output_dir, bundle)
    docs_dir = output_dir / "docs"
    static_dir = docs_dir / "_static"
    docs_dir.mkdir(parents=True, exist_ok=True)
    static_dir.mkdir(parents=True, exist_ok=True)
    _copy_sources(bundle, static_dir)

    document = _document_json(bundle, publication)
    (output_dir / "document.json").write_text(
        json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "intake.json").write_text(
        json.dumps(
            {
                "preferred_source": "epub",
                "paired_sources": bundle.paired,
                "epub_version": publication.version,
                "chapters": len(publication.chapters),
                "blocks": len(publication.blocks),
                "layout": layout,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    chapter_targets: list[str] = []
    if layout in {"chapter", "both"}:
        chapters_dir = docs_dir / "chapters"
        chapters_dir.mkdir(parents=True, exist_ok=True)
        for chapter in publication.chapters:
            filename = f"{_chapter_slug(chapter.title or chapter.href, chapter.index)}.md"
            (chapters_dir / filename).write_text(
                _render_myst_chapter(chapter, "../../_static/epub-source"),
                encoding="utf-8",
            )
            chapter_targets.append(f"chapters/{filename[:-3]}")
    if layout in {"merged", "both"}:
        (docs_dir / "chapter.md").write_text(
            _render_myst_merged(publication), encoding="utf-8"
        )
    if layout == "merged":
        toc_targets = ["chapter"]
    elif layout == "chapter":
        toc_targets = chapter_targets
    else:
        toc_targets = chapter_targets + ["chapter"]
    toc = "\n".join(toc_targets)
    merged_link = "- 整本兼容版：[`chapter.md`](chapter.md)\n" if layout == "both" else ""
    index_text = (
        f"# {publication.title}\n\n"
        "这是由 `book-to-sphinx.py` 生成的 EPUB-first Sphinx/MyST 项目。\n\n"
        + ("- 正文阅读层：按章节拆分\n" if layout != "merged" else "- 正文阅读层：[`chapter.md`](chapter.md)\n")
        + merged_link
        + "- 原始 EPUB：[`source.epub`](_static/source.epub)\n"
        + ("- 原始 PDF：[`source.pdf`](_static/source.pdf)\n" if bundle.pdf else "")
        + "- EPUB 源文件：`_static/epub-source/`\n\n"
        "```{toctree}\n:maxdepth: 3\n\n"
        + toc
        + "\n```\n"
    )
    (docs_dir / "index.md").write_text(index_text, encoding="utf-8")
    (docs_dir / "conf.py").write_text(
        f"project = {publication.title!r}\n"
        "author = 'book-to-sphinx'\n"
        "extensions = ['myst_parser']\n"
        "myst_heading_anchors = 3\n"
        "html_theme = 'pydata_sphinx_theme'\n"
        f"html_title = {publication.title!r}\n"
        "html_static_path = ['_static']\n"
        "html_css_files = ['custom.css']\n"
        "html_theme_options = {\n"
        "    'navbar_end': ['theme-switcher', 'navbar-icon-links'],\n"
        "    'secondary_sidebar_items': [],\n"
        "}\n",
        encoding="utf-8",
    )
    (static_dir / "custom.css").write_text(
        ".source-ref { float:right; font-size:.78rem; opacity:.7; }\n"
        ".source-ref a { text-decoration:none; }\n"
        "img { max-width:100%; height:auto; }\n"
        "pre { overflow-x:auto; }\n",
        encoding="utf-8",
    )
    (output_dir / "requirements.txt").write_text(
        "sphinx>=9.1.0\nmyst-parser>=5.1.0\npydata-sphinx-theme>=0.20.0\n",
        encoding="utf-8",
    )
    (output_dir / "README.md").write_text(
        f"# {publication.title}\n\n"
        "```bash\n"
        "python -m pip install -r requirements.txt\n"
        "python -m sphinx -b html -W --keep-going docs _build/html\n"
        "```\n\n"
        "正文来自 EPUB；如有配对 PDF，可从 `_static/source.pdf` 打开视觉核对版本。\n",
        encoding="utf-8",
    )
    if not build:
        return 0
    try:
        import subprocess
        import sys

        return subprocess.run(
            [sys.executable, "-m", "sphinx", "-b", "html", "-W", "--keep-going", "docs", "_build/html"],
            cwd=output_dir,
            check=False,
        ).returncode
    except OSError as error:
        print(f"未执行 Sphinx 构建: {error}")
        return 2
