#!/usr/bin/env bash
# wiki-engine × quant-scanner 桥接：快速扫描当前触发的形态
#
# 用法：
#   ./run-scan.sh NVDA
#   ./run-scan.sh NVDA AAPL TSLA --workers 4
#
# 输出：JSON 扫描结果到 stdout（直接给 AI 解析）
set -euo pipefail

# wrappers/ 上一级就是 quant-scanner 项目根
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
QUANT_SCANNER_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

if [[ ! -d "$QUANT_SCANNER_DIR/src/quant_scanner" ]]; then
  echo "ERROR: quant-scanner not found at $QUANT_SCANNER_DIR" >&2
  exit 1
fi

cd "$QUANT_SCANNER_DIR"

# 激活 venv（如果存在）
if [[ -d ".venv" ]]; then
  source .venv/bin/activate
fi

# 把所有参数传给 quant-scanner scan
exec python -m quant_scanner.scanner.cli "$@"
