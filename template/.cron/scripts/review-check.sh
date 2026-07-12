#!/bin/bash
#
# review-check.sh — 扫描到期决策，微信推送具体列表 + 写 pending 历史
# 纯 shell 检查到期决策，零 token
#
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CRON_DIR="$(dirname "$SCRIPT_DIR")"
WIKI_DIR="$(dirname "$CRON_DIR")"
cd "$WIKI_DIR"

source "$CRON_DIR/config.sh" 2>/dev/null || true

PENDING="$CRON_DIR/pending.md"
TODAY=$(date "+%Y-%m-%d")
TS=$(date "+%Y-%m-%d %H:%M")
ISSUES=""
COUNT=0

if [ -d "decisions" ]; then
    while IFS= read -r f; do
        [ -z "$f" ] && continue
        review_date=$(grep "^review_date:" "$f" 2>/dev/null | awk '{print $2}' | tr -d '"')
        if [ -n "$review_date" ] && [[ "$review_date" < "$TODAY" || "$review_date" == "$TODAY" ]]; then
            title=$(head -20 "$f" | grep "^title:" | sed 's/title: *//' || true)
            title=${title:-$(basename "$f" .md)}
            ISSUES="${ISSUES}• ${title}（到期 ${review_date}）
"
            COUNT=$((COUNT + 1))
        fi
    done < <(grep -rl "status: open" decisions/ 2>/dev/null || true)
fi

if [ "$COUNT" -eq 0 ]; then
    echo "ALL CLEAR"
    exit 0
fi

# 构造具体消息 + 微信推送
MSG="📋 决策复盘提醒（${TS}）：${COUNT} 个决策到期

${ISSUES}建议：和 AI 说'复盘 XX'逐条回顾实际结果。"

echo "- [$TS] 📋 决策复盘：${COUNT} 个到期" >> "$PENDING"

# 微信推送（直接 curl，不用 bark）
if [ -n "$WECHAT_ID" ] && [ -n "$WECHAT_PUSH_KEY" ]; then
    curl -s -X POST "${WECHAT_PUSH_SERVER}/api/wechat/push" \
        -H "Authorization: Bearer ${WECHAT_PUSH_KEY}" \
        -H "Content-Type: application/json" \
        -d "{\"wechat_id\":\"${WECHAT_ID}\",\"text\":$(python3 -c "import json,sys; print(json.dumps(sys.argv[1]))" "$MSG")}" >/dev/null 2>&1 &
fi

# 保留原 echo 输出（wiki-cron 可能用）
echo "$ISSUES" | sed '/^$/d'
