import contextlib
import io
import tempfile
import unittest
import zipfile
import importlib.util
from pathlib import Path
from unittest.mock import patch

from book_outputs import reader_css, reader_script, render_reader
from book_sources import bundle_from_input, discover_source_bundles, discover_sources
from book_sources import validate_output_dir
from epub_parser import _resource_uri, extract_epub, parse_epub


def load_builder():
    script = Path(__file__).parents[1] / "book-reader-builder.py"
    spec = importlib.util.spec_from_file_location("book_reader_builder_test", script)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载 builder: {script}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_fixture(path: Path) -> None:
    files = {
        "mimetype": "application/epub+zip",
        "META-INF/container.xml": (
            '<?xml version="1.0"?>'
            '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">'
            '<rootfiles><rootfile full-path="OEBPS/content.opf" '
            'media-type="application/oebps-package+xml"/></rootfiles></container>'
        ),
        "OEBPS/content.opf": (
            '<?xml version="1.0"?>'
            '<package xmlns="http://www.idpf.org/2007/opf" version="2.0">'
            '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
            '<dc:title>Fixture Book</dc:title><dc:language>en</dc:language></metadata>'
            '<manifest><item id="chapter" href="Text/chapter.xhtml" '
            'media-type="application/xhtml+xml"/><item id="ncx" href="toc.ncx" '
            'media-type="application/x-dtbncx+xml"/><item id="img" href="Images/a.png" '
            'media-type="image/png"/></manifest>'
            '<spine toc="ncx"><itemref idref="chapter"/></spine></package>'
        ),
        "OEBPS/toc.ncx": (
            '<?xml version="1.0"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/">'
            '<navMap><navPoint><navLabel><text>Chapter One</text></navLabel>'
            '<content src="Text/chapter.xhtml#p1"/></navPoint></navMap></ncx>'
        ),
        "OEBPS/Text/chapter.xhtml": (
            '<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml">'
            '<head><title>Fixture</title></head><body><div id="p1"><h1>Chapter One</h1>'
            '<p id="p2" onclick="alert(1)">Hello <code>world</code>.</p>'
            '<h2 id="p3">Section One</h2><h2 id="p4">Figure 1.1 Caption</h2>'
            '<pre id="p5">SELECT 1;</pre>'
            '<img id="p6" src="../Images/a.png" alt="diagram"/>'
            '<h2 id="p7">This chapter covers</h2></div></body></html>'
        ),
        "OEBPS/Images/a.png": "png-bytes",
    }
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)


def write_epub3_fixture(path: Path) -> None:
    files = {
        "mimetype": "application/epub+zip",
        "META-INF/container.xml": (
            '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">'
            '<rootfiles><rootfile full-path="OEBPS/package.opf" '
            'media-type="application/oebps-package+xml"/></rootfiles></container>'
        ),
        "OEBPS/package.opf": (
            '<package xmlns="http://www.idpf.org/2007/opf" version="3.0">'
            '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
            '<dc:title>EPUB 3 Fixture</dc:title><dc:language>en</dc:language></metadata>'
            '<manifest><item id="chapter" href="Text/chapter.xhtml" '
            'media-type="application/xhtml+xml"/><item id="nav" href="nav.xhtml" '
            'media-type="application/xhtml+xml" properties="nav"/></manifest>'
            '<spine><itemref idref="chapter"/></spine></package>'
        ),
        "OEBPS/nav.xhtml": (
            '<html xmlns="http://www.w3.org/1999/xhtml" '
            'xmlns:epub="http://www.idpf.org/2007/ops"><body>'
            '<nav epub:type="toc"><ol><li><a href="Text/chapter.xhtml#p1">EPUB Three</a>'
            '</li></ol></nav></body></html>'
        ),
        "OEBPS/Text/chapter.xhtml": (
            '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
            '<h1 id="p1">EPUB Three</h1><p>Content</p></body></html>'
        ),
    }
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)


class EpubParserTests(unittest.TestCase):
    def test_discovers_multiple_books_and_pairs_each_representation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            epub_dir = root / "source-epub"
            pdf_dir = root / "source-pdf"
            epub_dir.mkdir()
            pdf_dir.mkdir()
            write_fixture(epub_dir / "alpha.epub")
            write_epub3_fixture(epub_dir / "beta.epub")
            (pdf_dir / "alpha.pdf").write_bytes(b"pdf")

            bundles = discover_source_bundles(root)
            self.assertEqual([bundle.key for bundle in bundles], ["alpha", "beta"])
            self.assertTrue(bundles[0].paired)
            self.assertIsNone(bundles[1].pdf)
            with self.assertRaises(RuntimeError):
                discover_sources(root)
            self.assertEqual(discover_sources(root, "beta").key, "beta")

    def test_library_index_and_book_pages_link_to_each_other(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            epub_dir = root / "source-epub"
            pdf_dir = root / "source-pdf"
            epub_dir.mkdir()
            pdf_dir.mkdir()
            alpha = epub_dir / "alpha.epub"
            beta = epub_dir / "beta.epub"
            write_fixture(alpha)
            write_epub3_fixture(beta)
            bundles = discover_source_bundles(root)
            target = root / "book-web"
            builder = load_builder()
            builder.build_library(bundles, target, "dark", False, "chapter")
            self.assertIn("books/alpha/index.html", (target / "index.html").read_text(encoding="utf-8"))
            self.assertIn('"book_id": "beta"', (target / "library.json").read_text(encoding="utf-8"))
            index = (target / "books/alpha/index.html").read_text(encoding="utf-8")
            chapter = (target / "books/alpha/chapters/001-chapter-one.html").read_text(encoding="utf-8")
            self.assertIn('../beta/index.html', index)
            self.assertIn('../../beta/index.html', chapter)

    def test_single_book_source_build_has_a_library_landing_page(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            epub_dir = root / "source-epub"
            epub_dir.mkdir()
            write_fixture(epub_dir / "only-book.epub")
            target = root / "book-web"
            builder = load_builder()

            with (
                patch.object(
                    builder.sys,
                    "argv",
                    [
                        "book-reader-builder.py",
                        "--source-dir",
                        str(root),
                        "--output-dir",
                        str(target),
                    ],
                ),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(builder.main(), 0)

            self.assertIn("共 1 本书", (target / "index.html").read_text(encoding="utf-8"))
            self.assertTrue((target / "books/only-book/index.html").is_file())

    def test_parses_epub2_spine_ncx_and_source_ids(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            epub = Path(directory) / "fixture.epub"
            write_fixture(epub)
            publication = parse_epub(epub)
            self.assertEqual(publication.version, "2.0")
            self.assertEqual(publication.title, "Fixture Book")
            self.assertEqual(publication.chapters[0].title, "Chapter One")
            self.assertEqual([block.kind for block in publication.blocks], ["heading", "paragraph", "heading", "heading", "code", "image", "heading"])
            self.assertEqual(publication.blocks[0].source_fragment, "p1")
            self.assertIn("epub-source://OEBPS/Text/chapter.xhtml#p2", publication.blocks[1].source_uri)
            self.assertIn("epub-source://OEBPS/Images/a.png", publication.blocks[5].html)
            self.assertNotIn("onclick", publication.blocks[1].html)

    def test_extracts_epub_safely_and_pairs_sources(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            epub_dir = root / "source-epub"
            pdf_dir = root / "source-pdf"
            epub_dir.mkdir()
            pdf_dir.mkdir()
            epub = epub_dir / "fixture.epub"
            write_fixture(epub)
            (pdf_dir / "fixture.pdf").write_bytes(b"pdf")
            target = root / "unpacked"
            extract_epub(epub, target)
            self.assertTrue((target / "OEBPS/Text/chapter.xhtml").is_file())
            with self.assertRaises(ValueError):
                extract_epub(epub, target)
            bundle = discover_sources(root)
            self.assertTrue(bundle.paired)
            self.assertEqual(bundle.preferred_kind, "epub")

    def test_rejects_unsafe_internal_resource_paths(self) -> None:
        with self.assertRaises(ValueError):
            _resource_uri("../../../outside.png", "OEBPS/Text/chapter.xhtml")
        with self.assertRaises(ValueError):
            _resource_uri("/outside.png", "OEBPS/Text/chapter.xhtml")

    def test_parses_epub3_nav_and_rejects_source_directory_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            epub = root / "fixture.epub"
            write_epub3_fixture(epub)
            publication = parse_epub(epub)
            self.assertEqual(publication.version, "3.0")
            self.assertEqual(publication.chapters[0].title, "EPUB Three")
            self.assertEqual(publication.blocks[0].source_fragment, "p1")

            bundle = bundle_from_input(epub)
            with self.assertRaises(ValueError):
                validate_output_dir(root / "source-epub" / "generated", bundle)

    def test_reader_themes_are_applied(self) -> None:
        self.assertIn("--bg:#f4f6f8", reader_css("light"))
        self.assertIn("--bg:#f4eee4", reader_css("sepia"))
        self.assertIn(':root[data-theme="dark"]', reader_css())
        self.assertIn("bookweave-theme", reader_script())
        self.assertIn("data-search-open", reader_script())
        self.assertIn("prefers-color-scheme", reader_script())

    def test_reader_defaults_to_chapter_pages_and_can_emit_merged_compatibility(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            epub_dir = root / "source-epub"
            pdf_dir = root / "source-pdf"
            epub_dir.mkdir()
            pdf_dir.mkdir()
            epub = epub_dir / "fixture.epub"
            write_fixture(epub)
            (pdf_dir / "fixture.pdf").write_bytes(b"pdf")
            bundle = discover_sources(root)
            publication = parse_epub(epub)

            target = root / "book-web"
            index = render_reader(bundle, publication, target, style="light")
            self.assertEqual(index, (target / "index.html").resolve())
            self.assertTrue((target / "chapters/001-chapter-one.html").is_file())
            self.assertTrue((target / "assets/reader.css").is_file())
            self.assertIn("chapters/001-chapter-one.html", index.read_text(encoding="utf-8"))
            chapter = (target / "chapters/001-chapter-one.html").read_text(encoding="utf-8")
            self.assertIn("../assets/reader.css", chapter)
            self.assertIn('data-default-theme="light"', chapter)
            self.assertIn('data-theme-select', chapter)
            self.assertIn('data-search-dialog', chapter)
            self.assertIn("../epub-source/OEBPS/Images/a.png", chapter)
            self.assertNotIn("原 EPUB 片段", chapter)
            self.assertNotIn("下载原始 EPUB", chapter)
            self.assertNotIn("打开原始 PDF", chapter)
            self.assertEqual(chapter.count("Chapter One</h1>"), 1)
            self.assertIn('<aside class="toc chapter-toc"', chapter)
            self.assertIn('class="toc-level-1 active"', chapter)
            self.assertIn('href="#rendered-epub-c0001-b0003"', chapter)
            self.assertIn("Section One", chapter)
            toc = chapter.split('<section class="chapter-content"', 1)[0]
            self.assertNotIn("Figure 1.1 Caption", toc)
            self.assertNotIn("This chapter covers", toc)
            self.assertIn("minmax(0,48rem)", (target / "assets/reader.css").read_text(encoding="utf-8"))

            render_reader(bundle, publication, target, layout="both")
            self.assertTrue((target / "merged_book.html").is_file())


if __name__ == "__main__":
    unittest.main()
