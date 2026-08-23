#!/usr/bin/env bash

# Build and serve BookWeave locally with Caddy.
# Stop the foreground server with Ctrl+C.
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
project_dir="$(cd -- "$script_dir/.." && pwd)"
site_root="${BOOKWEAVE_SITE_ROOT:-$project_dir/book-web}"
host="${BOOKWEAVE_HOST:-127.0.0.1}"
port="${BOOKWEAVE_PORT:-8765}"
caddy_bin="${CADDY_BIN:-caddy}"

if ! command -v uv >/dev/null 2>&1; then
    echo "错误：未找到 uv。请先安装 uv：https://docs.astral.sh/uv/" >&2
    exit 1
fi

if ! command -v "$caddy_bin" >/dev/null 2>&1; then
    echo "错误：未找到 Caddy。macOS 可执行：brew install caddy" >&2
    exit 1
fi

if [[ ! "$port" =~ ^[0-9]+$ ]] || ((port < 1 || port > 65535)); then
    echo "错误：BOOKWEAVE_PORT 必须是 1 到 65535 的端口号。" >&2
    exit 1
fi

if [[ "${BOOKWEAVE_SKIP_BUILD:-0}" != "1" ]]; then
    echo "正在生成静态站点：$site_root"
    (
        cd "$project_dir"
        uv run python book-reader-builder.py \
            --source-dir "$project_dir" \
            --output-dir "$site_root" \
            --layout both
    )
fi

if [[ ! -f "$site_root/index.html" ]]; then
    echo "错误：找不到 $site_root/index.html。请放入书籍后重新运行，或不要设置 BOOKWEAVE_SKIP_BUILD=1。" >&2
    exit 1
fi

export BOOKWEAVE_LISTEN="$host:$port"
export BOOKWEAVE_BIND="$host"
export BOOKWEAVE_SITE_ROOT="$site_root"

echo "BookWeave 正在运行： http://$host:$port"
echo "站点目录：$site_root"
echo "按 Ctrl+C 停止服务。"

exec "$caddy_bin" run --config "$script_dir/Caddyfile" --adapter caddyfile
