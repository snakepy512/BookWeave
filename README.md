# BookWeave

BookWeave 将 EPUB 转为适合浏览器阅读和逐章翻译的网页，并保留 PDF 作为视觉核对与页码证据。PDF-only 输入仍有兼容入口。

## 快速开始

项目使用 Python 3.14 和 uv 管理依赖：

```bash
uv sync
uv run python book-reader-builder.py --source-dir . -o ./book-web --server
```

默认会打开 `http://127.0.0.1:8080/index.html`。不启动服务器时，直接打开生成的 `book-web/index.html` 也可以查看。

这是一个以脚本为入口的本地工具，不需要安装成 Python package；推荐始终使用 `uv run` 执行脚本，以确保使用锁定依赖。

## 输入源目录

项目支持 EPUB-first 输入：

```text
source-epub/   # 优先作为正文阅读源
source-pdf/    # 作为视觉核对和稳定页码证据
```

这两个目录只用于放本地书籍文件，目录中的实际 EPUB/PDF 已在 `.gitignore` 中排除，不应提交到 GitHub。仓库只保留空目录占位文件；请使用者自行准备有权处理的书籍。

当两个目录中存在同名 EPUB 和 PDF 时，正文使用 EPUB，输出同时保留两个原始文件。
只有 PDF 或只有 EPUB 时，也可以独立处理。

## 使用

### PDF 兼容入口

### 单页模式

```bash
python pdf-reader-builder.py book.pdf
```

生成 page_0001.html, page_0002.html...

### 合并模式

```bash
python pdf-reader-builder.py book.pdf --merge
```

生成 merged_book.html

### 启动本地服务器

```bash
python pdf-reader-builder.py book.pdf --merge --server
```

自动在浏览器打开 http://localhost:8080

### 更多选项

```bash
python pdf-reader-builder.py book.pdf -o ./reading --style light
```

可选主题: dark(默认), light, sepia

### EPUB-first 浏览器阅读器

省略输入文件时，自动扫描当前目录的 `source-epub/` 和 `source-pdf/`：

```bash
python book-reader-builder.py -o ./book-web
python book-reader-builder.py --source-dir . -o ./book-web --server
python book-reader-builder.py --source-dir . -o ./book-web --layout both
python book-reader-builder.py --source-dir . -o ./book-web --chapter 3
```

如果 EPUB 和 PDF 同时存在，正文来自 EPUB；原始 EPUB 会解压到 `epub-source/` 供 source ref 跳转，PDF 作为原始视觉核对文件保留。

EPUB 阅读器支持 `--style dark|light|sepia`；`book-web/` 和 `output/` 都是可随时重新生成的本地输出目录，不应作为源代码提交。
默认输出 `index.html` 和 `chapters/*.html`，便于翻译插件按章节处理；`--layout merged` 只生成兼容用的 `merged_book.html`，`--layout both` 同时生成两种布局。

### 截取 PDF 的某一章

`pdf-chapter-extractor.py` 会读取 PDF 内置书签，按章节边界原样复制页面，保留原 PDF 的排版、图片、字体和链接。

```bash
# 先查看 PDF 中识别到的章节和页码
python pdf-chapter-extractor.py "book.pdf" --list

# 提取第三章，默认生成 book_chapter_03.pdf
python pdf-chapter-extractor.py "book.pdf" --chapter 3

# 指定输出路径
python pdf-chapter-extractor.py "book.pdf" --chapter 3 -o ./output/chapter-3.pdf
```

### PDF 转 Sphinx / MyST 工程

`pdf-to-sphinx.py` 会生成可重复构建的 Sphinx 工程，同时保留原 PDF、`document.json`、`intake.json` 和每页预览图。

```bash
python pdf-to-sphinx.py output/chapter-3.pdf -o output/chapter-3-sphinx

# 安装 Sphinx 依赖后，同时构建 HTML
python pdf-to-sphinx.py output/chapter-3.pdf \
  -o output/chapter-3-sphinx \
  --build
```

正文是可重排的阅读层；复杂表格、公式和版式通过页面中的“原页 p. N”链接回看原 PDF。当前核心管线要求 PDF 自带文本层。

### EPUB-first 转 Sphinx / MyST 工程

```bash
python book-to-sphinx.py --source-dir . -o output/book-sphinx
python book-to-sphinx.py --source-dir . -o output/book-sphinx --build
python book-to-sphinx.py --source-dir . -o output/book-sphinx --layout both --build
```

EPUB 正文默认按章节生成多个 MyST 页面，通过目录组织；`--layout merged` 保留单页兼容输出，`--layout both` 同时生成两种布局。配对 PDF 作为视觉证据保留。`pdf-reader-builder.py` 和 `pdf-to-sphinx.py` 仍作为 PDF-only 兼容入口保留。

## 开发与验证

```bash
uv run python -m unittest discover -s tests -v
uv run python -m compileall -q book_outputs.py book_sources.py epub_parser.py \
  book-reader-builder.py book-to-sphinx.py pdf-reader-builder.py \
  pdf-to-sphinx.py pdf-chapter-extractor.py tests
```

GitHub Actions 会在 push 和 pull request 时执行同样的检查。生成的 `book-web/`、`output/` 以及本地源书籍不会进入版本控制。

## 版权与许可

本项目代码与用户提供的书籍源文件是两类不同内容。发布到 GitHub 前，请确认源书籍拥有合法处理权限，并为项目代码选择合适的开源许可证；仓库当前没有替用户作出许可证选择。
