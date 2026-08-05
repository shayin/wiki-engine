#!/usr/bin/env bash
# run-indicators.sh — 单股全套技术指标（~22 个，含成交量 OBV/MFI/VWAP/布林/KDJ/ADX）
# 用法: bash run-indicators.sh QCOM
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$SCRIPT_DIR/../.venv/bin/python3"
exec "$PY" -m quant_scanner.scanner.cli indicators "$@"
