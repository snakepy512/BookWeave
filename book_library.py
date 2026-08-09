"""Shared metadata and HTML helpers for the generated multi-book library."""

from __future__ import annotations

import html
import json
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class LibraryBook:
    """Metadata needed by the library index and in-reader book switcher."""

    book_id: str
    key: str
    title: str
    preferred: str
    paired: bool
    entrypoint: str
    epub_name: str | None = None
    pdf_name: str | None = None
    chapter_count: int | None = None
    block_count: int | None = None
    page_count: int | None = None

    @property
    def entrypoint_name(self) -> str:
        return self.entrypoint.rsplit("/", 1)[-1]

    def with_entrypoint(self, entrypoint: str) -> "LibraryBook":
        return replace(self, entrypoint=entrypoint)

    def as_json(self) -> dict[str, object]:
        return {
            "book_id": self.book_id,
            "key": self.key,
            "title": self.title,
            "preferred": self.preferred,
            "paired": self.paired,
            "entrypoint": self.entrypoint,
            "epub": self.epub_name,
            "pdf": self.pdf_name,
            "chapter_count": self.chapter_count,
            "block_count": self.block_count,
            "page_count": self.page_count,
        }


def render_book_navigation(
    books: list[LibraryBook],
    current_book_id: str,
    library_href: str,
    book_href_prefix: str,
) -> str:
    """Render a relative-path book switcher for a generated book page."""
    options: list[str] = []
    for book in books:
        href = f"{book_href_prefix}{book.book_id}/{book.entrypoint_name}"
        selected = " selected" if book.book_id == current_book_id else ""
        options.append(
            f'<option value="{html.escape(href, quote=True)}"{selected}>'
            f"{html.escape(book.title)}</option>"
        )
    return (
        '<nav class="library-nav" aria-label="书籍导航">'
        f'<a href="{html.escape(library_href, quote=True)}">📚 书库</a>'
        '<label>切换书籍 '
        '<select data-book-switcher aria-label="切换书籍">'
        + "".join(options)
        + "</select></label></nav>"
    )


def render_library_index(
    output_dir: Path,
    books: list[LibraryBook],
    style: str,
    css: str,
    js: str,
) -> Path:
    """Write the generated library landing page and its JSON catalog."""
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "assets").mkdir(parents=True, exist_ok=True)
    (output_dir / "assets/reader.css").write_text(css, encoding="utf-8")
    (output_dir / "assets/reader.js").write_text(js, encoding="utf-8")
    (output_dir / "library.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "books": [book.as_json() for book in books],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    items: list[str] = []
    for book in books:
        formats = " / ".join(
            value for value in ("EPUB" if book.epub_name else None, "PDF" if book.pdf_name else None)
            if value
        )
        counts = []
        if book.chapter_count is not None:
            counts.append(f"{book.chapter_count} 章")
        if book.page_count is not None:
            counts.append(f"{book.page_count} 页")
        meta = " · ".join(value for value in (formats, *counts) if value)
        items.append(
            f'<li data-book-item><a class="library-card" '
            f'href="{html.escape(book.entrypoint, quote=True)}">'
            f'<span class="library-card-title">{html.escape(book.title)}</span>'
            f'<span class="library-card-meta">{html.escape(meta or book.preferred.upper())}</span>'
            "</a></li>"
        )

    body = f'''<div class="layout"><main class="reader">
<header class="reader-header">
<div class="eyebrow">BookWeave Library</div>
<h1>书库</h1>
<p class="reader-subtitle">共 {len(books)} 本书；选择一本开始阅读。</p>
</header>
<section class="chapter-index" aria-labelledby="library-title">
<h2 id="library-title">我的书籍</h2>
<input class="chapter-filter" data-book-filter type="search" placeholder="搜索书名" aria-label="搜索书名">
<ul class="library-list">{"".join(items)}</ul>
</section>
</main></div>'''
    document = f'''<!doctype html>
<html lang="zh-CN" data-default-theme="{html.escape(style, quote=True)}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>书库 - BookWeave</title>
<link rel="stylesheet" href="assets/reader.css">
</head>
<body>{body}
<button class="theme-toggle" data-theme-toggle type="button" aria-label="切换到浅色模式">浅色模式</button>
<script src="assets/reader.js" defer></script>
</body>
</html>
'''
    index = output_dir / "index.html"
    index.write_text(document, encoding="utf-8")
    return index.resolve()
