#!/usr/bin/env python3
"""
PDF -> Sphinx/MyST 双轨文档生成器。

这个脚本把 PDF 转成一个可重复构建的 Sphinx 工程，同时保留原 PDF 与页级
预览作为证据层。核心中间文件是 document.json：正文 block 都带有
source_page、bbox、block_id 和字体/字号等元数据，方便后续人工修订或重跑。

当前版本使用 PyMuPDF 完成原生文本 PDF 的核心管线；正文以可提取的文本层为准，
并保留原 PDF 与页级预览作为核验入口。

示例:
    python pdf-to-sphinx.py output/chapter-3.pdf -o output/chapter-3-sphinx
    python pdf-to-sphinx.py book.pdf -o output/book-sphinx --build
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import fitz  # PyMuPDF
except ImportError:
    print("缺少 PyMuPDF，请先安装: python -m pip install PyMuPDF", file=sys.stderr)
    raise SystemExit(1)


TOOL_VERSION = "0.2.0"


@dataclass
class Block:
    block_id: str
    source_page: int
    order: int
    kind: str
    text: str = ""
    lines: list[str] = field(default_factory=list)
    line_spans: list[list[dict[str, Any]]] = field(default_factory=list)
    bbox: list[float] = field(default_factory=list)
    style: dict[str, Any] = field(default_factory=dict)
    asset: str | None = None
    flags: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class OutlineEntry:
    level: int
    title: str
    page: int
    anchor: str
    order: int

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def clean_text(value: str) -> str:
    value = value.replace("\u00a0", " ").replace("\uf0a1", "•").replace("\uf0a2", "•")
    return value


def flat_text(value: str) -> str:
    return re.sub(r"\s+", " ", clean_text(value)).strip()


def slugify(value: str) -> str:
    value = re.sub(r"[^\w\u4e00-\u9fff]+", "-", value.lower(), flags=re.UNICODE).strip("-")
    return value or "section"


def outline_title_without_number(title: str) -> str:
    return re.sub(r"^\d+(?:\.\d+)*\s+", "", flat_text(title))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def span_style(spans: list[dict[str, Any]]) -> dict[str, Any]:
    sizes = [float(span.get("size", 0)) for span in spans if span.get("size")]
    fonts = sorted({str(span.get("font", "")) for span in spans if span.get("font")})
    flags = sorted({int(span.get("flags", 0)) for span in spans})
    colors = sorted({int(span.get("color", 0)) for span in spans if span.get("color") is not None})
    font_text = " ".join(fonts).lower()
    meaningful_spans = [span for span in spans if str(span.get("text", "")).strip()]
    meaningful_fonts = [str(span.get("font", "")).lower() for span in meaningful_spans]
    return {
        "font_names": fonts,
        "font_size_min": min(sizes) if sizes else 0,
        "font_size_max": max(sizes) if sizes else 0,
        "flags": flags,
        "colors": colors,
        # Inline code may coexist with normal prose in the same PDF block. A
        # block is a code block only when all meaningful spans are monospace;
        # individual monospace spans are still rendered as inline code later.
        "monospace": bool(meaningful_fonts) and all(
            "courier" in font or "mono" in font or bool(int(span.get("flags", 0)) & 8)
            for span, font in zip(meaningful_spans, meaningful_fonts)
        ),
        "bold": "bold" in font_text or any(flag & 16 for flag in flags),
        "italic": "italic" in font_text or any(flag & 2 for flag in flags),
    }


def block_from_raw(page_number: int, order: int, raw: dict[str, Any]) -> Block | None:
    bbox = [round(float(value), 2) for value in raw.get("bbox", [])]
    raw_type = int(raw.get("type", 0))
    block_id = f"p{page_number:04d}-b{order:04d}"
    if raw_type == 1:
        return Block(
            block_id=block_id,
            source_page=page_number,
            order=order,
            kind="image",
            bbox=bbox,
            style={"width": raw.get("width"), "height": raw.get("height"), "ext": raw.get("ext", "png")},
        )

    if "lines" not in raw:
        return None
    lines: list[str] = []
    line_spans: list[list[dict[str, Any]]] = []
    all_spans: list[dict[str, Any]] = []
    for line in raw.get("lines", []):
        spans: list[dict[str, Any]] = []
        for raw_span in line.get("spans", []):
            span = {
                "text": str(raw_span.get("text", "")),
                "font": str(raw_span.get("font", "")),
                "size": round(float(raw_span.get("size", 0)), 2),
                "flags": int(raw_span.get("flags", 0)),
                "color": int(raw_span.get("color", 0)),
                "bbox": [round(float(value), 2) for value in raw_span.get("bbox", [])],
            }
            spans.append(span)
            all_spans.append(span)
        line_spans.append(spans)
        lines.append("".join(span["text"] for span in spans))
    text = "\n".join(lines)
    if not flat_text(text):
        return None
    return Block(
        block_id=block_id,
        source_page=page_number,
        order=order,
        kind="text",
        text=text,
        lines=lines,
        line_spans=line_spans,
        bbox=bbox,
        style=span_style(all_spans),
    )


def is_page_number(text: str) -> bool:
    return bool(re.fullmatch(r"(?:\d+|[ivxlcdm]+)", flat_text(text), flags=re.IGNORECASE))


def repeated_edge_key(block: Block, page_height: float) -> str | None:
    if not block.bbox or not block.text:
        return None
    y0, y1 = block.bbox[1], block.bbox[3]
    if y0 > page_height * 0.14 and y1 < page_height * 0.86:
        return None
    value = flat_text(block.text).lower()
    value = re.sub(r"^(?:\d+|[ivxlcdm]+)\s+", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\s+", " ", value)
    return value if len(value) >= 8 else None


def classify_text_block(block: Block) -> str:
    text = flat_text(block.text)
    first = flat_text(block.lines[0] if block.lines else block.text)
    if re.match(r"^[•·*]\s*", first):
        return "list-item"
    if re.match(r"^(?:table|figure)\s+\d", text, flags=re.IGNORECASE):
        return "caption"
    if first.upper() in {"NOTE", "TIP", "LINGO"} or first.lower().startswith("lingo:"):
        return "callout"
    if block.style.get("monospace") and block.style.get("font_size_max", 0) <= 11:
        block.style["console"] = bool(
            re.search(r"^\s*-\[\s*RECORD\b", "\n".join(block.lines), flags=re.MULTILINE)
        )
        return "code"
    return "paragraph"


def read_outline(doc: fitz.Document) -> list[OutlineEntry]:
    result: list[OutlineEntry] = []
    seen: dict[str, int] = {}
    for order, item in enumerate(doc.get_toc(), start=1):
        if len(item) < 3:
            continue
        level, title, page = int(item[0]), flat_text(str(item[1])), int(item[2])
        if not title or page < 1:
            continue
        base = f"section-{slugify(title)}"
        seen[base] = seen.get(base, 0) + 1
        anchor = base if seen[base] == 1 else f"{base}-{seen[base]}"
        result.append(OutlineEntry(level, title, page, anchor, order))
    return result


def page_drawing_regions(page: fitz.Page) -> list[dict[str, Any]]:
    regions: list[dict[str, Any]] = []
    for drawing in page.get_drawings():
        rect = drawing.get("rect")
        fill = drawing.get("fill")
        if not rect or fill is None:
            continue
        regions.append(
            {
                "rect": [round(float(value), 2) for value in rect],
                "fill": [round(float(value), 4) for value in fill],
            }
        )
    return regions


def raw_pages(
    doc: fitz.Document,
) -> tuple[list[list[Block]], dict[str, Any], list[list[dict[str, Any]]]]:
    pages: list[list[Block]] = []
    page_infos: list[dict[str, Any]] = []
    page_drawings: list[list[dict[str, Any]]] = []
    for page_index, page in enumerate(doc, start=1):
        drawings = page_drawing_regions(page)
        raw = page.get_text("dict", sort=True)
        blocks: list[Block] = []
        text_chars = 0
        image_blocks = 0
        for order, raw_block in enumerate(raw.get("blocks", []), start=1):
            block = block_from_raw(page_index, order, raw_block)
            if block is None:
                continue
            if block.kind == "image":
                image_blocks += 1
            else:
                text_chars += len(flat_text(block.text))
            blocks.append(block)
        page_infos.append(
            {
                "page": page_index,
                "width": round(page.rect.width, 2),
                "height": round(page.rect.height, 2),
                "native_text_chars": text_chars,
                "image_blocks": image_blocks,
                "drawing_regions": len(drawings),
                "classification": (
                    "native-text" if text_chars else
                    "image-only" if image_blocks else
                    "empty"
                ),
            }
        )
        page_drawings.append(drawings)
        pages.append(blocks)

    edge_counts: dict[str, int] = {}
    for page_index, blocks in enumerate(pages):
        height = float(page_infos[page_index]["height"])
        for block in blocks:
            key = repeated_edge_key(block, height)
            if key:
                edge_counts[key] = edge_counts.get(key, 0) + 1
    repeat_threshold = max(2, min(5, len(pages) // 8 or 2))
    repeated_edges = {key for key, count in edge_counts.items() if count >= repeat_threshold}

    filtered_pages: list[list[Block]] = []
    dropped_headers = 0
    dropped_footers = 0
    for page_index, blocks in enumerate(pages):
        height = float(page_infos[page_index]["height"])
        filtered: list[Block] = []
        for block in blocks:
            value = flat_text(block.text)
            y0, y1 = (block.bbox[1], block.bbox[3]) if block.bbox else (0, 0)
            edge = repeated_edge_key(block, height)
            footer_number = y0 >= height * 0.86 and is_page_number(value)
            licensed = value.lower().startswith("licensed to ")
            if licensed or footer_number or (edge in repeated_edges and (y0 <= height * 0.14 or y1 >= height * 0.86)):
                if footer_number or y1 >= height * 0.86:
                    dropped_footers += 1
                else:
                    dropped_headers += 1
                continue
            if block.kind == "text":
                block.kind = classify_text_block(block)
            filtered.append(block)
        filtered_pages.append(filtered)

    counts = {info["classification"]: 0 for info in page_infos}
    for info in page_infos:
        counts[info["classification"]] += 1
    intake = {
        "total_pages": len(page_infos),
        "pages_by_classification": counts,
        "native_text_chars": sum(info["native_text_chars"] for info in page_infos),
        "image_blocks": sum(info["image_blocks"] for info in page_infos),
        "repeated_edge_signatures": sorted(repeated_edges),
        "dropped_repeated_headers": dropped_headers,
        "dropped_page_numbers_or_footers": dropped_footers,
        "page_infos": page_infos,
    }
    return filtered_pages, intake, page_drawings


def coalesce_wrapped_lists(blocks: list[Block]) -> list[Block]:
    result: list[Block] = []
    for block in blocks:
        if result:
            previous = result[-1]
            close_y = bool(previous.bbox and block.bbox and block.bbox[1] - previous.bbox[3] <= 10)
            continuation = previous.kind == "list-item" and block.kind == "paragraph" and close_y
            if continuation:
                previous.lines.extend(block.lines)
                previous.line_spans.extend(block.line_spans)
                previous.text = "\n".join(previous.lines)
                previous.bbox[3] = block.bbox[3]
                previous.style.setdefault("merged_block_ids", [previous.block_id]).append(block.block_id)
                continue
        result.append(block)
    return result


def positioned_code_rows(blocks: list[Block]) -> list[list[dict[str, Any]]]:
    spans: list[dict[str, Any]] = []
    for block in blocks:
        for line_spans in block.line_spans:
            for span in line_spans:
                text = clean_text(str(span.get("text", "")))
                bbox = span.get("bbox", [])
                if text.strip() and len(bbox) >= 4:
                    spans.append(
                        {
                            **span,
                            "text": text,
                            "y": float(bbox[1]),
                            "y0": float(bbox[1]),
                            "y1": float(bbox[3]),
                            "x0": float(bbox[0]),
                            "x1": float(bbox[2]),
                        }
                    )
    if not spans:
        return []

    rows: list[list[dict[str, Any]]] = []
    for span in sorted(spans, key=lambda item: (item["y"], item["x0"])):
        if rows and abs(span["y"] - rows[-1][0]["y"]) <= 1.5:
            rows[-1].append(span)
        else:
            rows.append([span])
    return rows


def geometric_code_lines(blocks: list[Block]) -> list[str]:
    """Rebuild monospace rows from PDF coordinates."""
    rows = positioned_code_rows(blocks)
    if not rows:
        return [clean_text(line).rstrip() for block in blocks for line in block.lines]

    spans = [span for row in rows for span in row]

    rendered: list[str] = []
    origin_x = min(float(span["x0"]) for span in spans)
    for row in rows:
        row.sort(key=lambda item: item["x0"])
        parts: list[str] = []
        previous: dict[str, Any] | None = None
        for span in row:
            value = span["text"].strip()
            if not value:
                continue
            if previous is None:
                char_width = max(3.5, float(span.get("size", 8)) * 0.6)
                indent = max(0, round((span["x0"] - origin_x) / char_width))
                parts.append(" " * indent + value)
            else:
                gap = span["x0"] - previous["x1"]
                char_width = max(3.5, float(span.get("size", 8)) * 0.6)
                explicit_space = span["text"][:1].isspace() or previous["text"][-1:].isspace()
                spaces = max(1, round(gap / char_width)) if gap >= 1.5 else (1 if explicit_space else 0)
                parts.append(" " * spaces + value)
            previous = span
        line = "".join(parts).rstrip()
        if line:
            rendered.append(line)
    return rendered


def tableish_block(block: Block) -> bool:
    lines = [clean_text(line) for line in block.lines]
    pipe_rows = [line for line in lines if "|" in line]
    has_psql_separator = any(
        re.fullmatch(r"\s*-{3,}(?:\s*[+|]\s*-{2,})+\s*", line)
        for line in lines
    )
    if pipe_rows and has_psql_separator:
        return True
    positioned = [
        span
        for line_spans in block.line_spans
        for span in line_spans
        if str(span.get("text", "")).strip() and len(span.get("bbox", [])) >= 4
    ]
    for span in sorted(positioned, key=lambda item: (item["bbox"][1], item["bbox"][0])):
        same_row = [
            candidate
            for candidate in positioned
            if abs(float(candidate["bbox"][1]) - float(span["bbox"][1])) <= 1.5
        ]
        same_row.sort(key=lambda item: item["bbox"][0])
        if len(same_row) >= 3:
            gaps = [
                float(right["bbox"][0]) - float(left["bbox"][2])
                for left, right in zip(same_row, same_row[1:])
            ]
            if max(gaps, default=0) >= 30:
                return True
    return False


def coalesce_code_tables(blocks: list[Block]) -> list[Block]:
    """Merge adjacent psql/table output blocks before rendering."""
    def is_visual_table(group: list[Block]) -> bool:
        # A PDF table and a psql result look similar to the text extractor,
        # but they need different HTML representations.  The former should
        # become a semantic table so its coloured header survives in the
        # browser; the latter must stay a preformatted console transcript so
        # pipes, padding and wrapped command output remain faithful.
        return group[0].kind == "paragraph"

    def is_table_candidate(block: Block) -> bool:
        if block.kind == "code":
            return True
        if block.kind != "paragraph":
            return False
        fonts = " ".join(str(font).lower() for font in block.style.get("font_names", []))
        return "courier" in fonts or "mono" in fonts

    result: list[Block] = []
    index = 0
    while index < len(blocks):
        block = blocks[index]
        if block.kind not in {"code", "paragraph"} or not tableish_block(block):
            result.append(block)
            index += 1
            continue
        group = [block]
        next_index = index + 1
        while next_index < len(blocks):
            candidate = blocks[next_index]
            if not is_table_candidate(candidate) or not candidate.bbox or not group[-1].bbox:
                break
            vertical_gap = candidate.bbox[1] - group[-1].bbox[3]
            if vertical_gap > 14:
                break
            group.append(candidate)
            next_index += 1
        if len(group) == 1:
            group[0].kind = "table"
            group[0].style["visual_table"] = is_visual_table(group)
            group[0].lines = geometric_code_lines(group)
            group[0].text = "\n".join(group[0].lines)
            result.append(group[0])
        else:
            merged_block_ids = [item.block_id for item in group]
            merged = Block(
                **{
                    **asdict(group[0]),
                    "kind": "table",
                    "style": {
                        **group[0].style,
                        "visual_table": is_visual_table(group),
                        "merged_block_ids": merged_block_ids,
                    },
                    "text": "\n".join(geometric_code_lines(group)),
                    "lines": geometric_code_lines(group),
                    "line_spans": [span for item in group for span in item.line_spans],
                    "bbox": [
                        min(item.bbox[0] for item in group),
                        min(item.bbox[1] for item in group),
                        max(item.bbox[2] for item in group),
                        max(item.bbox[3] for item in group),
                    ],
                }
            )
            result.append(merged)
        index = next_index
    return result


def rect_intersects(left: list[float], right: list[float]) -> bool:
    return left[0] < right[2] and left[2] > right[0] and left[1] < right[3] and left[3] > right[1]


def normalize_pages(
    pages: list[list[Block]],
    page_drawings: list[list[dict[str, Any]]] | None = None,
) -> list[list[Block]]:
    """Apply block coalescing and attach page geometry before serialization."""
    page_drawings = page_drawings or [[] for _ in pages]
    normalized_pages: list[list[Block]] = []
    for page_index, page in enumerate(pages):
        blocks = coalesce_code_tables(coalesce_wrapped_lists(page))
        regions = page_drawings[page_index] if page_index < len(page_drawings) else []
        for block in blocks:
            if block.style.get("visual_table") and block.bbox:
                block.style["fill_regions"] = [
                    region for region in regions if rect_intersects(region["rect"], block.bbox)
                ]
        normalized_pages.append(blocks)
    return normalized_pages


def pdf_color_css(value: Any) -> str | None:
    """Convert PyMuPDF's packed span color or RGB fill to a CSS color."""
    if isinstance(value, int):
        red = (value >> 16) & 0xFF
        green = (value >> 8) & 0xFF
        blue = value & 0xFF
    elif isinstance(value, (list, tuple)) and len(value) >= 3:
        red, green, blue = (round(max(0.0, min(1.0, float(channel))) * 255) for channel in value[:3])
    else:
        return None
    return f"#{red:02x}{green:02x}{blue:02x}"


def should_preserve_pdf_color(color: str | None) -> bool:
    """Keep semantic PDF colors while letting theme text colors adapt."""
    return bool(color and color not in {"#000000", "#262626"})


def table_column_edges(block: Block, rows: list[list[dict[str, Any]]]) -> list[float]:
    """Infer table columns from drawn grid lines, then from text geometry."""
    if not block.bbox:
        return []
    regions = block.style.get("fill_regions", [])
    relevant_rects = [region["rect"] for region in regions if rect_intersects(region["rect"], block.bbox)]
    left = min([block.bbox[0], *(rect[0] for rect in relevant_rects)])
    right = max([block.bbox[2], *(rect[2] for rect in relevant_rects)])
    height = block.bbox[3] - block.bbox[1]

    grid_lines = sorted(
        {
            round((rect[0] + rect[2]) / 2, 2)
            for rect in relevant_rects
            if rect[2] - rect[0] <= 2 and rect[3] - rect[1] >= max(14, height * 0.45)
            and left + 1 < (rect[0] + rect[2]) / 2 < right - 1
        }
    )
    if grid_lines:
        return [left, *grid_lines, right]

    header = rows[0] if rows else []
    starts = sorted({round(float(span["x0"]), 2) for span in header})
    if len(starts) < 2:
        candidate_rows: list[list[float]] = []
        for row in rows:
            row_starts: list[float] = []
            previous_end: float | None = None
            for span in sorted(row, key=lambda item: item["x0"]):
                if previous_end is None or span["x0"] - previous_end >= 8:
                    row_starts.append(float(span["x0"]))
                previous_end = max(previous_end or span["x1"], span["x1"])
            candidate_rows.append(row_starts)
        starts = max(candidate_rows, key=len, default=[])
    if len(starts) < 2:
        return [left, right]
    return [left, *[(a + b) / 2 for a, b in zip(starts, starts[1:])], right]


def column_for(x: float, edges: list[float]) -> int:
    for index, right in enumerate(edges[1:]):
        if x < right:
            return index
    return max(0, len(edges) - 2)


def table_row_cells(row: list[dict[str, Any]], edges: list[float]) -> list[dict[str, Any]]:
    cells: dict[int, dict[str, Any]] = {}
    for span in sorted(row, key=lambda item: item["x0"]):
        start = column_for(float(span["x0"]), edges)
        end = column_for(max(float(span["x0"]), float(span["x1"]) - 0.01), edges)
        cell = cells.setdefault(start, {"start": start, "end": end, "parts": []})
        cell["end"] = max(cell["end"], end)
        cell["parts"].append(span)
    return sorted(cells.values(), key=lambda cell: cell["start"])


def cell_text(parts: list[dict[str, Any]]) -> str:
    return " ".join(clean_text(str(part["text"])).strip() for part in parts if str(part["text"]).strip())


def cell_style(parts: list[dict[str, Any]], fill_regions: list[dict[str, Any]], tag: str) -> str:
    declarations: list[str] = []
    colors = [pdf_color_css(part.get("color")) for part in parts]
    colors = [color for color in colors if color]
    if colors and (tag == "th" or any(should_preserve_pdf_color(color) for color in colors)):
        declarations.append(f"color: {max(dict.fromkeys(colors), key=colors.count)}")
    if parts:
        part_box = [
            min(float(part["x0"]) for part in parts),
            min(float(part.get("y0", part.get("y", 0))) for part in parts),
            max(float(part["x1"]) for part in parts),
            max(float(part.get("y1", part.get("y", 0))) for part in parts),
        ]
        fills = [
            region for region in fill_regions
            if region["rect"][2] - region["rect"][0] > 4
            and region["rect"][3] - region["rect"][1] > 4
            and rect_intersects(region["rect"], part_box)
        ]
        if fills:
            declarations.append(f"background-color: {pdf_color_css(fills[0].get('fill'))}")
    if not declarations:
        return ""
    return f' style="{html.escape("; ".join(declarations), quote=True)}"'


def render_table_row(cells: list[dict[str, Any]], column_count: int, tag: str, fill_regions: list[dict[str, Any]]) -> str:
    rendered: list[str] = []
    cursor = 0
    for cell in cells:
        while cursor < cell["start"]:
            rendered.append(f"<{tag}></{tag}>")
            cursor += 1
        colspan = max(1, cell["end"] - cell["start"] + 1)
        colspan_attr = f' colspan="{colspan}"' if colspan > 1 else ""
        style = cell_style(cell["parts"], fill_regions, tag)
        text = html.escape(cell_text(cell["parts"]))
        content = text if tag == "th" else f"<code>{text}</code>"
        rendered.append(f"<{tag}{colspan_attr}{style}>{content}</{tag}>")
        cursor = cell["end"] + 1
    while cursor < column_count:
        rendered.append(f"<{tag}></{tag}>")
        cursor += 1
    return f"<tr>{''.join(rendered)}</tr>"


def render_visual_table(block: Block) -> str:
    rows = positioned_code_rows([block])
    if not rows:
        code = html.escape("\n".join(block.lines))
        return f'<pre class="pdf-console"><code>{code}</code></pre>'

    edges = table_column_edges(block, rows)
    if len(edges) < 2:
        code = html.escape("\n".join(block.lines))
        return f'<pre class="pdf-console"><code>{code}</code></pre>'
    column_count = len(edges) - 1
    fill_regions = block.style.get("fill_regions", [])
    header = table_row_cells(rows[0], edges)
    if not header:
        code = html.escape("\n".join(block.lines))
        return f'<pre class="pdf-console"><code>{code}</code></pre>'

    logical_rows: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] | None = None
    for row in rows[1:]:
        cells = table_row_cells(row, edges)
        has_first_column = any(cell["start"] == 0 for cell in cells)
        if current is None or has_first_column:
            if current is not None:
                logical_rows.append(current)
            current = cells
        else:
            for cell in cells:
                existing = next((item for item in current if item["start"] == cell["start"]), None)
                if existing is None:
                    current.append(cell)
                else:
                    existing["end"] = max(existing["end"], cell["end"])
                    existing["parts"].extend(cell["parts"])
    if current is not None:
        logical_rows.append(current)

    head_html = render_table_row(header, column_count, "th", fill_regions)
    body_html = "".join(render_table_row(row, column_count, "td", fill_regions) for row in logical_rows)
    return (
        '<div class="pdf-table-wrap"><table class="pdf-table">'
        f"<thead>{head_html}</thead><tbody>{body_html}</tbody>"
        "</table></div>"
    )


def heading_match(block: Block, entries: list[OutlineEntry]) -> tuple[OutlineEntry | None, int]:
    if block.kind not in {"paragraph", "caption"}:
        return None, 0
    lines = [flat_text(line) for line in block.lines if flat_text(line)]
    for entry in entries:
        target = outline_title_without_number(entry.title)
        accumulated: list[str] = []
        for index, line in enumerate(lines[:4]):
            accumulated.append(line)
            candidate = flat_text(" ".join(accumulated))
            candidate = re.sub(r"^\d+(?:\.\d+)*\s+", "", candidate)
            if candidate == target or candidate.startswith(target + " "):
                return entry, index + 1
            if target.startswith(candidate + " "):
                continue
            if len(candidate) > len(target) + 16:
                break
    if block.style.get("font_size_max", 0) >= 20:
        value = flat_text(block.text)
        for entry in entries:
            target = outline_title_without_number(entry.title)
            if target.startswith(value) or value.startswith(target) or target in value:
                return entry, min(2, len(lines))
    return None, 0


def markdown_escape(value: str) -> str:
    value = html.escape(value, quote=False)
    value = value.replace("\\", "\\\\").replace("`", "\\`")
    value = value.replace("|", "\\|")
    return value


def joined_lines(lines: list[str]) -> str:
    cleaned = [clean_text(line).strip() for line in lines if clean_text(line).strip()]
    if not cleaned:
        return ""
    result = cleaned[0]
    for line in cleaned[1:]:
        if result.endswith("-") and not result.endswith("--"):
            result = result[:-1] + line
        else:
            result += " " + line
    return re.sub(r"\s+", " ", result).strip()


def style_markdown_text(block: Block) -> str:
    """Render inline style intent with conservative Markdown markers."""
    if not block.line_spans:
        return markdown_escape(joined_lines(block.lines))
    rendered_lines: list[str] = []
    for spans in block.line_spans:
        pieces: list[str] = []
        for span in spans:
            text = clean_text(str(span.get("text", "")))
            if not text:
                continue
            safe = markdown_escape(text)
            font = str(span.get("font", "")).lower()
            flags = int(span.get("flags", 0))
            is_mono = "courier" in font or "mono" in font or bool(flags & 8)
            is_bold = "bold" in font or bool(flags & 16)
            is_italic = "italic" in font or bool(flags & 2)
            color = pdf_color_css(span.get("color"))
            if color and not is_mono and should_preserve_pdf_color(color):
                safe = f'<span style="color: {color}">{safe}</span>'
            if is_mono and text.strip():
                leading = text[: len(text) - len(text.lstrip())]
                trailing = text[len(text.rstrip()) :]
                content = safe.strip()
                if not leading and pieces and re.search(r"\w$", pieces[-1]) and content and content[0].isalnum():
                    leading = " "
                safe = f"{leading}`{content}`{trailing}" if "`" not in text else safe
            elif is_bold and text.strip():
                safe = f"**{safe}**"
            elif is_italic and text.strip():
                safe = f"*{safe}*"
            pieces.append(safe)
        rendered_lines.append("".join(pieces))
    result = " ".join(line.strip() for line in rendered_lines if line.strip())
    result = re.sub(r"(?<=\w)-\s+(?=\w)", "", result)
    return result


def source_ref(page: int, block_id: str, root: str = "../") -> str:
    return (
        f'<span class="source-ref">'
        f'<a href="{root}_static/source.pdf#page={page}" '
        f'title="source_page={page}, block_id={block_id}">原页 p. {page}</a>'
        f"</span>"
    )


def fence_for(text: str) -> str:
    longest_run = max((len(run) for run in re.findall(r"`+", text)), default=0)
    return "`" * max(3, longest_run + 1)


def render_block(block: Block) -> str:
    ref = source_ref(block.source_page, block.block_id)
    if block.kind == "image" and block.asset:
        return f"![PDF image on page {block.source_page}](../_static/{block.asset})\n\n{ref}"
    if block.kind == "code":
        code = "\n".join(clean_text(line).rstrip() for line in block.lines).strip("\n")
        if block.style.get("console"):
            return f'<pre class="pdf-console"><code>{html.escape(code)}</code></pre>\n\n{ref}'
        fence = fence_for(code)
        return f"{fence}\n{code}\n{fence}\n\n{ref}"
    if block.kind == "table":
        code = html.escape("\n".join(clean_text(line).rstrip() for line in block.lines).strip("\n"))
        rendered = (
            render_visual_table(block)
            if block.style.get("visual_table")
            else f'<pre class="pdf-console"><code>{code}</code></pre>'
        )
        return f"{rendered}\n\n{ref}"
    text = style_markdown_text(block)
    if not text:
        return ""
    if block.kind == "list-item":
        text = re.sub(r"^[•·*]\s*", "", text)
        return f"- {text} {ref}"
    if block.kind == "caption":
        return f"*{text}* {ref}"
    if block.kind == "callout":
        lines = [flat_text(line) for line in block.lines if flat_text(line)]
        label = lines[0].rstrip(":") if lines else "Note"
        body_block = Block(
            **{**asdict(block), "lines": lines[1:], "line_spans": block.line_spans[1:]}
        )
        body = style_markdown_text(body_block) if len(lines) > 1 else ""
        return f"> **{markdown_escape(label)}**  \n> {body} {ref}"
    return f"{text}\n\n{ref}"


def render_page_preview(page: fitz.Page, target: Path, dpi: float = 120) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    scale = dpi / 72.0
    pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
    pixmap.save(str(target))


def assign_image_assets(doc: fitz.Document, pages: list[list[Block]], assets_dir: Path) -> int:
    image_count = 0
    image_dir = assets_dir / "images"
    image_dir.mkdir(parents=True, exist_ok=True)
    for page_index, page in enumerate(doc, start=1):
        raw_blocks = page.get_text("dict", sort=True).get("blocks", [])
        image_blocks = [raw for raw in raw_blocks if int(raw.get("type", 0)) == 1]
        image_index = 0
        for block in pages[page_index - 1]:
            if block.kind != "image":
                continue
            raw = image_blocks[image_index] if image_index < len(image_blocks) else None
            image_index += 1
            if not raw or not raw.get("image"):
                block.flags.append("image_asset_unavailable")
                continue
            ext = str(raw.get("ext", "png"))
            name = f"page-{page_index:04d}-{block.block_id}.{ext}"
            (image_dir / name).write_bytes(raw["image"])
            block.asset = f"images/{name}"
            image_count += 1
    return image_count


def render_myst_document(
    pages: list[list[Block]],
    outline: list[OutlineEntry],
    title: str,
    page_count: int,
    include_page_images: bool,
) -> str:
    safe_title = markdown_escape(flat_text(title))
    lines = [
        f"# {safe_title}",
        "",
        "> 这是从 PDF 生成的可重排阅读层。复杂表格、公式和版式请通过每个 block 的原页链接核对。",
        "",
    ]
    placed: set[str] = set()
    for page_number, raw_blocks in enumerate(pages, start=1):
        blocks = raw_blocks
        page_entries = [entry for entry in outline if entry.page == page_number]
        page_image_link = (
            f' <a href="../_static/pages/page-{page_number:04d}.png">打开页图</a>'
            if include_page_images
            else ""
        )
        lines.extend(
            [
                f'<div class="source-page-marker" id="source-page-{page_number}">'
                f'<strong>原页 p. {page_number}</strong> '
                f'<a href="../_static/source.pdf#page={page_number}">打开 PDF 本页</a>'
                f'{page_image_link}</div>',
                "",
            ]
        )
        for block in blocks:
            heading, consumed = heading_match(block, [entry for entry in page_entries if entry.anchor not in placed])
            if heading:
                placed.add(heading.anchor)
                if heading.level == 1 and page_number == 1:
                    # The document H1 already represents the chapter root.
                    # Keep its bookmark anchor without rendering a duplicate H2.
                    lines.extend([f'<span id="{heading.anchor}"></span>', ""])
                else:
                    # The generated chapter title is the document H1, so PDF
                    # outline level 2 maps directly to an H2 subsection.
                    # Keeping the source level avoids MyST's non-consecutive
                    # heading warnings for PDFs whose first child is level 2.
                    heading_level = max(2, min(4, heading.level))
                    lines.extend([f"{'#' * heading_level} {markdown_escape(heading.title)}", ""])
                remaining = block.lines[consumed:]
                if remaining and joined_lines(remaining):
                    remaining_block = Block(
                        **{
                            **asdict(block),
                            "lines": remaining,
                            "line_spans": block.line_spans[consumed:],
                            "text": "\n".join(remaining),
                        }
                    )
                    lines.extend([render_block(remaining_block), ""])
                continue
            if block.style.get("font_size_max", 0) >= 20 and placed:
                value = flat_text(block.text)
                if len(value) < 100 and any(entry.level == 1 and entry.anchor in placed for entry in page_entries):
                    continue
            rendered = render_block(block)
            if rendered:
                comment = (
                    f"<!-- block_id={block.block_id} source_page={block.source_page} "
                    f"bbox={block.bbox} -->"
                )
                lines.extend([comment, rendered, ""])
        # Do not wrap a complete page in a raw <div>. MyST turns headings into
        # nested sections, and a wrapper crossing section boundaries produces
        # invalid HTML that PyData's flex layout renders as horizontal columns.
        if page_number < page_count:
            lines.extend(["---", ""])

    missing = [entry for entry in outline if entry.anchor not in placed and entry.page <= page_count]
    if missing:
        lines.extend(["## 未定位的书签", ""])
        for entry in missing:
            lines.append(
                f"- [{markdown_escape(entry.title)}](#source-page-{entry.page}) "
                f"- 原页 p. {entry.page}"
            )
    return "\n".join(lines).rstrip() + "\n"


def build_document_json(
    input_pdf: Path,
    doc: fitz.Document,
    pages: list[list[Block]],
    intake: dict[str, Any],
    outline: list[OutlineEntry],
    generated_at: str,
) -> dict[str, Any]:
    total_blocks = sum(len(page) for page in pages)
    text_chars = sum(len(flat_text(block.text)) for page in pages for block in page if block.kind != "image")
    warnings: list[str] = []
    if not outline:
        warnings.append("PDF has no built-in bookmarks; heading anchors rely on page-level fallback only.")
    return {
        "schema_version": "1.0",
        "tool": {"name": "pdf-to-sphinx", "version": TOOL_VERSION},
        "generated_at": generated_at,
        "source": {
            "path": str(input_pdf.resolve()),
            "sha256": sha256_file(input_pdf),
            "pages": len(doc),
            "metadata": doc.metadata,
            "encrypted": bool(doc.is_encrypted),
        },
        "intake": {**intake, "outline_entries": len(outline)},
        "warnings": warnings,
        "outline": [entry.to_json() for entry in outline],
        "qa": {
            "block_count": total_blocks,
            "published_source_text_chars": text_chars,
            "pages_emitted": len(pages),
            "filtering_is_reversible_via_source_pdf": True,
        },
        "pages": [
            {
                "page": page_number,
                "blocks": [block.to_json() for block in blocks],
            }
            for page_number, blocks in enumerate(pages, start=1)
        ],
    }


def write_sphinx_project(
    input_pdf: Path,
    output_dir: Path,
    doc_json: dict[str, Any],
    pages: list[list[Block]],
    outline: list[OutlineEntry],
    title: str,
    include_page_images: bool,
) -> None:
    docs_dir = output_dir / "docs"
    static_dir = docs_dir / "_static"
    page_images_dir = static_dir / "pages"
    docs_dir.mkdir(parents=True, exist_ok=True)
    static_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(input_pdf, static_dir / "source.pdf")

    document_path = output_dir / "document.json"
    document_path.write_text(json.dumps(doc_json, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "intake.json").write_text(
        json.dumps(doc_json["intake"], ensure_ascii=False, indent=2), encoding="utf-8"
    )

    display_title = flat_text(title) or "PDF document"
    safe_title = markdown_escape(display_title)
    index = f"""# {safe_title}

这是由 `pdf-to-sphinx.py` 生成的 Sphinx/MyST 项目。

- 正文阅读层：[`chapter.md`](chapter.md)
- 原始证据层：[`source.pdf`](_static/source.pdf)
- 结构中间真相源：[`document.json`](../document.json)
- 处理摘要：[`intake.json`](../intake.json)

```{{toctree}}
:maxdepth: 3

chapter
```
"""
    (docs_dir / "index.md").write_text(index, encoding="utf-8")
    (docs_dir / "chapter.md").write_text(
        render_myst_document(pages, outline, display_title, len(pages), include_page_images), encoding="utf-8"
    )

    conf = f"""project = {display_title!r}
author = "pdf-to-sphinx"
copyright = ""
extensions = ["myst_parser"]
myst_heading_anchors = 3
myst_enable_extensions = ["colon_fence", "deflist", "fieldlist", "tasklist"]
html_sidebars = {{
    "**": ["sidebar-collapse.html", "page-toc.html"],
}}
html_theme = "pydata_sphinx_theme"
html_title = {display_title!r}
html_static_path = ["_static"]
html_css_files = ["custom.css"]
html_theme_options = {{
    "navbar_end": ["theme-switcher", "navbar-icon-links"],
    "show_nav_level": 3,
    "secondary_sidebar_items": [],
}}
exclude_patterns = []
"""
    (docs_dir / "conf.py").write_text(conf, encoding="utf-8")
    (output_dir / "requirements.txt").write_text(
        "sphinx>=9.1.0\nmyst-parser>=5.1.0\npydata-sphinx-theme>=0.20.0\n", encoding="utf-8"
    )
    (output_dir / "Makefile").write_text(
        "html:\n\tpython -m sphinx -b html -W --keep-going docs _build/html\n", encoding="utf-8"
    )
    (output_dir / "README.md").write_text(
        (
            f"# {safe_title}\n\n"
            "## Build\n\n"
            "```bash\n"
            "python -m pip install -r requirements.txt\n"
            "python -m sphinx -b html -W --keep-going docs _build/html\n"
            "open _build/html/index.html\n"
            "```\n\n"
            "正文是可重排阅读层；复杂表格、公式和版式请点击页面中的“原页 p. N”回看原 PDF。\n"
            "原 PDF、document.json 与 intake.json 都应和 MyST 一起版本化或归档。\n"
        ),
        encoding="utf-8",
    )
    custom_css = """:root { --pst-color-primary: #58a6ff; }
html[data-theme="light"], html:not([data-theme]) { --pst-color-target: #f28c28; }
html[data-theme="dark"] { --pst-color-target: #d97706; }
.source-page-marker { display: flex; gap: 1rem; flex-wrap: wrap; align-items: baseline; margin: 2.25rem 0 .9rem; padding: .55rem .75rem; border-left: 3px solid var(--pst-color-primary); background: color-mix(in srgb, var(--pst-color-primary) 8%, transparent); font-size: .88rem; }
.source-page-marker strong { color: var(--pst-color-text-base); }
.source-ref { float: right; font-size: .78rem; opacity: .75; }
.source-ref a { text-decoration: none; }
.bd-content { min-width: 0; }
.bd-article-container, .bd-article { min-width: 0; width: 100%; max-width: none; }
.bd-article { max-width: 96ch; margin-inline: auto; }
.bd-article pre { max-width: 100%; overflow-x: auto; }
.bd-article pre.pdf-console { white-space: pre; overflow-x: auto; padding: 1rem; font-size: .86rem; line-height: 1.55; }
.pdf-table-wrap { overflow-x: auto; margin: 1.25rem 0; }
table.pdf-table { width: 100%; min-width: 620px; border-collapse: collapse; table-layout: fixed; font-size: .88rem; }
table.pdf-table th { background: #456f89; color: #fff; font-weight: 700; text-align: left; }
table.pdf-table th, table.pdf-table td { padding: .6rem .75rem; border: 1px solid color-mix(in srgb, var(--pst-color-primary) 35%, var(--pst-color-border)); vertical-align: top; }
table.pdf-table tbody tr:nth-child(even) { background: color-mix(in srgb, var(--pst-color-primary) 6%, transparent); }
table.pdf-table code { white-space: pre-wrap; overflow-wrap: anywhere; }
.bd-article p, .bd-article li { line-height: 1.78; }
.bd-article h1, .bd-article h2, .bd-article h3 { letter-spacing: -.015em; }
.bd-article h2 { margin-top: 2.8rem; padding-top: 1rem; border-top: 1px solid color-mix(in srgb, var(--pst-color-primary) 24%, transparent); }
.bd-article > hr { margin: 2.5rem auto; max-width: 96ch; opacity: .35; }
.bd-sidebar-primary .page-toc { margin-top: 1.5rem; }
.bd-sidebar-primary .page-toc nav ul { padding-left: 0; }
@media (max-width: 900px) { .bd-article { max-width: none; margin-inline: 1rem; } }
img { max-width: 100%; height: auto; }
"""
    (static_dir / "custom.css").write_text(custom_css, encoding="utf-8")

    if include_page_images:
        source_doc = fitz.open(str(input_pdf))
        try:
            for page_number, page in enumerate(source_doc, start=1):
                render_page_preview(page, page_images_dir / f"page-{page_number:04d}.png")
        finally:
            source_doc.close()


def maybe_build(output_dir: Path) -> int:
    build_dir = output_dir / "_build" / "html"
    command = [
        sys.executable,
        "-m",
        "sphinx",
        "-b",
        "html",
        "-W",
        "--keep-going",
        str(output_dir / "docs"),
        str(build_dir),
    ]
    try:
        completed = subprocess.run(command, check=False)
    except OSError as error:
        print(f"未执行 Sphinx 构建: 无法启动当前 Python 环境 ({error}).", file=sys.stderr)
        print(f"请先运行: python -m pip install -r {output_dir / 'requirements.txt'}", file=sys.stderr)
        return 2
    return completed.returncode


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="把 PDF 生成为可追溯的 Sphinx/MyST 工程。")
    parser.add_argument("input_pdf", type=Path, help="输入 PDF")
    parser.add_argument("-o", "--output", type=Path, required=True, help="输出 Sphinx 工程目录")
    parser.add_argument("--title", help="覆盖文档标题")
    parser.add_argument("--no-page-images", action="store_true", help="不生成每页 PNG 预览图")
    parser.add_argument("--build", action="store_true", help="生成后运行 sphinx-build -W")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_pdf = args.input_pdf.expanduser().resolve()
    output_dir = args.output.expanduser().resolve()
    if not input_pdf.is_file():
        print(f"找不到输入 PDF: {input_pdf}", file=sys.stderr)
        return 2
    if input_pdf == output_dir or input_pdf in output_dir.parents:
        print("输出目录不能是输入 PDF 所在文件或其父级覆盖范围。", file=sys.stderr)
        return 2

    generated_at = datetime.now(timezone.utc).isoformat()
    doc = fitz.open(str(input_pdf))
    try:
        outline = read_outline(doc)
        pages, intake, page_drawings = raw_pages(doc)
        image_assets = assign_image_assets(doc, pages, output_dir / "docs" / "_static")
        outline_title = outline[0].title if outline else ""
        title = flat_text(
            str(
                args.title
                or doc.metadata.get("title")
                or outline_title
                or input_pdf.stem.replace("_", " ").replace("-", " ")
            )
        )
        pages = normalize_pages(pages, page_drawings)
        document = build_document_json(input_pdf, doc, pages, intake, outline, generated_at)
        document["assets"] = {"embedded_images": image_assets, "page_images": not args.no_page_images}
        write_sphinx_project(input_pdf, output_dir, document, pages, outline, title, not args.no_page_images)
    finally:
        doc.close()

    print(f"已生成 Sphinx 工程: {output_dir}")
    print(f"  pages: {document['source']['pages']}")
    print(f"  blocks: {document['qa']['block_count']}")
    print(f"  outline entries: {len(outline)}")
    print(f"  document.json: {output_dir / 'document.json'}")
    if args.build:
        return maybe_build(output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
