"""Discover and pair EPUB/PDF source files for the book pipeline."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


def source_key(path: Path) -> str:
    """Return a forgiving key used to pair an EPUB with its PDF."""
    value = path.stem.casefold()
    value = re.sub(r"[_\W]+", " ", value, flags=re.UNICODE)
    return re.sub(r"\s+", " ", value).strip()


def book_id_for_key(key: str) -> str:
    """Return a stable, filesystem-friendly directory name for a book key."""
    value = re.sub(r"[^\w]+", "-", key.casefold(), flags=re.UNICODE).strip("-_")
    return value[:80] or "book"


@dataclass(frozen=True)
class SourceBundle:
    """The available representations of one publication."""

    key: str
    epub: Path | None = None
    pdf: Path | None = None

    @property
    def preferred(self) -> Path:
        if self.epub is not None:
            return self.epub
        if self.pdf is not None:
            return self.pdf
        raise ValueError("source bundle has no EPUB or PDF")

    @property
    def preferred_kind(self) -> str:
        return "epub" if self.epub is not None else "pdf"

    @property
    def paired(self) -> bool:
        return self.epub is not None and self.pdf is not None

    def as_json(self) -> dict[str, object]:
        return {
            "key": self.key,
            "preferred": self.preferred_kind,
            "paired": self.paired,
            "epub": str(self.epub.resolve()) if self.epub else None,
            "pdf": str(self.pdf.resolve()) if self.pdf else None,
        }

    @property
    def book_id(self) -> str:
        return book_id_for_key(self.key)


def validate_output_dir(output_dir: Path, bundle: SourceBundle) -> Path:
    """Resolve an output directory and reject source-directory collisions."""
    target = output_dir.expanduser().resolve()
    for source in (bundle.epub, bundle.pdf):
        if source is None:
            continue
        source_dir = source.parent
        if target == source or target == source_dir or source_dir in target.parents:
            raise ValueError(
                f"输出目录不能位于源文件目录中: {target}（源文件: {source}）"
            )
    return target


def _candidate_files(directory: Path, suffix: str) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(
        path for path in directory.iterdir()
        if path.is_file() and path.suffix.casefold() == suffix
    )


def discover_source_bundles(source_dir: Path) -> list[SourceBundle]:
    """Discover every book and pair EPUB/PDF files by their normalized stem."""
    root = source_dir.expanduser().resolve()
    epub_candidates = _candidate_files(root / "source-epub", ".epub")
    pdf_candidates = _candidate_files(root / "source-pdf", ".pdf")
    grouped: dict[str, dict[str, list[Path]]] = {}
    for kind, candidates in (("epub", epub_candidates), ("pdf", pdf_candidates)):
        for path in candidates:
            key = source_key(path)
            grouped.setdefault(key, {"epub": [], "pdf": []})[kind].append(path)

    if not grouped:
        raise FileNotFoundError(
            f"未找到源文件，请把 EPUB 放入 {root / 'source-epub'}，"
            f"或把 PDF 放入 {root / 'source-pdf'}"
        )

    bundles: list[SourceBundle] = []
    for key in sorted(grouped):
        files = grouped[key]
        for kind in ("epub", "pdf"):
            if len(files[kind]) > 1:
                raise RuntimeError(
                    f"同一本书存在多个 {kind.upper()} 源文件: "
                    + ", ".join(path.name for path in files[kind])
                )
        bundles.append(
            SourceBundle(
                key,
                epub=files["epub"][0] if files["epub"] else None,
                pdf=files["pdf"][0] if files["pdf"] else None,
            )
        )
    return bundles


def discover_sources(source_dir: Path, book: str | None = None) -> SourceBundle:
    """Return one discovered source bundle for compatibility commands."""
    bundles = discover_source_bundles(source_dir)
    if book:
        requested_key = source_key(Path(book))
        exact = [item for item in bundles if item.key == requested_key]
        contains = [item for item in bundles if requested_key in item.key]
        matches = exact or contains
        if not matches:
            raise FileNotFoundError(f"找不到书籍源文件: {book}")
        if len(matches) > 1:
            raise RuntimeError(
                f"找到多个匹配的书籍: " + ", ".join(item.key for item in matches)
            )
        return matches[0]
    if len(bundles) > 1:
        raise RuntimeError(
            "源目录包含多本书，请使用 --book 指定，或使用书库模式；候选: "
            + ", ".join(item.key for item in bundles)
        )
    return bundles[0]


def bundle_from_input(input_path: Path) -> SourceBundle:
    """Create a bundle for a directly supplied EPUB or PDF path."""
    path = input_path.expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"找不到输入文件: {path}")
    suffix = path.suffix.casefold()
    if suffix == ".epub":
        return SourceBundle(source_key(path), epub=path)
    if suffix == ".pdf":
        return SourceBundle(source_key(path), pdf=path)
    raise ValueError(f"只支持 EPUB 或 PDF 输入: {path}")
