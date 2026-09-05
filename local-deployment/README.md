# Local deployment

[简体中文](README.zh-CN.md)

This directory runs the BookWeave-generated `book-web/` as a static site that is accessible only on the local machine. By default, it listens on `127.0.0.1:8765` and is not exposed to your LAN or the internet.

## Prerequisites

- Python 3.14 and [uv](https://docs.astral.sh/uv/)
- [Caddy](https://caddyserver.com/docs/install); on macOS, run `brew install caddy`
- One or more books you are permitted to use in the repository root's `source-epub/` and/or `source-pdf/` directories

## Start

From the repository root, run:

```bash
./local-deployment/start.sh
```

The script serves the existing `book-web/` and starts Caddy in the foreground without rebuilding. On the first run or after updating books, run `./local-deployment/start.sh --rebuild` to generate pages before starting. Open <http://127.0.0.1:8765> and press `Ctrl+C` in the terminal to stop it.

The script never commits or uploads books or generated output. It reads source files locally and serves the static site only through the loopback address.

## Common options

Use a different port:

```bash
BOOKWEAVE_PORT=9000 ./local-deployment/start.sh
```

Rebuild pages and start the server (use on the first run or after updating books):

```bash
./local-deployment/start.sh --rebuild
```

Allow access from your local network (only on a trusted network):

```bash
BOOKWEAVE_HOST=0.0.0.0 ./local-deployment/start.sh
```

This makes book content available to devices on the same network that know your Mac's IP address and port. Keep the default `127.0.0.1` when LAN access is not needed.

## Files

- `start.sh`: checks dependencies and starts the service; builds the static site only with `--rebuild`.
- `Caddyfile`: static-file, compression, and listener configuration; it normally needs no changes.

To start the service automatically after macOS login or restart, wrap this script in a `launchd` user service. The included workflow intentionally stays in the foreground so you can first verify the book and site output.
