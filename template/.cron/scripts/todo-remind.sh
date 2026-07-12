#!/bin/bash
#
# todo-remind.sh — 待办提醒 + 闹钟
#
# 模式:
#   无参数（默认）: 每分钟检查 remind 条目，时间到了推送明细
#   --summary:      早晚汇总模式，统计待办总数写入 pending.md
#
# remind 语法（待办和闹钟通用）:
#   `remind:HH:MM`         — 每天该时间提醒
#   `remind:DAY HH:MM`     — 每周指定日提醒（Mon/Tue/Wed/Thu/Fri/Sat/Sun）
#   `remind:M/D HH:MM`     — 指定日期提醒一次
#
# 闹钟区（## 闹钟）的条目以 ⏰ 开头，使用相同的 remind 标记。
# 一次性闹钟（M/D 格式）触发后自动从文件删除。
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

    # 提取具体待办（前 3 条标题，去掉 cadence 等反引号标记，截断 40 字）
    WORK_TOP3=$(awk '/^## 工作/,/^## 个人/' "$ACTIVE" | grep "^- \[ \]" | head -3 | sed 's/^- \[ \] //' | sed 's/`[^`]*`//g' | cut -c1-40)
    PERSONAL_TOP3=$(awk '/^## 个人/,/^## 跟踪/' "$ACTIVE" | grep "^- \[ \]" | head -3 | sed 's/^- \[ \] //' | sed 's/`[^`]*`//g' | cut -c1-40)
    # 跟踪项需关注（带 cadence 的，前 3 条）
    TRACKING_DUE=$(awk '/^## 跟踪项/,/^## 已完成/' "$ACTIVE" | grep "cadence:" | head -3 | sed 's/`[^`]*`//g' | cut -c1-50)

    MSG="📚 待办汇总（${TS}）
工作 ${WORK_COUNT} 项 | 个人 ${PERSONAL_COUNT} 项 | 跟踪 ${TRACKING_COUNT} 个"
    [ -n "$WORK_TOP3" ] && MSG="${MSG}

【工作 Top3】
${WORK_TOP3}"
    [ -n "$PERSONAL_TOP3" ] && MSG="${MSG}

【个人 Top3】
${PERSONAL_TOP3}"
    [ -n "$TRACKING_DUE" ] && MSG="${MSG}

【跟踪项】
${TRACKING_DUE}"

    echo "- [$TS] 待办汇总已推送（工作${WORK_COUNT}/个人${PERSONAL_COUNT}/跟踪${TRACKING_COUNT}）" >> "$PENDING"

    # 微信推送（直接 curl，不用 bark）
    if [ -n "$WECHAT_ID" ] && [ -n "$WECHAT_PUSH_KEY" ]; then
        curl -s -X POST "${WECHAT_PUSH_SERVER}/api/wechat/push" \
            -H "Authorization: Bearer ${WECHAT_PUSH_KEY}" \
            -H "Content-Type: application/json" \
            -d "{\"wechat_id\":\"${WECHAT_ID}\",\"text\":$(python3 -c "import json,sys; print(json.dumps(sys.argv[1]))" "$MSG")}" >/dev/null 2>&1 &
    fi
    exit 0
fi

# ================================================================
# 默认模式：每分钟检查 remind 条目（待办 + 闹钟统一处理）
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

# 匹配 remind 时间（通用函数，返回 true/false）
match_remind_spec() {
    local spec="$1"
    if echo "$spec" | grep -qE '^[A-Z][a-z]{2} [0-9]{1,2}:[0-9]{2}$'; then
        # DAY HH:MM（每周）
        local spec_dow spec_hhmm
        spec_dow=$(echo "$spec" | awk '{print $1}')
        spec_hhmm=$(echo "$spec" | awk '{print $2}')
        [ "$spec_dow" = "$NOW_DOW" ] && [ "$spec_hhmm" = "$NOW_HHMM" ] && return 0

    elif echo "$spec" | grep -qE '^[0-9]{1,2}/[0-9]{1,2} [0-9]{1,2}:[0-9]{2}$'; then
        # M/D HH:MM（指定日期）
        local spec_md spec_hhmm
        spec_md=$(echo "$spec" | awk '{print $1}')
        spec_hhmm=$(echo "$spec" | awk '{print $2}')
        [ "$spec_md" = "$NOW_MD" ] && [ "$spec_hhmm" = "$NOW_HHMM" ] && return 0

    elif echo "$spec" | grep -qE '^[0-9]{1,2}:[0-9]{2}$'; then
        # HH:MM（每天）
        [ "$spec" = "$NOW_HHMM" ] && return 0
    fi
    return 1
}

# 预计算闹钟区范围
ALARM_SECTION_START=$(grep -n "^## 闹钟" "$ACTIVE" | head -1 | cut -d: -f1 || true)

REMIND_ITEMS=""
ALARM_ITEMS=""
DELETE_LINES=""

line_num=0
while IFS= read -r line; do
    line_num=$((line_num + 1))

    # 匹配待办（- [ ]）或闹钟（- ⏰）
    IS_ALARM=false
    if echo "$line" | grep -q "^- \[ \]"; then
        IS_ALARM=false
    elif echo "$line" | grep -q "^- ⏰"; then
        IS_ALARM=true
    else
        continue
    fi

    # 提取 remind 标记
    REMIND_SPECS=$(echo "$line" | grep -oE 'remind:[^`]+' | sed 's/^remind://g' || true)
    [ -z "$REMIND_SPECS" ] && continue

    # 逐个匹配 remind 时间
    MATCH=false
    while IFS= read -r SPEC; do
        [ -z "$SPEC" ] && continue
        if match_remind_spec "$SPEC"; then
            MATCH=true
            # 闹钟区的 M/D 格式 = 一次性，触发后删除
            if [ "$IS_ALARM" = true ] && echo "$SPEC" | grep -qE '^[0-9]{1,2}/[0-9]{1,2}'; then
                DELETE_LINES="${DELETE_LINES}${line_num}
"
            fi
            break
        fi
    done <<< "$REMIND_SPECS"

    if [ "$MATCH" = true ]; then
        # 防重复
        ITEM_HASH=$(echo "$line" | /sbin/md5 | cut -c1-8)
        ITEM_TYPE=$([ "$IS_ALARM" = true ] && echo "alarm" || echo "remind")
        REMIND_ID="${REMIND_KEY}|${ITEM_TYPE}|${ITEM_HASH}"
        if [ -f "$REMIND_LOG" ] && grep -qF "$REMIND_ID" "$REMIND_LOG" 2>/dev/null; then
            continue
        fi
        echo "$REMIND_ID" >> "$REMIND_LOG"

        # 提取描述文本
        if [ "$IS_ALARM" = true ]; then
            ITEM_TEXT=$(echo "$line" | sed 's/^- ⏰ *//' | sed 's/`remind:[^`]*`//g' | sed 's/  */ /g' | sed 's/ *$//')
            ALARM_ITEMS="${ALARM_ITEMS}- ${ITEM_TEXT}
"
        else
            ITEM_TEXT=$(echo "$line" | sed 's/^- \[ \] *//' | sed 's/`remind:[^`]*`//g' | sed 's/  */ /g' | sed 's/ *$//')
            SECTION=$(get_section "$line_num")
            SECTION=$(echo "$SECTION" | sed 's/^## //')
            REMIND_ITEMS="${REMIND_ITEMS}- ${ITEM_TEXT}（${SECTION}）
"
        fi
    fi
done < "$ACTIVE"

# 输出待办提醒
if [ -n "$REMIND_ITEMS" ]; then
    TS=$(date "+%Y-%m-%d %H:%M")
    echo "- [$TS] ⏰ 待办提醒：${REMIND_ITEMS}" | head -5 >> "$PENDING"

    FIRST_ITEM=$(echo -e "$REMIND_ITEMS" | head -1 | sed 's/^- //')
    TOTAL=$(echo -e "$REMIND_ITEMS" | grep -c "^-" || true)
    PUSH_MSG="$FIRST_ITEM"
    [ "$TOTAL" -gt 1 ] && PUSH_MSG="${PUSH_MSG} 等 ${TOTAL} 项"
    bark_push "⏰ 待办提醒" "$PUSH_MSG"
fi

# 输出闹钟提醒
if [ -n "$ALARM_ITEMS" ]; then
    TS=$(date "+%Y-%m-%d %H:%M")
    echo "- [$TS] 🔔 闹钟：${ALARM_ITEMS}" | head -5 >> "$PENDING"

    FIRST_ITEM=$(echo -e "$ALARM_ITEMS" | head -1 | sed 's/^- //')
    TOTAL=$(echo -e "$ALARM_ITEMS" | grep -c "^-" || true)
    PUSH_MSG="$FIRST_ITEM"
    [ "$TOTAL" -gt 1 ] && PUSH_MSG="${PUSH_MSG} 等 ${TOTAL} 项"
    bark_push "🔔 闹钟" "$PUSH_MSG"
fi

# 删除已触发的一次性闹钟
if [ -n "$DELETE_LINES" ]; then
    for ln in $(echo "$DELETE_LINES" | sort -rn); do
        sed -i.bak "${ln}d" "$ACTIVE" 2>/dev/null || true
        rm -f "$ACTIVE.bak"
    done
fi

# 清理过期的 remind 记录（只保留今天的）
if [ -f "$REMIND_LOG" ]; then
    grep "^${TODAY}" "$REMIND_LOG" > "$REMIND_LOG.tmp" 2>/dev/null || true
    mv "$REMIND_LOG.tmp" "$REMIND_LOG" 2>/dev/null || true
fi
