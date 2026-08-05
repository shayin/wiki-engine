#!/usr/bin/env bash
# wiki-engine × quant-scanner 桥接：跑胜率统计，生成 HTML 报告
#
# 用法：
#   ./run-stats.sh NVDA AAPL TSLA                  # 默认 2y，price-only，horizon=20
#   ./run-stats.sh NVDA --period 3y --horizon 60   # 自定义参数
#
# 输出：HTML 报告路径到 stdout 最后一行（前面是日志）
# 默认输出目录：mind/tmp/（按 mind 工作区规则）
set -euo pipefail

# wrappers/ 上一级就是 quant-scanner 项目根
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
QUANT_SCANNER_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

OUTPUT_DIR="/Users/shayin/data1/htdocs/project/mind/tmp"
STAMP=$(date +%Y-%m-%d_%H%M%S)

if [[ ! -d "$QUANT_SCANNER_DIR/src/quant_scanner" ]]; then
  echo "ERROR: quant-scanner not found at $QUANT_SCANNER_DIR" >&2
  exit 1
fi

cd "$QUANT_SCANNER_DIR"

if [[ -d ".venv" ]]; then
  source .venv/bin/activate
fi

mkdir -p "$OUTPUT_DIR"
OUTPUT_FILE="$OUTPUT_DIR/stats-${STAMP}.html"

# 默认参数：price-only（避免外部 API 慢），用户传参可覆盖
# 注意：用 --price-only 默认开启，如果用户已传该 flag 会冲突，由 CLI 互斥逻辑兜底
python -m quant_scanner.stats.cli "$@" \
  --price-only \
  -o "$OUTPUT_FILE" \
  >&2

# 最后一行输出 HTML 路径，供 AI 解析
echo "$OUTPUT_FILE"
