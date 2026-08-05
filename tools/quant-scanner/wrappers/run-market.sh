#!/usr/bin/env bash
# run-market.sh — 大盘背景（趋势+VIX+风格轮动+risk-on/off 判定）
# 用法: bash run-market.sh
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$SCRIPT_DIR/../.venv/bin/python3"
exec "$PY" -m quant_scanner.scanner.cli market "$@"
