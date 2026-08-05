#!/usr/bin/env bash
# run-sector.sh — 板块相对强度排名 + 轮动 + 启动信号（找板块机会）
# 用法: bash run-sector.sh
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$SCRIPT_DIR/../.venv/bin/python3"
exec "$PY" -m quant_scanner.scanner.cli sector "$@"
