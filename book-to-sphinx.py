#!/usr/bin/env python3
"""Generate an EPUB-first Sphinx/MyST project with PDF fallback."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from book_outputs import render_sphinx
from book_sources import bundle_from_input, discover_sources
from epub_parser import parse_epub


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="EPUB-first book to Sphinx/MyST; PDF-only inputs use the existing pipeline."
    )
    parser.add_argument("input", nargs="?", type=Path, help="直接输入 EPUB/PDF；省略时扫描 source-* 目录")
    parser.add_argument("--source-dir", type=Path, default=Path("."), help="包含 source-epub/source-pdf 的目录")
    parser.add_argument("--book", help="源文件名或书名 stem，用于多本书时选择")
    parser.add_argument("-o", "--output", type=Path, required=True, help="输出 Sphinx 工程目录")
    parser.add_argument("--title", help="覆盖文档标题")
    parser.add_argument("--build", action="store_true", help="生成后构建 HTML")
    parser.add_argument(
        "--layout",
        choices=["chapter", "merged", "both"],
        default="chapter",
        help="EPUB Sphinx 输出布局：chapter（默认）、merged 或 both",
    )
    parser.add_argument("--no-page-images", action="store_true", help="兼容 PDF 参数；EPUB-first 模式无页图")
    return parser.parse_args()


def run_pdf_fallback(args: argparse.Namespace, pdf: Path) -> int:
    script = Path(__file__).with_name("pdf-to-sphinx.py")
    command = [sys.executable, str(script), str(pdf), "-o", str(args.output)]
    if args.title:
        command.extend(["--title", args.title])
    if args.no_page_images:
        command.append("--no-page-images")
    if args.build:
        command.append("--build")
    return subprocess.run(command, check=False).returncode


def main() -> int:
    try:
        args = parse_args()
        bundle = bundle_from_input(args.input) if args.input else discover_sources(args.source_dir, args.book)
        if bundle.epub is None:
            if bundle.pdf is None:
                raise ValueError("source bundle has no input")
            if args.layout != "chapter":
                print("提示: PDF-only 模式忽略 --layout，使用原有 PDF 管线。", file=sys.stderr)
            return run_pdf_fallback(args, bundle.pdf)

        publication = parse_epub(bundle.epub)
        if args.title:
            publication.title = args.title
        result = render_sphinx(
            bundle,
            publication,
            args.output,
            build=args.build,
            layout=args.layout,
        )
        print(f"📚 EPUB preferred: {bundle.epub.name}")
        if bundle.pdf:
            print(f"🧾 PDF evidence:   {bundle.pdf.name}")
        print(f"✅ Written: {args.output.resolve()}")
        return result
    except (FileNotFoundError, OSError, RuntimeError, ValueError) as error:
        print(f"错误: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
