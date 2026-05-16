#!/bin/bash
#
# inbox-check.sh — 检查 inbox/ 是否有待处理文章
# 输出：文件数量和列表，或 ALL CLEAR
#
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CRON_DIR="$(dirname "$SCRIPT_DIR")"
WIKI_DIR="$(dirname "$CRON_DIR")"
cd "$WIKI_DIR"

if [ ! -d "inbox" ]; then
    echo "ALL CLEAR"
    exit 0
fi

# 统计 inbox 下的 .md 文件
PENDING=""
COUNT=0

for f in inbox/*.md; do
    [ -f "$f" ] || continue
    COUNT=$((COUNT + 1))
    PENDING="${PENDING}${f}
"
done

if [ "$COUNT" -eq 0 ]; then
    echo "ALL CLEAR"
else
    echo "PENDING_FILES|${COUNT}"
    echo "$PENDING" | sed '/^$/d'
fi
