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

The script generates an up-to-date `book-web/`, then starts Caddy in the foreground. Open <http://127.0.0.1:8765> and press `Ctrl+C` in the terminal to stop it.

The script never commits or uploads books or generated output. It reads source files locally and serves the static site only through the loopback address.

## Common options

Use a different port:

```bash
BOOKWEAVE_PORT=9000 ./local-deployment/start.sh
```

Skip the build when `book-web/` is already current:

```bash
BOOKWEAVE_SKIP_BUILD=1 ./local-deployment/start.sh
```

Allow access from your local network (only on a trusted network):

```bash
BOOKWEAVE_HOST=0.0.0.0 ./local-deployment/start.sh
```

This makes book content available to devices on the same network that know your Mac's IP address and port. Keep the default `127.0.0.1` when LAN access is not needed.

## Files

- `start.sh`: checks dependencies, builds the static site, and starts the service.
- `Caddyfile`: static-file, compression, and listener configuration; it normally needs no changes.

To start the service automatically after macOS login or restart, wrap this script in a `launchd` user service. The included workflow intentionally stays in the foreground so you can first verify the book and site output.
