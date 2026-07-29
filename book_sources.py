"""Discover and pair EPUB/PDF source files for the book pipeline."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


def source_key(path: Path) -> str:
    """Return a forgiving key used to pair an EPUB with its PDF."""
    value = path.stem.casefold()
    value = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


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


def _select(candidates: list[Path], requested: str | None, kind: str) -> Path | None:
    if not candidates:
        return None
    if requested:
        requested_key = source_key(Path(requested))
        exact = [path for path in candidates if source_key(path) == requested_key]
        if len(exact) == 1:
            return exact[0]
        contains = [path for path in candidates if requested_key in source_key(path)]
        if len(contains) == 1:
            return contains[0]
        if not exact and not contains:
            raise FileNotFoundError(f"找不到匹配的 {kind.upper()} 源文件: {requested}")
        raise RuntimeError(f"找到多个匹配的 {kind.upper()} 源文件: {requested}")
    if len(candidates) == 1:
        return candidates[0]
    raise RuntimeError(
        f"{kind.upper()} 源目录有多个文件，请使用 --book 指定: "
        + ", ".join(path.name for path in candidates)
    )


def discover_sources(source_dir: Path, book: str | None = None) -> SourceBundle:
    """Discover sources from ``source-epub`` and ``source-pdf``.

    If both directories contain one file, they are paired when their normalized
    stems match. A requested ``book`` may be a filename or a stem.
    """
    root = source_dir.expanduser().resolve()
    epub_candidates = _candidate_files(root / "source-epub", ".epub")
    pdf_candidates = _candidate_files(root / "source-pdf", ".pdf")

    if book:
        epub = _select(epub_candidates, book, "epub")
        pdf = _select(pdf_candidates, book, "pdf")
        if epub is None and pdf is None:
            raise FileNotFoundError(f"找不到书籍源文件: {book}")
        return SourceBundle(source_key(epub or pdf), epub=epub, pdf=pdf)

    if len(epub_candidates) > 1 or len(pdf_candidates) > 1:
        all_keys = {source_key(path) for path in epub_candidates + pdf_candidates}
        if len(all_keys) != 1:
            raise RuntimeError(
                "源目录包含多本书，请使用 --book 指定；候选: "
                + ", ".join(path.name for path in epub_candidates + pdf_candidates)
            )

    epub = epub_candidates[0] if epub_candidates else None
    pdf = pdf_candidates[0] if pdf_candidates else None
    if epub is None and pdf is None:
        raise FileNotFoundError(
            f"未找到源文件，请把 EPUB 放入 {root / 'source-epub'}，"
            f"或把 PDF 放入 {root / 'source-pdf'}"
        )
    if epub and pdf and source_key(epub) != source_key(pdf):
        raise RuntimeError(
            "EPUB 和 PDF 文件名无法配对: "
            f"{epub.name} / {pdf.name}。请统一文件名或使用 --book。"
        )
    return SourceBundle(source_key(epub or pdf), epub=epub, pdf=pdf)


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
