import importlib.util
import sys
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "pdf-to-sphinx.py"
SPEC = importlib.util.spec_from_file_location("pdf_to_sphinx", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"无法加载 {SCRIPT}")
pdf_to_sphinx = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = pdf_to_sphinx
SPEC.loader.exec_module(pdf_to_sphinx)

READER_SCRIPT = Path(__file__).parents[1] / "pdf-reader-builder.py"
READER_SPEC = importlib.util.spec_from_file_location("pdf_reader_builder_test", READER_SCRIPT)
if READER_SPEC is None or READER_SPEC.loader is None:
    raise RuntimeError(f"无法加载 {READER_SCRIPT}")
pdf_reader = importlib.util.module_from_spec(READER_SPEC)
sys.modules[READER_SPEC.name] = pdf_reader
READER_SPEC.loader.exec_module(pdf_reader)


class PdfToSphinxTests(unittest.TestCase):
    def test_fence_is_longer_than_embedded_backtick_run(self) -> None:
        self.assertEqual(pdf_to_sphinx.fence_for("select `value`"), "```")
        self.assertEqual(pdf_to_sphinx.fence_for("````"), "`````")

    def test_tableish_detection_requires_a_structured_psql_result(self) -> None:
        block = pdf_to_sphinx.Block(
            block_id="p0001-b0001",
            source_page=1,
            order=1,
            kind="code",
            lines=["name | value", "------+------", "foo  | bar"],
        )
        prose_like_code = pdf_to_sphinx.Block(
            block_id="p0001-b0002",
            source_page=1,
            order=2,
            kind="code",
            lines=["echo a | b"],
        )
        self.assertTrue(pdf_to_sphinx.tableish_block(block))
        self.assertFalse(pdf_to_sphinx.tableish_block(prose_like_code))

    def test_page_image_link_is_optional(self) -> None:
        block = pdf_to_sphinx.Block(
            block_id="p0001-b0001",
            source_page=1,
            order=1,
            kind="paragraph",
            text="A paragraph",
            lines=["A paragraph"],
        )
        document = pdf_to_sphinx.render_myst_document([[block]], [], "A title", 1, False)
        self.assertIn("打开 PDF 本页", document)
        self.assertNotIn("打开页图", document)

    def test_visual_table_supports_colspan_and_pdf_colors(self) -> None:
        span = {
            "text": "Merged header",
            "color": 16777215,
            "x0": 1.0,
            "x1": 19.0,
            "y0": 1.0,
            "y1": 9.0,
        }
        row = pdf_to_sphinx.table_row_cells([span], [0.0, 10.0, 20.0, 30.0])
        html = pdf_to_sphinx.render_table_row(row, 3, "th", [{"rect": [0, 0, 20, 10], "fill": [0.1, 0.2, 0.3]}])
        self.assertIn('colspan="2"', html)
        self.assertIn("color: #ffffff", html)
        self.assertIn("background-color: #1a334c", html)

    def test_semantic_inline_pdf_color_is_emitted(self) -> None:
        block = pdf_to_sphinx.Block(
            block_id="p0001-b0001",
            source_page=1,
            order=1,
            kind="paragraph",
            line_spans=[[{"text": "Important", "font": "FranklinGothic-Demi", "flags": 16, "color": 0xCC3300}]],
        )
        rendered = pdf_to_sphinx.style_markdown_text(block)
        self.assertIn('style="color: #cc3300"', rendered)

    def test_pdf_reader_emits_shared_document_controls(self) -> None:
        document = pdf_reader.generate_merged_html(
            "Demo",
            [(1, [])],
            [],
            pdf_reader.css_for("light"),
            style="light",
        )
        self.assertIn('data-default-theme="light"', document)
        self.assertIn('data-search-dialog', document)
        self.assertIn('data-theme-select', document)
        self.assertIn(':root[data-theme="dark"]', pdf_reader.css_for("light"))


if __name__ == "__main__":
    unittest.main()
