"""Render EPUB-first book bundles into the browser and Sphinx outputs."""

from __future__ import annotations

import hashlib
import html
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from book_library import LibraryBook, render_book_navigation, render_reader_controls
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


def _render_reader_block(block: EpubBlock, source_prefix: str) -> str:
    content = rewrite_asset_refs(block.html, source_prefix)
    anchor = html.escape(f"rendered-{block.block_id}", quote=True)
    return f'<div class="epub-block" id="{anchor}">{content}</div>'


def _render_reader_heading(block: EpubBlock) -> str:
    level = max(1, min(6, block.heading_level or 2))
    anchor = html.escape(f"rendered-{block.block_id}", quote=True)
    return (
        f'<h{level} id="{anchor}" class="reader-heading">{html.escape(block.text)}</h{level}>'
    )


def _render_reader_chapter_content(chapter, source_prefix: str) -> str:
    content: list[str] = []
    for block in chapter.blocks:
        if (
            block.kind == "heading"
            and (block.heading_level or 0) == 1
            and block.text.strip() == (chapter.title or "").strip()
        ):
            # The chapter page already renders this title in its header.
            continue
        if block.kind == "heading":
            content.append(_render_reader_heading(block))
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


def _is_chapter_overview_heading(text: str) -> bool:
    """Return whether a generic overview label should stay out of the TOC."""
    return text.strip().casefold() == "this chapter covers"


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
    def variables(theme_name: str) -> str:
        selected = READER_THEMES[theme_name]
        return (
            f"color-scheme: {selected['color_scheme']}; "
            f"--bg:{selected['bg']}; --surface:{selected['surface']}; --text:{selected['text']}; "
            f"--heading:{selected['heading']}; --accent:{selected['accent']}; --muted:{selected['muted']}; "
            f"--border:{selected['border']}; --code:{selected['code']}; "
            f"--surface-hover:{selected['surface_hover']}; --banner:{selected['banner']};"
        )

    default_theme = style if style in READER_THEMES else "dark"
    theme_rules = "\n".join(
        f':root[data-theme="{theme_name}"] {{ {variables(theme_name)} }}'
        for theme_name in READER_THEMES
    )
    css = """
:root { __BOOKWEAVE_DEFAULT_THEME__ }
__BOOKWEAVE_THEME_RULES__
* { box-sizing:border-box; }
html { scroll-behavior:smooth; background:var(--bg); }
body { margin:0; background:var(--bg); color:var(--text); font:16px/1.75 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }
a { color:var(--accent); text-decoration:none; } a:hover { text-decoration:underline; }
button,input,select { font:inherit; } button,select { cursor:pointer; }
.layout { display:grid; grid-template-columns:minmax(15rem,18rem) minmax(0,48rem); gap:clamp(2rem,6vw,6rem); width:min(88rem,calc(100% - 4rem)); margin:0 auto; align-items:start; }
.layout > .reader:only-child { grid-column:1 / -1; max-width:48rem; }
.toc { position:sticky; top:1.25rem; height:calc(100vh - 2.5rem); overflow:auto; padding:0 1rem .5rem 0; border-right:1px solid var(--border); scrollbar-width:thin; }
.chapter-layout { grid-template-columns:minmax(17rem,20rem) minmax(0,1fr) minmax(22rem,26rem); gap:2rem; width:calc(100% - 4rem); max-width:none; }
.chapter-layout .section-toc { padding:0 0 .5rem 1.5rem; border-right:0; border-left:1px solid var(--border); }
.chapter-layout .chapter-content { max-width:78ch; }.section-toc a { padding:.48rem .65rem; font-size:.94rem; line-height:1.45; }.section-toc .toc-level-2 { padding-left:.65rem !important; }.section-toc .toc-level-3 { padding-left:1.25rem !important; font-size:.86rem !important; }
.toc-empty { margin:.5rem; color:var(--muted); font-size:.78rem; }
.toc-header { margin-bottom:.75rem; padding-bottom:.75rem; border-bottom:1px solid var(--border); color:var(--heading); font-size:.84rem; font-weight:650; }
.toc-list { display:grid; gap:.08rem; }.toc-group { margin:0; }.toc-group > summary { list-style:none; cursor:pointer; }.toc-group > summary::-webkit-details-marker { display:none; }.toc-group > summary::before { content:"⌄"; color:var(--muted); font-size:.75rem; }.toc-group:not([open]) > summary::before { content:"›"; }.toc-sublist { margin:.1rem 0 .35rem; }
.toc a { display:block; padding:.3rem .5rem; border-radius:.35rem; color:var(--muted); font-size:.82rem; line-height:1.35; }.toc a:hover,.toc a.active { color:var(--heading); background:var(--surface-hover); text-decoration:none; }.toc-level-2 { padding-left:1.15rem !important; }.toc-level-3 { padding-left:1.8rem !important; font-size:.76rem !important; }
.reader { min-width:0; padding:4rem 0 7rem; }.reader-nav { display:flex; justify-content:space-between; gap:1rem; margin-bottom:2.5rem; color:var(--muted); font-size:.82rem; }.reader-nav-links,.chapter-nav { display:flex; gap:1rem; flex-wrap:wrap; }.reader-header { margin-bottom:2.2rem; padding-bottom:1.2rem; border-bottom:1px solid var(--border); }.eyebrow { color:var(--accent); font-size:.72rem; font-weight:700; letter-spacing:.1em; text-transform:uppercase; }.reader-header h1 { margin:.35rem 0 .55rem; }.reader-subtitle { margin:0; color:var(--muted); font-size:.9rem; }
.chapter-index { margin-top:2rem; }.chapter-filter { width:100%; margin:0 0 1rem; padding:.65rem .75rem; color:var(--text); background:var(--surface); border:1px solid var(--border); border-radius:.4rem; outline:none; }.chapter-filter:focus { border-color:var(--accent); box-shadow:0 0 0 3px color-mix(in srgb,var(--accent) 18%,transparent); }.chapter-list,.library-list { display:grid; gap:.1rem; padding:0; list-style:none; }.chapter-list li,.library-list li { margin:0; }.chapter-list a,.library-card { display:flex; align-items:baseline; gap:.75rem; padding:.7rem .65rem; border-radius:.4rem; color:var(--text); text-decoration:none; }.chapter-list a:hover,.library-card:hover { background:var(--surface-hover); text-decoration:none; }.chapter-number { min-width:2.25rem; color:var(--accent); font:.76rem ui-monospace,SFMono-Regular,Menlo,monospace; }.chapter-meta,.library-card-meta { margin-left:auto; color:var(--muted); font-size:.75rem; }.library-card-title { color:var(--heading); font:1.05rem Georgia,serif; }
.chapter-content { min-width:0; width:100%; max-width:46rem; }.chapter-nav { margin:3rem 0; padding-top:1.2rem; border-top:1px solid var(--border); }.reader h1,.reader h2,.reader h3,.reader h4 { color:var(--heading); line-height:1.22; }.reader h1 { font:2.7rem/1.12 Georgia,serif; letter-spacing:-.025em; }.reader h2 { margin-top:3rem; padding-top:.3rem; font:1.75rem/1.2 Georgia,serif; }.reader h3 { margin-top:2rem; font:1.28rem/1.25 Georgia,serif; }.reader p,.reader li { font-family:Georgia,"Times New Roman",serif; }.reader img { max-width:100%; height:auto; }.reader pre { overflow-x:auto; padding:1rem; border:1px solid var(--border); border-radius:.45rem; background:var(--code); font:.82rem/1.55 ui-monospace,SFMono-Regular,Menlo,monospace; white-space:pre; }.reader table { width:100%; border-collapse:collapse; overflow:auto; display:block; }.reader th,.reader td { padding:.5rem .7rem; border:1px solid var(--border); text-align:left; }.source-banner { padding:.8rem 1rem; border-left:2px solid var(--accent); background:var(--banner); color:var(--muted); }
.library-nav { display:grid; gap:.65rem; margin-bottom:1.75rem; padding-bottom:.8rem; border-bottom:1px solid var(--border); color:var(--muted); font-size:.84rem; }.library-nav-main { display:flex; align-items:center; justify-content:space-between; gap:1rem; flex-wrap:wrap; }.library-nav label { display:flex; align-items:center; gap:.45rem; }.library-nav select { max-width:min(28rem,70vw); padding:.35rem .5rem; color:var(--text); background:var(--surface); border:1px solid var(--border); border-radius:.35rem; }
.reader-controls { display:flex; align-items:center; justify-content:flex-end; gap:.45rem; flex-wrap:wrap; width:fit-content; padding:.3rem; border:1px solid var(--border); border-radius:.55rem; background:color-mix(in srgb,var(--surface) 92%,transparent); }.reader-controls-inline { justify-self:end; }.reader-controls-standalone { margin:0 0 1rem auto; }.reader-controls button,.reader-controls select { border:0; border-radius:.32rem; color:var(--muted); background:transparent; font-size:.77rem; padding:.4rem .5rem; }.reader-controls button:hover,.reader-controls select:hover { color:var(--heading); background:var(--surface-hover); }.reader-controls kbd { margin-left:.3rem; padding:.08rem .25rem; border:1px solid var(--border); border-radius:.22rem; font:.68rem ui-monospace,monospace; }.theme-control { display:flex; align-items:center; gap:.15rem; color:var(--muted); font-size:.72rem; }.sidebar-toggle { display:none; }
.search-dialog { width:min(42rem,calc(100vw - 2rem)); border:1px solid var(--border); border-radius:.7rem; padding:0; color:var(--text); background:var(--surface); box-shadow:0 20px 60px rgba(0,0,0,.25); }.search-dialog::backdrop { background:rgba(0,0,0,.35); }.search-panel { padding:.75rem; }.search-panel-header { display:flex; align-items:center; gap:.75rem; }.search-panel-header label { flex:1; }.search-panel-header input { width:100%; border:0; outline:0; color:var(--text); background:transparent; font-size:1rem; }.search-panel-header button { border:1px solid var(--border); border-radius:.35rem; color:var(--muted); background:var(--bg); padding:.3rem .45rem; }.search-hint { margin:.65rem 0 .35rem; color:var(--muted); font: .73rem/1.4 -apple-system,sans-serif; }.search-results { max-height:min(55vh,28rem); overflow:auto; margin:0; padding:0; list-style:none; }.search-results a { display:block; padding:.65rem .6rem; border-radius:.35rem; color:var(--text); }.search-results a:hover,.search-results a[aria-selected="true"] { background:var(--surface-hover); text-decoration:none; }.search-results small { display:block; color:var(--muted); }
@media (max-width:900px) { .layout:not(.chapter-layout) { display:block; width:min(46rem,calc(100% - 2rem)); }.reader { padding-top:4rem; }.layout:not(.chapter-layout) .toc { position:fixed; z-index:15; top:0; bottom:0; left:0; width:min(20rem,88vw); height:auto; padding:5rem 1rem 2rem; border:0; border-right:1px solid var(--border); background:var(--surface); box-shadow:12px 0 36px rgba(0,0,0,.16); transform:translateX(-105%); transition:transform .18s ease; }.sidebar-open .layout:not(.chapter-layout) .toc { transform:translateX(0); }.layout:not(.chapter-layout) .sidebar-toggle { display:inline-block; }.layout:not(.chapter-layout) .chapter-content { max-width:none; } }
@media (max-width:560px) { body { font-size:15px; }.layout { width:calc(100% - 1.5rem); }.reader { padding-top:1.75rem; }.reader h1 { font-size:2.25rem; }.reader-controls { width:100%; justify-content:space-between; }.reader-controls-inline { justify-self:stretch; }.search-trigger kbd { display:none; } }
"""
    return css.replace(
        "__BOOKWEAVE_DEFAULT_THEME__", variables(default_theme)
    ).replace("__BOOKWEAVE_THEME_RULES__", theme_rules)


def reader_script() -> str:
    return """
(() => {
  const root = document.documentElement;
  const themeSelect = document.querySelector('[data-theme-select]');
  const themeKey = 'bookweave-theme';
  const themes = new Set(['system', 'dark', 'light', 'sepia']);
  let savedTheme = null;
  try { savedTheme = window.localStorage.getItem(themeKey); } catch (_) {}
  const defaultTheme = root.dataset.defaultTheme || 'dark';
  const setTheme = (choice) => {
    const selected = themes.has(choice) ? choice : 'system';
    const resolved = selected === 'system'
      ? (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light')
      : selected;
    root.dataset.theme = resolved;
    root.dataset.themePreference = selected;
    if (themeSelect) themeSelect.value = selected;
  };
  setTheme(themes.has(savedTheme) ? savedTheme : (themes.has(defaultTheme) ? defaultTheme : 'system'));
  themeSelect?.addEventListener('change', () => {
    setTheme(themeSelect.value);
    try { window.localStorage.setItem(themeKey, themeSelect.value); } catch (_) {}
  });
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener?.('change', () => {
    if (root.dataset.themePreference === 'system') setTheme('system');
  });

  const filter = document.querySelector('[data-chapter-filter]');
  if (filter) {
    const items = [...document.querySelectorAll('[data-chapter-item]')];
    filter.addEventListener('input', () => {
      const query = filter.value.trim().toLowerCase();
      items.forEach(item => {
        item.hidden = Boolean(query) && !item.textContent.toLowerCase().includes(query);
      });
    });
  }
  const bookFilter = document.querySelector('[data-book-filter]');
  if (bookFilter) {
    const books = [...document.querySelectorAll('[data-book-item]')];
    bookFilter.addEventListener('input', () => {
      const query = bookFilter.value.trim().toLowerCase();
      books.forEach(book => {
        book.hidden = Boolean(query) && !book.textContent.toLowerCase().includes(query);
      });
    });
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
  const sectionLinks = [...document.querySelectorAll('[data-section-link]')];
  const sectionTargets = sectionLinks
    .map(link => ({ link, target: document.getElementById(link.dataset.target) }))
    .filter(item => item.target);
  if ('IntersectionObserver' in window && sectionTargets.length) {
    const byTarget = new Map(sectionTargets.map(item => [item.target, item.link]));
    const observer = new IntersectionObserver(entries => {
      const visible = entries.filter(entry => entry.isIntersecting).sort((left, right) => left.boundingClientRect.top - right.boundingClientRect.top)[0];
      if (!visible) return;
      sectionLinks.forEach(link => link.classList.remove('active'));
      byTarget.get(visible.target)?.classList.add('active');
    }, { rootMargin: '-15% 0px -70% 0px', threshold: 0 });
    sectionTargets.forEach(item => observer.observe(item.target));
  }

  const dialog = document.querySelector('[data-search-dialog]');
  const searchInput = document.querySelector('[data-search-input]');
  const results = document.querySelector('[data-search-results]');
  const searchItems = [...document.querySelectorAll('[data-search-item], .toc a, .chapter-list a, .library-card')]
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
"""


def _reader_document(
    title: str,
    language: str,
    body: str,
    css_href: str,
    js_href: str,
    style: str = "dark",
    controls: str = "",
) -> str:
    escaped_language = html.escape(language or "en", quote=True)
    default_theme = style if style in READER_THEMES else "dark"
    return f'''<!doctype html>
<html lang="{escaped_language}" data-default-theme="{default_theme}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title>
<link rel="stylesheet" href="{html.escape(css_href, quote=True)}">
</head>
<body>{controls}{body}
<script src="{html.escape(js_href, quote=True)}" defer></script>
</body>
</html>
'''


def _render_index_document(
    bundle: SourceBundle,
    publication: EpubPublication,
    chapters: list[tuple[object, str]],
    style: str,
    library_books: list[LibraryBook] | None = None,
) -> str:
    items = []
    for chapter, filename in chapters:
        items.append(
            f'<li data-chapter-item><a href="chapters/{html.escape(filename, quote=True)}">'
            f'<span class="chapter-number">{chapter.index:03d}</span>'
            f'<span>{html.escape(chapter.title or chapter.href)}</span>'
            f'<span class="chapter-meta">{len(chapter.blocks)} blocks</span></a></li>'
        )
    library_nav = (
        render_book_navigation(library_books, bundle.book_id, "../../index.html", "../")
        if library_books
        else ""
    )
    standalone_controls = render_reader_controls("reader-controls-standalone") if not library_books else ""
    body = f'''<div class="layout"><main class="reader">
{library_nav}
<header class="reader-header">
<div class="eyebrow">EPUB Reader</div>
<h1>{html.escape(publication.title)}</h1>
<p class="reader-subtitle">按章节阅读；每个页面都可单独交给翻译插件处理。</p>
</header>
<p class="source-banner" translate="no">正文优先来自 EPUB；图片等本地资源位于 <code>epub-source/</code>。</p>
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
        style,
        standalone_controls,
    )


def _render_book_toc(chapters: list[tuple[object, str]], current_chapter) -> str:
    """Render only the book's top-level chapters for the left sidebar."""
    items: list[str] = []
    for item_chapter, item_filename in chapters:
        is_current = item_chapter.index == current_chapter.index
        chapter_href = "#top" if is_current else html.escape(item_filename, quote=True)
        items.append(
            f'<a class="toc-level-1 book-chapter-link{" active" if is_current else ""}" '
            'data-book-chapter-link '
            f'href="{chapter_href}">{item_chapter.index:03d} · '
            f'{html.escape(item_chapter.title or item_chapter.href)}</a>'
        )
    return (
        '<aside class="toc book-toc" aria-label="书籍目录">'
        '<div class="toc-header"><a href="../index.html">书籍目录</a></div>'
        f'<nav class="toc-list">{"".join(items)}</nav></aside>'
    )


def _render_current_chapter_toc(chapter) -> str:
    """Render headings from the selected chapter for the right sidebar."""
    items: list[str] = []
    for block in chapter.blocks:
        if (
            block.kind != "heading"
            or (block.heading_level or 0) < 2
            or block.text.strip() == (chapter.title or "").strip()
            or _is_caption_heading(block.text)
            or _is_chapter_overview_heading(block.text)
        ):
            continue
        anchor = html.escape(f"rendered-{block.block_id}", quote=True)
        level = min(3, max(2, block.heading_level or 2))
        items.append(
            f'<a class="toc-level-{level} chapter-section-link" data-section-link '
            f'data-target="{anchor}" href="#{anchor}">{html.escape(block.text)}</a>'
        )
    empty = '<p class="toc-empty">本章没有可展开的小节。</p>' if not items else ""
    return (
        '<aside class="toc section-toc" aria-label="本章目录">'
        '<div class="toc-header"><span>本章目录</span></div>'
        f'<nav class="toc-list">{"".join(items)}{empty}</nav></aside>'
    )


def _render_chapter_document(
    bundle: SourceBundle,
    publication: EpubPublication,
    chapters: list[tuple[object, str]],
    position: int,
    style: str,
    library_books: list[LibraryBook] | None = None,
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
    book_toc = _render_book_toc(chapters, chapter)
    section_toc = _render_current_chapter_toc(chapter)
    library_nav = (
        render_book_navigation(library_books, bundle.book_id, "../../index.html", "../../")
        if library_books
        else ""
    )
    standalone_controls = render_reader_controls("reader-controls-standalone") if not library_books else ""
    body = f'''<div class="layout chapter-layout">
{book_toc}
<main class="reader">
{library_nav}
<nav class="reader-nav" aria-label="章节导航">
<a href="../index.html">← 返回目录</a><span class="reader-nav-links">{nav_links}</span>
</nav>
<header class="reader-header">
<div class="eyebrow">Chapter {chapter.index:03d}</div>
<h1>{html.escape(chapter.title or chapter.href)}</h1>
<p class="reader-subtitle">{len(chapter.blocks)} content blocks</p>
</header>
<section class="chapter-content" data-translate="main">
{_render_reader_chapter_content(chapter, "../epub-source")}
</section>
<nav class="chapter-nav" aria-label="章节导航">{nav_links}<a href="../index.html">返回目录</a></nav>
</main>
{section_toc}
</div>'''
    return _reader_document(
        chapter.title or publication.title,
        publication.language,
        body,
        "../assets/reader.css",
        "../assets/reader.js",
        style,
        standalone_controls,
    )


def _render_merged_document(
    bundle: SourceBundle,
    publication: EpubPublication,
    style: str,
    library_books: list[LibraryBook] | None = None,
) -> str:
    toc: list[str] = []
    content: list[str] = []
    for chapter in publication.chapters:
        chapter_anchor = f"chapter-{chapter.index}"
        title = chapter.title or chapter.href
        toc.append(f'<a href="#{chapter_anchor}">{html.escape(title)}</a>')
        content.append(f'<section id="{chapter_anchor}"><h2>{html.escape(title)}</h2>')
        content.append(_render_reader_chapter_content(chapter, "epub-source"))
        content.append("</section>")
    library_nav = (
        render_book_navigation(library_books, bundle.book_id, "../../index.html", "../")
        if library_books
        else ""
    )
    standalone_controls = render_reader_controls("reader-controls-standalone") if not library_books else ""
    body = f'''<div class="layout"><aside class="toc"><strong>目录</strong>{"".join(toc)}</aside>
<main class="reader">{library_nav}<header class="reader-header"><div class="eyebrow">EPUB Reader</div>
<h1>{html.escape(publication.title)}</h1></header>
<p class="source-banner" translate="no">正文优先来自 EPUB。</p>
<section class="chapter-content" data-translate="main">{"".join(content)}</section>
</main></div>'''
    return _reader_document(
        publication.title,
        publication.language,
        body,
        "assets/reader.css",
        "assets/reader.js",
        style,
        standalone_controls,
    )


def render_reader(
    bundle: SourceBundle,
    publication: EpubPublication,
    output_dir: Path,
    style: str = "dark",
    layout: str = "chapter",
    chapter_number: int | None = None,
    library_books: list[LibraryBook] | None = None,
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
                _render_chapter_document(
                    bundle, publication, chapters, position, style, library_books
                ),
                encoding="utf-8",
            )
        (output_dir / "index.html").write_text(
            _render_index_document(
                bundle, publication, chapters, style, library_books
            ),
            encoding="utf-8",
        )
    if layout in {"merged", "both"}:
        (output_dir / "merged_book.html").write_text(
            _render_merged_document(bundle, publication, style, library_books),
            encoding="utf-8",
        )
    (output_dir / "README.md").write_text(
        f"# {publication.title}\n\n"
        "正文由 EPUB 生成。默认入口是 `index.html`，每章位于 `chapters/`；"
        "如启用兼容模式，整本内容位于 `merged_book.html`。\n"
        "原始 EPUB 位于 `source.epub`，可检查的 EPUB 内部文件位于 `epub-source/`。\n",
        encoding="utf-8",
    )
    return output_dir / ("merged_book.html" if layout == "merged" else "index.html")


def _render_myst_block(
    block: EpubBlock,
    heading_level: int | None = None,
    source_prefix: str = "../_static/epub-source",
) -> str:
    if block.kind == "heading":
        level = heading_level or max(2, min(6, (block.heading_level or 2) + 1))
        return f"{'#' * level} {block.text}\n\n"
    content = rewrite_asset_refs(block.html, source_prefix)
    return f"{content}\n\n"


def _render_myst_chapter(chapter, source_prefix: str) -> str:
    lines = [f"# {chapter.title or chapter.href}", ""]
    last_heading_level = 1
    for block in chapter.blocks:
        if block.kind == "heading":
            if (
                (block.heading_level or 0) == 1
                and block.text.strip() == (chapter.title or "").strip()
            ):
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
        "> 正文阅读层来自 EPUB。",
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
