#!/bin/bash
#
# review-check.sh — wiki-review 粗筛脚本
# 纯 shell 检查到期决策，零 token
#
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WIKI_DIR="$(dirname "$SCRIPT_DIR")"
cd "$WIKI_DIR"

TODAY=$(date "+%Y-%m-%d")
ISSUES=""

if [ -d "decisions" ]; then
    while IFS= read -r f; do
        [ -z "$f" ] && continue
        review_date=$(grep "^review_date:" "$f" 2>/dev/null | awk '{print $2}' | tr -d '"')
        if [ -n "$review_date" ] && [[ "$review_date" < "$TODAY" || "$review_date" == "$TODAY" ]]; then
            title=$(head -20 "$f" | grep "^title:" | sed 's/title: *//' || true)
            title=${title:-$(basename "$f" .md)}
            ISSUES="${ISSUES}${f}|${title}|${review_date}
"
        fi
    done < <(grep -rl "status: open" decisions/ 2>/dev/null || true)
fi

if [ -z "$ISSUES" ]; then
    echo "ALL CLEAR"
else
    echo "$ISSUES" | sed '/^$/d'
fi
