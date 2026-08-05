#!/usr/bin/env bash
# run-screen.sh — 选股扫描（breakout突破+放量 / trend趋势第二阶段 / momentum短线动量）
# 用法: bash run-screen.sh breakout [--watchlist path]
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$SCRIPT_DIR/../.venv/bin/python3"
exec "$PY" -m quant_scanner.scanner.cli screen "$@"
