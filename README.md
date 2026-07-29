# BookWeave

[![CI](https://github.com/snakepy512/BookWeave/actions/workflows/ci.yml/badge.svg)](https://github.com/snakepy512/BookWeave/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

[English](README.md) | [简体中文](README.zh-CN.md)

BookWeave is an EPUB-first book reader and document conversion pipeline. It turns EPUB content into browser-friendly, chapter-level HTML pages for reading and translation, while retaining PDF as a visual reference and stable page-level evidence.

PDF-only input remains supported through compatibility commands.

## Quick start

BookWeave uses Python 3.14 and [uv](https://docs.astral.sh/uv/) for dependency management:

```bash
uv sync
uv run python book-reader-builder.py --source-dir . -o ./book-web --server
```

The local server opens at `http://127.0.0.1:8080/index.html`. Without a server, open the generated `book-web/index.html` directly.

BookWeave is a script-based local tool and does not need to be installed as a Python package. Use `uv run` to execute commands with the locked dependencies.

## Input sources

The project uses an EPUB-first source layout:

```text
source-epub/   # preferred source for reflowable reading content
source-pdf/    # visual reference and stable page-level evidence
```

Actual EPUB/PDF files in these directories are ignored by Git and must not be committed. The repository only keeps empty-directory placeholders; users provide their own legally usable book files.

When matching EPUB and PDF files are present, EPUB supplies the reading layer and both original files are retained in generated output. EPUB-only and PDF-only workflows are also supported.

## Usage

### EPUB browser reader

When no input file is supplied, BookWeave scans `source-epub/` and `source-pdf/`:

```bash
uv run python book-reader-builder.py --source-dir . -o ./book-web
uv run python book-reader-builder.py --source-dir . -o ./book-web --server
uv run python book-reader-builder.py --source-dir . -o ./book-web --layout both
uv run python book-reader-builder.py --source-dir . -o ./book-web --chapter 3
```

If both EPUB and PDF are available, EPUB provides the main content. The original EPUB is unpacked into `epub-source/` for source-reference links, while the PDF is retained for visual verification.

The reader supports `--style dark|light|sepia`. By default it creates `index.html` and one file per chapter under `chapters/`, making each chapter easier to process with translation tools. Use `--layout merged` for the compatibility single-page output, or `--layout both` for both layouts.

Generated `book-web/` and `output/` directories can be recreated at any time and should not be committed.

### PDF compatibility reader

Single-page output:

```bash
uv run python pdf-reader-builder.py book.pdf
```

This creates `page_0001.html`, `page_0002.html`, and so on.

Merged output:

```bash
uv run python pdf-reader-builder.py book.pdf --merge
```

Start a local server:

```bash
uv run python pdf-reader-builder.py book.pdf --merge --server
```

Choose a theme:

```bash
uv run python pdf-reader-builder.py book.pdf -o ./reading --style light
```

Available themes are `dark`, `light`, and `sepia`.

### Extract a PDF chapter

`pdf-chapter-extractor.py` reads the PDF's built-in bookmarks and copies chapter page ranges while preserving the original layout, images, fonts, and links.

```bash
# List detected chapters and page ranges
uv run python pdf-chapter-extractor.py book.pdf --list

# Extract chapter 3
uv run python pdf-chapter-extractor.py book.pdf --chapter 3

# Choose an output path
uv run python pdf-chapter-extractor.py book.pdf --chapter 3 -o ./output/chapter-3.pdf
```

### Build a PDF Sphinx/MyST project

`pdf-to-sphinx.py` creates a reproducible Sphinx project and retains the original PDF, `document.json`, `intake.json`, and page previews.

```bash
uv run python pdf-to-sphinx.py output/chapter-3.pdf -o output/chapter-3-sphinx

# Build HTML as well
uv run python pdf-to-sphinx.py output/chapter-3.pdf \
  -o output/chapter-3-sphinx \
  --build
```

The reflowable text layer is designed for reading. Complex tables, formulas, and layout details link back to the original PDF page. The PDF pipeline currently requires a text layer.

### Build an EPUB-first Sphinx/MyST project

```bash
uv run python book-to-sphinx.py --source-dir . -o output/book-sphinx
uv run python book-to-sphinx.py --source-dir . -o output/book-sphinx --build
uv run python book-to-sphinx.py --source-dir . -o output/book-sphinx --layout both --build
```

The default layout creates one MyST page per EPUB chapter. Use `--layout merged` for the compatibility single-page document or `--layout both` for both layouts. A paired PDF is retained as visual evidence.

## Development and validation

```bash
uv run python -m unittest discover -s tests -v
uv run python -m compileall -q book_outputs.py book_sources.py epub_parser.py \
  book-reader-builder.py book-to-sphinx.py pdf-reader-builder.py \
  pdf-to-sphinx.py pdf-chapter-extractor.py tests
```

GitHub Actions runs the same checks on pushes and pull requests. Dependabot tracks GitHub Actions updates.

## Copyright and license

BookWeave's source code and user-provided book files are separate types of content. Confirm that you have the right to process any source books, and review the [MIT License](LICENSE) for the project code before redistribution.

## Acknowledgements

BookWeave was developed with assistance from OpenAI Codex.
