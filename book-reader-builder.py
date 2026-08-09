#!/usr/bin/env python3
"""EPUB-first browser reader with PDF compatibility fallback."""

from __future__ import annotations

import argparse
import http.server
import importlib.util
import sys
import threading
import time
import webbrowser
from pathlib import Path
from types import ModuleType

from book_library import LibraryBook, render_library_index
from book_outputs import reader_css, reader_script, render_reader
from book_sources import (
    SourceBundle,
    bundle_from_input,
    discover_source_bundles,
    discover_sources,
    validate_output_dir,
)
from epub_parser import parse_epub


def _load_legacy_pdf_reader() -> ModuleType:
    script = Path(__file__).with_name("pdf-reader-builder.py")
    spec = importlib.util.spec_from_file_location("legacy_pdf_reader", script)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载 PDF reader: {script}")
    module = importlib.util.module_from_spec(spec)
    # dataclasses resolves postponed annotations through sys.modules while the
    # legacy module is being executed.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _start_server(host: str, port: int, directory: Path, entrypoint: str) -> None:
    class QuietHandler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(directory), **kwargs)

        def log_message(self, format: str, *args) -> None:
            del format, args

    server = http.server.HTTPServer((host, port), QuietHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://{host}:{port}/{entrypoint}"
    print(f"\n🌐 Server running at: {url}\n📂 Serving from: {directory}\n🛑 Press Ctrl+C to stop\n")
    try:
        webbrowser.open(url)
    except (OSError, webbrowser.Error):
        pass
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping server...")
        server.shutdown()


def build(
    bundle: SourceBundle,
    output_dir: Path,
    style: str,
    merge: bool,
    layout: str,
    chapter_number: int | None,
) -> Path:
    if bundle.epub:
        publication = parse_epub(bundle.epub)
        result = render_reader(
            bundle,
            publication,
            output_dir,
            style=style,
            layout=layout,
            chapter_number=chapter_number,
        )
        print(f"📚 EPUB preferred: {bundle.epub.name}")
        if bundle.pdf:
            print(f"🧾 PDF evidence:   {bundle.pdf.name}")
        print(f"✅ Written: {result}")
        return output_dir.resolve()

    if bundle.pdf is None:
        raise ValueError("source bundle has no input")
    legacy = _load_legacy_pdf_reader()
    return legacy.process_pdf(str(bundle.pdf), str(output_dir), merge, style)


def _pdf_title(path: Path) -> str:
    return path.stem.replace("_", " ").replace("-", " ")


def _prepare_library_books(
    bundles: list[SourceBundle],
    layout: str,
    merge: bool,
) -> tuple[list[LibraryBook], dict[str, object]]:
    books: list[LibraryBook] = []
    publications: dict[str, object] = {}
    seen_ids: set[str] = set()
    for bundle in bundles:
        if bundle.book_id in seen_ids:
            raise RuntimeError(f"书籍目录 ID 冲突: {bundle.key} -> {bundle.book_id}")
        seen_ids.add(bundle.book_id)
        if bundle.epub:
            publication = parse_epub(bundle.epub)
            title = publication.title
            entrypoint_name = "merged_book.html" if layout == "merged" else "index.html"
            book = LibraryBook(
                book_id=bundle.book_id,
                key=bundle.key,
                title=title,
                preferred="epub",
                paired=bundle.paired,
                entrypoint=f"books/{bundle.book_id}/{entrypoint_name}",
                epub_name=bundle.epub.name,
                pdf_name=bundle.pdf.name if bundle.pdf else None,
                chapter_count=len(publication.chapters),
                block_count=len(publication.blocks),
            )
            publications[bundle.book_id] = publication
        else:
            if bundle.pdf is None:
                raise ValueError(f"书籍没有 EPUB 或 PDF 输入: {bundle.key}")
            book = LibraryBook(
                book_id=bundle.book_id,
                key=bundle.key,
                title=_pdf_title(bundle.pdf),
                preferred="pdf",
                paired=False,
                entrypoint=f"books/{bundle.book_id}/"
                + ("merged_book.html" if merge else "page_0001.html"),
                pdf_name=bundle.pdf.name,
            )
        books.append(book)
    return books, publications


def build_library(
    bundles: list[SourceBundle],
    output_dir: Path,
    style: str,
    merge: bool,
    layout: str,
) -> Path:
    """Build every discovered book below one static library directory."""
    if not bundles:
        raise ValueError("书库没有可构建的书籍")
    if layout not in {"chapter", "merged", "both"}:
        raise ValueError(f"不支持的 EPUB 输出布局: {layout}")

    output_dir = output_dir.expanduser().resolve()
    for bundle in bundles:
        validate_output_dir(output_dir / "books" / bundle.book_id, bundle)
    books, publications = _prepare_library_books(bundles, layout, merge)
    output_dir.mkdir(parents=True, exist_ok=True)

    legacy = None
    for bundle, book in zip(bundles, books):
        book_dir = output_dir / "books" / bundle.book_id
        if bundle.epub:
            render_reader(
                bundle,
                publications[bundle.book_id],
                book_dir,
                style=style,
                layout=layout,
                library_books=books,
            )
        else:
            if legacy is None:
                legacy = _load_legacy_pdf_reader()
            legacy.process_pdf(
                str(bundle.pdf),
                str(book_dir),
                merge,
                style,
                library_books=books,
                book_id=book.book_id,
            )

    render_library_index(
        output_dir,
        books,
        style,
        reader_css(style),
        reader_script(),
    )
    return output_dir


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="EPUB-first book reader; falls back to the existing PDF reader."
    )
    parser.add_argument("input", nargs="?", type=Path, help="直接输入 EPUB/PDF；省略时扫描 source-* 目录")
    parser.add_argument("--source-dir", type=Path, default=Path("."), help="包含 source-epub/source-pdf 的目录")
    parser.add_argument("--book", help="源文件名或书名 stem，用于多本书时选择")
    parser.add_argument(
        "--library",
        action="store_true",
        help="兼容选项：扫描 source-* 时现在默认生成书库",
    )
    parser.add_argument("--output-dir", "-o", type=Path, default=Path("./pdf-web"))
    parser.add_argument("--merge", action="store_true", help="PDF-only 时合并成 merged_book.html")
    parser.add_argument(
        "--layout",
        choices=["chapter", "merged", "both"],
        default="chapter",
        help="EPUB 输出布局：chapter（默认）、merged 或 both",
    )
    parser.add_argument("--chapter", type=int, help="只生成指定编号的 EPUB 章节（从 1 开始）")
    parser.add_argument("--server", "-s", action="store_true")
    parser.add_argument("--port", "-p", type=int, default=8080)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--style", choices=["dark", "light", "sepia"], default="dark")
    return parser.parse_args()


def main() -> int:
    try:
        args = parse_args()
        if args.library and (args.input or args.book):
            raise ValueError("--library 不能和直接输入或 --book 同时使用")
        if args.input or args.book:
            bundle = bundle_from_input(args.input) if args.input else discover_sources(args.source_dir, args.book)
            bundles = None
        else:
            discovered = discover_source_bundles(args.source_dir)
            # Source-directory builds always have a stable library landing page,
            # even with only one book.  Use --book for an intentionally standalone
            # reader build.
            bundles = discovered
            bundle = None
        if bundles is not None:
            if args.chapter is not None:
                raise ValueError("书库模式不能使用 --chapter，请先用 --book 指定一本书")
            output_dir = build_library(
                bundles,
                args.output_dir,
                args.style,
                args.merge,
                args.layout,
            )
            print(f"📚 Library: {len(bundles)} books")
            print(f"✅ Written: {output_dir / 'index.html'}")
            if args.server:
                _start_server(args.host, args.port, output_dir, "index.html")
            return 0
        if bundle.pdf and bundle.epub is None and args.layout != "chapter":
            print("提示: PDF-only 模式忽略 --layout，使用 --merge 控制输出。", file=sys.stderr)
        output_dir = build(
            bundle,
            args.output_dir,
            args.style,
            args.merge,
            args.layout,
            args.chapter,
        )
        if args.server:
            if bundle.epub:
                entrypoint = "merged_book.html" if args.layout == "merged" else "index.html"
            else:
                entrypoint = "merged_book.html" if args.merge else "page_0001.html"
            _start_server(args.host, args.port, output_dir, entrypoint)
        return 0
    except (FileNotFoundError, OSError, RuntimeError, ValueError) as error:
        print(f"错误: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
