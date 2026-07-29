#!/usr/bin/env python3
"""
Extract one chapter from a PDF without re-rendering it.

The script uses the PDF's built-in outline/bookmarks to find the chapter
boundaries, then copies the original pages into a new PDF. This preserves the
source PDF's typography, images, links, annotations, and page geometry.

Examples:
    python pdf-chapter-extractor.py book.pdf --chapter 3
    python pdf-chapter-extractor.py book.pdf --chapter 3 -o chapter-3.pdf
    python pdf-chapter-extractor.py book.pdf --list
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

try:
    import fitz  # PyMuPDF
except ImportError:
    print("缺少 PyMuPDF，请先安装: python -m pip install PyMuPDF", file=sys.stderr)
    raise SystemExit(1)


@dataclass(frozen=True)
class OutlineEntry:
    level: int
    title: str
    page: int  # 1-based PDF page number
    order: int


@dataclass(frozen=True)
class Chapter:
    number: int
    title: str
    start_page: int
    end_page: int
    outline_order: int


def clean_title(title: str) -> str:
    return re.sub(r"\s+", " ", title.replace("\u00a0", " ")).strip()


def chapter_number(title: str) -> int | None:
    """Extract a leading chapter number from common bookmark title formats."""
    match = re.match(
        r"^\s*(?:chapter\s*)?(\d+)(?=\s|[.:)\-]|$)",
        title,
        flags=re.IGNORECASE,
    )
    return int(match.group(1)) if match else None


def read_outline(doc: fitz.Document) -> list[OutlineEntry]:
    entries: list[OutlineEntry] = []
    for order, item in enumerate(doc.get_toc(), start=1):
        if len(item) < 3:
            continue
        level, title, page = int(item[0]), clean_title(str(item[1])), int(item[2])
        if title and page > 0:
            entries.append(OutlineEntry(level, title, page, order))
    return entries


def find_chapters(outline: list[OutlineEntry], total_pages: int) -> list[Chapter]:
    """Build chapter ranges from level-1 outline entries.

    A chapter ends immediately before the next level-1 chapter entry. Other
    level-1 entries such as ``contents`` are ignored because they do not begin
    with a numeric chapter number.
    """
    roots = [entry for entry in outline if entry.level == 1 and chapter_number(entry.title) is not None]
    chapters: list[Chapter] = []
    for index, entry in enumerate(roots):
        next_page = roots[index + 1].page if index + 1 < len(roots) else total_pages + 1
        start_page = max(1, min(entry.page, total_pages))
        end_page = max(start_page, min(total_pages, next_page - 1))
        chapters.append(
            Chapter(
                number=chapter_number(entry.title) or 0,
                title=entry.title,
                start_page=start_page,
                end_page=end_page,
                outline_order=entry.order,
            )
        )
    return chapters


def find_requested_chapter(chapters: list[Chapter], number: int) -> Chapter:
    for chapter in chapters:
        if chapter.number == number:
            return chapter
    available = ", ".join(str(chapter.number) for chapter in chapters) or "none"
    raise ValueError(f"找不到第 {number} 章。PDF 中可用的章节编号: {available}")


def output_path_for(input_pdf: Path, chapter_number_value: int) -> Path:
    return input_pdf.with_name(
        f"{input_pdf.stem}_chapter_{chapter_number_value:02d}{input_pdf.suffix}"
    )


def build_chapter_outline(
    outline: list[OutlineEntry],
    chapter: Chapter,
) -> list[list[int | str]]:
    """Keep bookmarks inside the extracted page range and rebase their pages."""
    selected = [
        entry
        for entry in outline
        if chapter.start_page <= entry.page <= chapter.end_page
        and entry.order >= chapter.outline_order
    ]
    if not selected or selected[0].order != chapter.outline_order:
        selected.insert(0, OutlineEntry(1, chapter.title, chapter.start_page, chapter.outline_order))

    result: list[list[int | str]] = []
    for entry in selected:
        # Do not let an unrelated numeric level-1 entry leak into the output.
        if entry.level == 1 and entry.order != chapter.outline_order:
            continue
        result.append(
            [
                entry.level,
                entry.title,
                entry.page - chapter.start_page + 1,
            ]
        )
    return result


def extract_chapter(
    input_pdf: Path,
    output_pdf: Path,
    chapter: Chapter,
    outline: list[OutlineEntry],
) -> None:
    if input_pdf.resolve() == output_pdf.resolve():
        raise ValueError("输出文件不能覆盖输入 PDF，请使用不同的 --output 路径。")
    output_pdf.parent.mkdir(parents=True, exist_ok=True)

    source = fitz.open(str(input_pdf))
    result = fitz.open()
    try:
        result.insert_pdf(
            source,
            from_page=chapter.start_page - 1,
            to_page=chapter.end_page - 1,
        )
        result.set_toc(build_chapter_outline(outline, chapter))
        result.set_metadata(source.metadata)
        result.save(str(output_pdf), garbage=4, deflate=True)
    finally:
        result.close()
        source.close()


def list_chapters(chapters: list[Chapter]) -> None:
    if not chapters:
        print("PDF 没有找到可识别的章节书签。")
        return
    print("可提取的章节:")
    for chapter in chapters:
        print(
            f"  第 {chapter.number} 章  "
            f"pages {chapter.start_page}-{chapter.end_page}  "
            f"{chapter.title}"
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="按 PDF 内置书签截取一整章，输出保持原版式的新 PDF。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            "  python pdf-chapter-extractor.py book.pdf --list\n"
            "  python pdf-chapter-extractor.py book.pdf --chapter 3\n"
            "  python pdf-chapter-extractor.py book.pdf --chapter 3 -o ./output/chapter-3.pdf"
        ),
    )
    parser.add_argument("input_pdf", type=Path, help="输入 PDF 文件")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--chapter", "-c", type=int, help="要提取的章节编号，例如 3")
    group.add_argument("--list", action="store_true", help="列出 PDF 中识别到的章节")
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        help="输出 PDF 路径；默认写入输入 PDF 同目录，并追加 _chapter_NN",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_pdf: Path = args.input_pdf.expanduser()
    if not input_pdf.is_file():
        print(f"找不到输入文件: {input_pdf}", file=sys.stderr)
        return 2

    doc = fitz.open(str(input_pdf))
    try:
        outline = read_outline(doc)
        chapters = find_chapters(outline, len(doc))
    finally:
        doc.close()

    if args.list:
        list_chapters(chapters)
        return 0

    try:
        chapter = find_requested_chapter(chapters, args.chapter)
        output_pdf = (args.output or output_path_for(input_pdf, chapter.number)).expanduser()
        extract_chapter(input_pdf, output_pdf, chapter, outline)
    except (ValueError, RuntimeError) as error:
        print(f"错误: {error}", file=sys.stderr)
        return 1

    page_count = chapter.end_page - chapter.start_page + 1
    print(f"已提取第 {chapter.number} 章: {chapter.title}")
    print(f"源 PDF 页面: {chapter.start_page}-{chapter.end_page} ({page_count} pages)")
    print(f"输出文件: {output_pdf.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
