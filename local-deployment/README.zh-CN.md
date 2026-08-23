# 本地部署

[English](README.md)

此目录用于把 BookWeave 生成的 `book-web/` 作为仅本机可访问的静态站点运行。默认监听 `127.0.0.1:8765`，不会暴露到局域网或互联网。

## 前置条件

- Python 3.14 与 [uv](https://docs.astral.sh/uv/)
- [Caddy](https://caddyserver.com/docs/install)，macOS 可运行 `brew install caddy`
- 在仓库根目录的 `source-epub/` 和/或 `source-pdf/` 放入有权使用的书籍文件

## 启动

在仓库根目录运行：

```bash
./local-deployment/start.sh
```

脚本会先生成最新的 `book-web/`，随后在前台启动 Caddy。浏览器访问 <http://127.0.0.1:8765>；在终端按 `Ctrl+C` 停止服务。

脚本不会提交或上传书籍与生成产物。它只在本机读取源文件，并将静态文件服务于本机回环地址。

## 常用配置

使用另一个端口：

```bash
BOOKWEAVE_PORT=9000 ./local-deployment/start.sh
```

已有最新 `book-web/` 时跳过构建：

```bash
BOOKWEAVE_SKIP_BUILD=1 ./local-deployment/start.sh
```

局域网访问（请只在受信任的网络中使用）：

```bash
BOOKWEAVE_HOST=0.0.0.0 ./local-deployment/start.sh
```

这会使书籍内容可被同一局域网中知道你的 Mac IP 和端口的设备读取。若不需要局域网访问，请保持默认的 `127.0.0.1`。

## 文件说明

- `start.sh`：检查依赖、生成静态站点并启动服务。
- `Caddyfile`：Caddy 的静态文件、压缩与监听配置；通常不需要直接修改。

如需让服务在 macOS 登录或重启后自动启动，可将此脚本封装为 `launchd` 用户服务；当前版本保持前台运行，以便先确认书籍和站点显示正常。
