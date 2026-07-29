# Contributing to BookWeave

感谢贡献。请先确认代码和测试没有包含任何书籍源文件、生成目录或个人数据。

## 本地开发

```bash
uv sync
uv run python -m unittest discover -s tests -v
```

提交前请确认以下目录和文件没有进入版本控制：

- `source-epub/` 中的实际 EPUB
- `source-pdf/` 中的实际 PDF
- `book-web/`、`output/` 和其他生成目录
- 本地缓存、编辑器配置和日志

功能修改应同时补充或更新测试。涉及 EPUB/PDF 输出时，请至少验证一次真实输出，并说明使用的本地输入没有被提交。
