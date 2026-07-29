"""Small, dependency-light EPUB 2/3 parser for the book reading pipeline.

The parser intentionally keeps the source XHTML close to the original file.
It extracts semantic blocks for the readers and preserves source href/fragment
references for traceability. Rendering and CSS are handled by the output layer.
"""

from __future__ import annotations

import html as html_lib
import posixpath
import re
import shutil
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Iterable
from xml.etree import ElementTree as ET


EPUB_SOURCE_SCHEME = "epub-source://"
BLOCK_TAGS = {
    "blockquote",
    "dd",
    "dt",
    "figure",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "hr",
    "img",
    "li",
    "ol",
    "p",
    "pre",
    "table",
    "ul",
}
SKIP_TAGS = {"script", "style", "title", "head"}


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].casefold()


def attr(element: ET.Element, name: str, default: str = "") -> str:
    for key, value in element.attrib.items():
        if local_name(key) == name.casefold() or key.casefold() == name.casefold():
            return value
    return default


def children(element: ET.Element, name: str | None = None) -> Iterable[ET.Element]:
    for child in list(element):
        if name is None or local_name(child.tag) == name.casefold():
            yield child


def find_first(element: ET.Element, name: str) -> ET.Element | None:
    for item in element.iter():
        if local_name(item.tag) == name.casefold():
            return item
    return None


def resolve_zip_path(base: str, href: str) -> str:
    value = href.split("#", 1)[0].split("?", 1)[0]
    return posixpath.normpath(posixpath.join(posixpath.dirname(base), value)).lstrip("/")


def fragment_from_href(href: str) -> str | None:
    if "#" not in href:
        return None
    fragment = href.split("#", 1)[1]
    return fragment or None


def resolve_zip_href(base: str, href: str) -> str:
    path = resolve_zip_path(base, href)
    fragment = fragment_from_href(href)
    return path + (f"#{fragment}" if fragment else "")


def source_ref(href: str, fragment: str | None = None) -> str:
    return EPUB_SOURCE_SCHEME + href + (f"#{fragment}" if fragment else "")


def _safe_relpath(path: str) -> Path:
    pure = PurePosixPath(path)
    if pure.is_absolute() or ".." in pure.parts:
        raise ValueError(f"EPUB 包含不安全路径: {path}")
    return Path(*pure.parts)


@dataclass
class EpubBlock:
    block_id: str
    chapter_index: int
    chapter_href: str
    source_href: str
    source_fragment: str | None
    kind: str
    html: str
    text: str
    heading_level: int | None = None
    order: int = 0
    flags: list[str] = field(default_factory=list)

    @property
    def source_uri(self) -> str:
        return source_ref(self.source_href, self.source_fragment)

    def to_json(self) -> dict[str, object]:
        return {
            "block_id": self.block_id,
            "chapter_index": self.chapter_index,
            "chapter_href": self.chapter_href,
            "source_href": self.source_href,
            "source_fragment": self.source_fragment,
            "source_uri": self.source_uri,
            "kind": self.kind,
            "html": self.html,
            "text": self.text,
            "heading_level": self.heading_level,
            "order": self.order,
            "flags": self.flags,
        }


@dataclass
class EpubChapter:
    index: int
    item_id: str
    href: str
    title: str
    blocks: list[EpubBlock] = field(default_factory=list)

    def to_json(self) -> dict[str, object]:
        return {
            "index": self.index,
            "item_id": self.item_id,
            "href": self.href,
            "title": self.title,
            "blocks": [block.to_json() for block in self.blocks],
        }


@dataclass
class EpubPublication:
    path: Path
    version: str
    title: str
    language: str
    opf_path: str
    chapters: list[EpubChapter]
    manifest: dict[str, str]
    nav_titles: dict[str, str]

    @property
    def blocks(self) -> list[EpubBlock]:
        return [block for chapter in self.chapters for block in chapter.blocks]

    def to_json(self) -> dict[str, object]:
        return {
            "path": str(self.path.resolve()),
            "version": self.version,
            "title": self.title,
            "language": self.language,
            "opf_path": self.opf_path,
            "chapter_count": len(self.chapters),
            "resource_count": len(self.manifest),
            "chapters": [chapter.to_json() for chapter in self.chapters],
        }


def _find_rootfile(zf: zipfile.ZipFile) -> str:
    container = ET.fromstring(zf.read("META-INF/container.xml"))
    rootfile = find_first(container, "rootfile")
    if rootfile is None or not attr(rootfile, "full-path"):
        raise ValueError("EPUB 缺少 META-INF/container.xml rootfile")
    return attr(rootfile, "full-path")


def _parse_ncx(zf: zipfile.ZipFile, ncx_path: str) -> dict[str, str]:
    result: dict[str, str] = {}
    root = ET.fromstring(zf.read(ncx_path))
    for point in root.iter():
        if local_name(point.tag) != "navpoint":
            continue
        content = find_first(point, "content")
        label = find_first(point, "text")
        href = attr(content, "src") if content is not None else ""
        title = " ".join((label.itertext() if label is not None else []))
        if href:
            resolved = resolve_zip_href(ncx_path, href)
            value = re.sub(r"\s+", " ", title).strip()
            result[resolved] = value
            # A chapter title is normally the first nav point targeting the
            # chapter. Keep that first title even when the NCX later contains
            # section-level entries in the same XHTML resource.
            base = resolved.split("#", 1)[0]
            result.setdefault(base, value)
    return result


def _parse_nav(zf: zipfile.ZipFile, nav_path: str) -> dict[str, str]:
    result: dict[str, str] = {}
    root = ET.fromstring(zf.read(nav_path))
    for element in root.iter():
        if local_name(element.tag) != "a":
            continue
        href = attr(element, "href")
        if not href:
            continue
        title = re.sub(r"\s+", " ", " ".join(element.itertext())).strip()
        resolved = resolve_zip_href(nav_path, href)
        result[resolved] = title
        result.setdefault(resolved.split("#", 1)[0], title)
    return result


def _resource_uri(path: str, current_path: str) -> str:
    raw_path = path.split("#", 1)[0].split("?", 1)[0]
    if raw_path.startswith("/"):
        raise ValueError(f"EPUB 包含不安全资源路径: {path}")
    resolved = resolve_zip_path(current_path, path)
    safe = _safe_relpath(resolved).as_posix()
    return source_ref(safe, fragment_from_href(path))


def _serialize(element: ET.Element, current_path: str, self_closing: bool = False) -> str:
    """Serialize XHTML into safe HTML and make resource links inspectable."""
    name = local_name(element.tag)
    if name in SKIP_TAGS:
        return ""
    attrs: list[str] = []
    for key, value in element.attrib.items():
        key_name = local_name(key)
        # EPUB is an input document, not trusted application HTML. Drop event
        # handlers and executable URL schemes before embedding fragments in the
        # generated reader/Sphinx pages.
        if key_name.startswith("on"):
            continue
        if key_name in {"href", "src"} and value.lstrip().casefold().startswith(
            ("javascript:", "vbscript:")
        ):
            continue
        if key_name == "href" or key_name == "src":
            if value.startswith(("http://", "https://", "mailto:", "data:", "#")):
                rendered = value
            else:
                rendered = _resource_uri(value, current_path)
            value = rendered
        if key_name.startswith("xmlns"):
            continue
        if key_name == "type" and "}" in key:
            key_name = "epub:type"
        attrs.append(f' {key_name}="{html_lib.escape(value, quote=True)}"')
    attr_text = "".join(attrs)
    void = name in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
    if void:
        return f"<{name}{attr_text}>"
    parts = [f"<{name}{attr_text}>"]
    if element.text:
        parts.append(html_lib.escape(element.text))
    for child in list(element):
        parts.append(_serialize(child, current_path))
        if child.tail:
            parts.append(html_lib.escape(child.tail))
    parts.append(f"</{name}>")
    return "".join(parts)


def _plain_text(element: ET.Element) -> str:
    value = " ".join(part.strip() for part in element.itertext() if part.strip())
    return re.sub(r"\s+", " ", value).strip()


def _class_context(element: ET.Element) -> str:
    return f"{attr(element, 'class')} {attr(element, 'epub:type')}".casefold()


def _iter_blocks(
    element: ET.Element,
    inherited_id: str | None = None,
    inherited_class: str = "",
) -> Iterable[tuple[ET.Element, str | None, str]]:
    name = local_name(element.tag)
    if name in SKIP_TAGS:
        return
    raw_id = attr(element, "id")
    # EPUB producers often put a publication-wide container id on a wrapper
    # such as ``sbo-rt-content``. That is not a useful source anchor. Preserve
    # ids that look like content anchors, while allowing a heading/paragraph
    # inside a block wrapper such as ``div#p10`` to inherit that id.
    is_content_id = bool(raw_id and re.match(r"^(?:p\d+|section[-_]\w+|block[-_]\w+)$", raw_id, re.IGNORECASE))
    element_id = raw_id if is_content_id else inherited_id
    context = f"{inherited_class} {_class_context(element)}".strip()
    if name in BLOCK_TAGS:
        # Treat an ordered/unordered list as one block so its list semantics and
        # nested inline markup are preserved in both output formats.
        yield element, element_id, context
        return
    for child in list(element):
        yield from _iter_blocks(child, element_id if is_content_id else inherited_id, context)


def _kind(element: ET.Element, context: str) -> tuple[str, int | None]:
    name = local_name(element.tag)
    if name.startswith("h") and name[1:].isdigit():
        return "heading", int(name[1:])
    if name == "pre":
        return "code", None
    if name == "table":
        return "table", None
    if name in {"ul", "ol"}:
        return "list", None
    if name in {"blockquote", "aside"} or "callout" in context or "note" in context:
        return "callout", None
    if name in {"figure", "img"}:
        return "image", None
    return "paragraph", None


def _chapter_title(chapter_root: ET.Element, fallback: str) -> str:
    for element in chapter_root.iter():
        if local_name(element.tag) in {"h1", "h2"}:
            value = _plain_text(element)
            if value:
                return value
    title = find_first(chapter_root, "title")
    if title is not None and _plain_text(title):
        return _plain_text(title)
    return fallback


def _parse_chapter(
    zf: zipfile.ZipFile,
    chapter_index: int,
    item_id: str,
    href: str,
    nav_titles: dict[str, str],
) -> EpubChapter:
    root = ET.fromstring(zf.read(href))
    body = find_first(root, "body")
    if body is None:
        raise ValueError(f"EPUB 内容文档缺少 body: {href}")
    chapter = EpubChapter(
        index=chapter_index,
        item_id=item_id,
        href=href,
        title=nav_titles.get(href, _chapter_title(root, Path(href).stem)),
    )
    for order, (element, element_id, context) in enumerate(_iter_blocks(body), start=1):
        kind, heading_level = _kind(element, context)
        fragment = element_id or f"block-{order}"
        block_id = f"epub-c{chapter_index:04d}-b{order:04d}"
        block = EpubBlock(
            block_id=block_id,
            chapter_index=chapter_index,
            chapter_href=href,
            source_href=href,
            source_fragment=fragment,
            kind=kind,
            html=_serialize(element, href),
            text=_plain_text(element),
            heading_level=heading_level,
            order=order,
        )
        if not block.text and kind != "image":
            continue
        chapter.blocks.append(block)
    return chapter


def parse_epub(path: Path) -> EpubPublication:
    """Parse an EPUB 2/3 publication into ordered semantic blocks."""
    epub_path = path.expanduser().resolve()
    with zipfile.ZipFile(epub_path) as zf:
        opf_path = _find_rootfile(zf)
        opf = ET.fromstring(zf.read(opf_path))
        opf_dir = posixpath.dirname(opf_path)
        version = attr(opf, "version", "2.0")
        metadata = find_first(opf, "metadata")
        if metadata is None:
            metadata = opf
        title_element = find_first(metadata, "title")
        language_element = find_first(metadata, "language")
        title = _plain_text(title_element) if title_element is not None else epub_path.stem
        language = _plain_text(language_element) if language_element is not None else ""

        manifest: dict[str, str] = {}
        manifest_items: dict[str, ET.Element] = {}
        manifest_root = find_first(opf, "manifest")
        if manifest_root is not None:
            for item in children(manifest_root, "item"):
                item_id = attr(item, "id")
                href = attr(item, "href")
                if not item_id or not href:
                    continue
                full_path = posixpath.normpath(posixpath.join(opf_dir, href)).lstrip("/")
                manifest[item_id] = full_path
                manifest_items[item_id] = item

        nav_titles: dict[str, str] = {}
        spine = find_first(opf, "spine")
        if spine is None:
            raise ValueError("EPUB 缺少 spine")
        toc_id = attr(spine, "toc")
        if toc_id and toc_id in manifest:
            nav_titles.update(_parse_ncx(zf, manifest[toc_id]))
        for item_id, item in manifest_items.items():
            properties = attr(item, "properties")
            if "nav" in properties.split() and item_id in manifest:
                nav_titles.update(_parse_nav(zf, manifest[item_id]))

        chapters: list[EpubChapter] = []
        for chapter_index, itemref in enumerate(children(spine, "itemref"), start=1):
            item_id = attr(itemref, "idref")
            href = manifest.get(item_id)
            if not href:
                continue
            media_type = attr(manifest_items[item_id], "media-type")
            if media_type not in {"application/xhtml+xml", "text/html"}:
                continue
            chapters.append(_parse_chapter(zf, chapter_index, item_id, href, nav_titles))

    return EpubPublication(
        path=epub_path,
        version=version,
        title=title,
        language=language,
        opf_path=opf_path,
        chapters=chapters,
        manifest=manifest,
        nav_titles=nav_titles,
    )


def extract_epub(path: Path, target: Path) -> None:
    """Safely extract an EPUB for source inspection and asset references."""
    target = target.expanduser()
    if target.is_symlink() or (target.exists() and not target.is_dir()):
        raise ValueError(f"EPUB 解压目标不是普通目录: {target}")
    target = target.resolve()
    if target.exists() and any(target.iterdir()):
        raise ValueError(f"EPUB 解压目标必须为空: {target}")
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path) as zf:
        for member in zf.infolist():
            relative = _safe_relpath(member.filename)
            destination = (target / relative).resolve()
            if target not in destination.parents and destination != target:
                raise ValueError(f"EPUB 包含不安全路径: {member.filename}")
            if member.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member) as source, destination.open("wb") as output:
                shutil.copyfileobj(source, output)


def rewrite_asset_refs(value: str, prefix: str) -> str:
    """Replace internal EPUB URI markers with a generated-output prefix."""
    return value.replace(EPUB_SOURCE_SCHEME, prefix.rstrip("/") + "/")
