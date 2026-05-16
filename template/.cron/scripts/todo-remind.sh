#!/bin/bash
#
# todo-remind.sh — 待办提醒
#
# 模式:
#   无参数（默认）: 每分钟检查 remind 条目，时间到了推送明细
#   --summary:      早晚汇总模式，统计待办总数写入 pending.md
#
# remind 语法（写在待办条目末尾）:
#   `remind:HH:MM`         — 每天该时间提醒
#   `remind:DAY HH:MM`     — 每周指定日提醒（Mon/Tue/Wed/Thu/Fri/Sat/Sun）
#   `remind:M/D HH:MM`     — 指定日期提醒一次
#
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CRON_DIR="$(dirname "$SCRIPT_DIR")"
WIKI_DIR="$(dirname "$CRON_DIR")"
cd "$WIKI_DIR"

source "$SCRIPT_DIR/bark-push.sh" 2>/dev/null || true

ACTIVE="todos/active.md"
PENDING=".cron/pending.md"

if [ ! -f "$ACTIVE" ]; then
    exit 0
fi

# ================================================================
# --summary 模式：早晚汇总（保留原有逻辑）
# ================================================================
if [ "$1" = "--summary" ]; then
    TS=$(date "+%Y-%m-%d %H:%M")

    WORK_COUNT=$(awk '/^## 工作/,/^## 个人/' "$ACTIVE" | grep -c "^- \[ \]" || true)
    PERSONAL_COUNT=$(awk '/^## 个人/,/^## 跟踪/' "$ACTIVE" | grep -c "^- \[ \]" || true)
    TRACKING_COUNT=$(awk '/^## 跟踪项/,/^## 已完成/' "$ACTIVE" | grep -c "^- \[ \]" || true)

    TOTAL=$((WORK_COUNT + PERSONAL_COUNT))

    if [ "$TOTAL" -eq 0 ] && [ "$TRACKING_COUNT" -eq 0 ]; then
        exit 0
    fi

    MSG="待办提醒：工作 ${WORK_COUNT} 项，个人 ${PERSONAL_COUNT} 项"
    if [ "$TRACKING_COUNT" -gt 0 ]; then
        MSG="${MSG}，跟踪项 ${TRACKING_COUNT} 个"
    fi

    echo "- [$TS] $MSG" >> "$PENDING"
    bark_push "📚 知识库·待办" "$MSG"
    exit 0
fi

# ================================================================
# 默认模式：每分钟检查 remind 条目
# ================================================================
NOW_HHMM=$(date "+%H:%M")
NOW_DOW=$(date "+%a")   # Mon Tue Wed ...
NOW_MD=$(date "+%-m/%-d") # 5/16

# 已提醒记录（防止同一分钟重复提醒）
REMIND_LOG="$CRON_DIR/logs/reminded.txt"
TODAY=$(date "+%Y-%m-%d")
REMIND_KEY="${TODAY}-${NOW_HHMM}"

# 提取当前分区名（向前搜索最近的 ## 标题）
get_section() {
    local line_num=$1
    awk -v n="$line_num" '
        NR < n && /^## / { section = $0 }
        NR == n { print section; exit }
    ' "$ACTIVE"
}

REMIND_ITEMS=""

line_num=0
while IFS= read -r line; do
    line_num=$((line_num + 1))

    # 只处理未完成的待办
    echo "$line" | grep -q "^- \[ \]" || continue

    # 检查是否有 remind 标记
    REMIND_SPEC=$(echo "$line" | grep -oE 'remind:[^ `]+' | head -1 || true)
    [ -z "$REMIND_SPEC" ] && continue

    # 去掉 remind: 前缀
    SPEC="${REMIND_SPEC#remind:}"

    MATCH=false

    if echo "$SPEC" | grep -qE '^[A-Z][a-z]{2} [0-9]{1,2}:[0-9]{2}$'; then
        # remind:DAY HH:MM（每周）
        SPEC_DOW=$(echo "$SPEC" | awk '{print $1}')
        SPEC_HHMM=$(echo "$SPEC" | awk '{print $2}')
        [ "$SPEC_DOW" = "$NOW_DOW" ] && [ "$SPEC_HHMM" = "$NOW_HHMM" ] && MATCH=true

    elif echo "$SPEC" | grep -qE '^[0-9]{1,2}/[0-9]{1,2} [0-9]{1,2}:[0-9]{2}$'; then
        # remind:M/D HH:MM（指定日期）
        SPEC_MD=$(echo "$SPEC" | awk '{print $1}')
        SPEC_HHMM=$(echo "$SPEC" | awk '{print $2}')
        [ "$SPEC_MD" = "$NOW_MD" ] && [ "$SPEC_HHMM" = "$NOW_HHMM" ] && MATCH=true

    elif echo "$SPEC" | grep -qE '^[0-9]{1,2}:[0-9]{2}$'; then
        # remind:HH:MM（每天）
        [ "$SPEC" = "$NOW_HHMM" ] && MATCH=true
    fi

    if [ "$MATCH" = true ]; then
        # 检查是否已提醒过（防重复）
        ITEM_HASH=$(echo "$line" | md5 | cut -c1-8)
        REMIND_ID="${REMIND_KEY}|${ITEM_HASH}"
        if [ -f "$REMIND_LOG" ] && grep -qF "$REMIND_ID" "$REMIND_LOG" 2>/dev/null; then
            continue
        fi
        # 记录已提醒
        echo "$REMIND_ID" >> "$REMIND_LOG"
        ITEM_TEXT=$(echo "$line" | sed 's/^- \[ \] *//' | sed 's/`remind:[^`]*`//' | sed 's/  */ /g' | sed 's/ *$//')
        SECTION=$(get_section "$line_num")
        SECTION=$(echo "$SECTION" | sed 's/^## //')
        REMIND_ITEMS="${REMIND_ITEMS}- ${ITEM_TEXT}（${SECTION}）
"
    fi
done < "$ACTIVE"

# 输出提醒
if [ -n "$REMIND_ITEMS" ]; then
    TS=$(date "+%Y-%m-%d %H:%M")
    echo "- [$TS] ⏰ 待办提醒：${REMIND_ITEMS}" | head -5 >> "$PENDING"

    # Bark 推送（取第一条）
    FIRST_ITEM=$(echo -e "$REMIND_ITEMS" | head -1 | sed 's/^- //')
    TOTAL=$(echo -e "$REMIND_ITEMS" | grep -c "^-" || true)
    PUSH_MSG="$FIRST_ITEM"
    [ "$TOTAL" -gt 1 ] && PUSH_MSG="${PUSH_MSG} 等 ${TOTAL} 项"
    bark_push "⏰ 待办提醒" "$PUSH_MSG"
fi

# 清理过期的 remind 记录（保留最近 7 天）
if [ -f "$REMIND_LOG" ]; then
    SEVEN_DAYS_AGO=$(date -v-7d "+%Y-%m-%d" 2>/dev/null || date -d "7 days ago" "+%Y-%m-%d")
    grep "^${SEVEN_DAYS_AGO}\|^${TODAY}\|$(date -v-1d '+%Y-%m-%d' 2>/dev/null)" "$REMIND_LOG" > "$REMIND_LOG.tmp" 2>/dev/null || true
    # 更简单：只保留今天的
    grep "^${TODAY}" "$REMIND_LOG" > "$REMIND_LOG.tmp" 2>/dev/null || true
    mv "$REMIND_LOG.tmp" "$REMIND_LOG" 2>/dev/null || true
fi
