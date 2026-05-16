#!/bin/bash
#
# todo-remind.sh — 待办提醒，读取 active.md 统计待办数写入 pending.md
# 纯 shell，零 token
#
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WIKI_DIR="$(dirname "$SCRIPT_DIR")"
cd "$WIKI_DIR"

# 加载 Bark 推送工具
source "$SCRIPT_DIR/bark-push.sh" 2>/dev/null || true

ACTIVE="todos/active.md"
PENDING="wiki/.cron-logs/pending.md"
TS=$(date "+%Y-%m-%d %H:%M")

if [ ! -f "$ACTIVE" ]; then
    exit 0
fi

# 统计未完成待办（排除长期和已完成）
WORK_COUNT=$(awk '/^## 工作/,/^## 个人/' "$ACTIVE" | grep -c "^- \[ \]" || true)
PERSONAL_COUNT=$(awk '/^## 个人/,/^## 跟踪/' "$ACTIVE" | grep -c "^- \[ \]" || true)

# 统计跟踪项（## 跟踪项 下的 - [ ] 项）
TRACKING_COUNT=$(awk '/^## 跟踪项/,/^## 已完成/' "$ACTIVE" | grep -c "^- \[ \]" || true)

TOTAL=$((WORK_COUNT + PERSONAL_COUNT))

if [ "$TOTAL" -eq 0 ] && [ "$TRACKING_COUNT" -eq 0 ]; then
    exit 0
fi

# 构造提醒消息
MSG="待办提醒：工作 ${WORK_COUNT} 项，个人 ${PERSONAL_COUNT} 项"
if [ "$TRACKING_COUNT" -gt 0 ]; then
    MSG="${MSG}，跟踪项 ${TRACKING_COUNT} 个"
fi

echo "- [$TS] $MSG" >> "$PENDING"

# Bark 手机推送
bark_push "📚 知识库·待办" "$MSG"
