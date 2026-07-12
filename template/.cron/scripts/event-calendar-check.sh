#!/bin/bash
#
# event-calendar-check.sh — 扫描 macro-tracker 事件日历，有变化时调 AI 深度分析 + 微信推送完整内容
#
# 触发：cron-check.sh 早晚各一次（11:30 / 19:00，config 的 EVENT_CALENDAR_TIMES）
# 机制：无变化 → 微信推送摘要；有变化（今日到期/逾期未填）→ 调 AI（claude -p）深度分析 → 微信推送完整内容
# 通知：一律微信（不用 bark），推送含完整分析不只是总结
#
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CRON_DIR="$(dirname "$SCRIPT_DIR")"
WIKI_DIR="$(dirname "$CRON_DIR")"
cd "$WIKI_DIR"

source "$CRON_DIR/config.sh" 2>/dev/null || true

PENDING="$CRON_DIR/pending.md"
LOG_DIR="$CRON_DIR/logs"
TODAY=$(date "+%Y-%m-%d")
TS=$(date "+%Y-%m-%d %H:%M:%S")
LOG="$LOG_DIR/event-calendar-${TODAY}.log"
mkdir -p "$LOG_DIR"

# ============================================================
# 微信推送（直接 curl，不用 bark）
# ============================================================
wechat_push() {
    local text="$1"
    [ -z "$WECHAT_ID" ] && { echo "[$TS] WECHAT_ID 未配置，跳过推送" >> "$LOG"; return 0; }
    [ -z "$WECHAT_PUSH_KEY" ] && return 0
    [ -z "$text" ] && return 1

    local payload
    payload="{\"wechat_id\":\"${WECHAT_ID}\",\"text\":$(python3 -c "import json,sys; print(json.dumps(sys.argv[1]))" "$text" 2>/dev/null)}" || return 1

    curl -s -X POST "${WECHAT_PUSH_SERVER}/api/wechat/push" \
        -H "Authorization: Bearer ${WECHAT_PUSH_KEY}" \
        -H "Content-Type: application/json" \
        -d "$payload" >/dev/null 2>&1 &
    echo "[$TS] 微信推送已发送（${#text} 字符）" >> "$LOG"
}

# ============================================================
# 1. 扫描所有 macro-tracker 事件日历
# ============================================================
EVENTS_FILE=$(mktemp)
TRACKERS=$(find "$WIKI_DIR/wiki/analysis" -path "*/follow-ups/*macro-tracker*.md" 2>/dev/null)

for tracker in $TRACKERS; do
    topic=$(basename "$(dirname "$(dirname "$tracker")")")
    # 提取「## 事件日历」到下一个 ## 之间的表格行（| YYYY-MM-DD |...）
    awk '/^## 事件日历/{f=1;next} /^## /{f=0} f && /^[[:space:]]*\| [0-9]{4}-[0-9]{2}-[0-9]{2}/' "$tracker" 2>/dev/null | \
    while IFS='|' read _ date event expect actual impact _; do
        date=$(echo "$date" | tr -d ' ' | sed 's/[[:space:]]//g')
        [ -z "$date" ] && continue
        event=$(echo "$event" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
        expect=$(echo "$expect" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
        actual=$(echo "$actual" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
        echo "${date}|${topic}|${event}|${expect}|${actual}|${tracker}" >> "$EVENTS_FILE"
    done
done

TOTAL=$(wc -l < "$EVENTS_FILE" 2>/dev/null | tr -d ' ' || echo "0")
echo "[$TS] 扫描完成：${TOTAL} 个事件" >> "$LOG"

# ============================================================
# 2. 分类：今日到期 / 逾期未填
# ============================================================
TODAY_EVENTS=""
OVERDUE_EVENTS=""
TODAY_COUNT=0
OVERDUE_COUNT=0
CHANGED_TRACKERS=""

while IFS='|' read -r date topic event expect actual tracker; do
    [ -z "$date" ] && continue
    if [ "$date" = "$TODAY" ]; then
        TODAY_EVENTS="${TODAY_EVENTS}• ${topic}: ${event}（预期 ${expect}，实际 ${actual}）\n"
        TODAY_COUNT=$((TODAY_COUNT + 1))
        CHANGED_TRACKERS="${CHANGED_TRACKERS}${tracker}\n"
    elif [[ "$date" < "$TODAY" ]] && { [[ "$actual" == *"—"* ]] || [[ "$actual" == "-" ]] || [ -z "$actual" ]; }; then
        OVERDUE_EVENTS="${OVERDUE_EVENTS}• ${topic}: ${event}（${date}，预期 ${expect}，结果未填）\n"
        OVERDUE_COUNT=$((OVERDUE_COUNT + 1))
        CHANGED_TRACKERS="${CHANGED_TRACKERS}${tracker}\n"
    fi
done < "$EVENTS_FILE"

HAS_CHANGE=0
{ [ "$TODAY_COUNT" -gt 0 ] || [ "$OVERDUE_COUNT" -gt 0 ]; } && HAS_CHANGE=1

# ============================================================
# 3. 有变化 → 调 AI 深度分析（claude -p zhipu-glm）
# ============================================================
if [ "$HAS_CHANGE" = "1" ]; then
    CHANGES_DESC="今日到期 ${TODAY_COUNT}：\n${TODAY_EVENTS}逾期未填 ${OVERDUE_COUNT}：\n${OVERDUE_EVENTS}"

    # 去重 tracker 路径
    TRACKER_LIST=$(echo -e "$CHANGED_TRACKERS" | sort -u | grep -v '^$')

    AI_PROMPT="你是投资研究助手（wiki-engine 自动扫描触发）。扫描 macro-tracker 事件日历发现以下事件有变化（今日到期或逾期未填）：

${CHANGES_DESC}

相关 tracker 文件（请 Read）：
${TRACKER_LIST}

任务：
1. Read 这些 macro-tracker，理解事件日历 + 变量分层 + 确认规则 + 原结论
2. 对今日到期事件：用 WebSearch 搜索最新结果（如财报 EPS/margin/数据），用 Edit 填入 tracker 事件日历的「实际」列
3. 对比预期 vs 实际，逐事件判断达成/未达成
4. 判断是否改变原结论（MPV 定价矩阵定位/持仓建议）——若改变，用 Edit 更新 tracker 跟踪记录
5. 输出完整推送内容（将被微信推送给用户），结构：
   - 事件详情（日期/事件/预期/实际）
   - 达成/未达成判断
   - 深度分析（是否改变结论 + 为什么 + 操作调整建议）
   - 结论一句话
专业名词首次出现加简短解释（用户非金融专业，如 MPV/forward PE/margin 等）。直接输出推送正文，不要前言。

tracker 上下文：用户持仓见 ${WIKI_DIR}/context/finance.md（只读）。"

    echo "[$TS] 检测到变化（今日 ${TODAY_COUNT}/逾期 ${OVERDUE_COUNT}），调用 AI 分析..." >> "$LOG"

    AI_OUTPUT=$(source ~/.claude/providers/zhipu-glm.sh 2>/dev/null && \
        timeout 300 claude -p "$AI_PROMPT" \
            --allowedTools "Read,Edit,WebSearch,Grep,Glob" \
            --dangerously-skip-permissions < /dev/null 2>&1 || echo "AI 分析失败（超时或错误），请手动查看 tracker")

    PUSH_TEXT="📊 事件日历扫描（${TS}）
共 ${TOTAL} 个跟踪事件 | 今日到期 ${TODAY_COUNT} | 逾期未填 ${OVERDUE_COUNT}

${AI_OUTPUT}"

    wechat_push "$PUSH_TEXT"
    echo "- [$TS] ⚡ 事件日历变化分析（今日 ${TODAY_COUNT}/逾期 ${OVERDUE_COUNT}）：${TODAY_EVENTS}${OVERDUE_EVENTS}AI 已分析并更新 tracker，详见 ${LOG}" >> "$PENDING"
    echo "[$TS] AI 分析完成，已微信推送（${#PUSH_TEXT} 字符）" >> "$LOG"
else
    # ============================================================
    # 4. 无变化 → 微信推送摘要
    # ============================================================
    PUSH_TEXT="📊 事件日历扫描（${TS}）
共 ${TOTAL} 个跟踪事件 | 今日到期 0 | 逾期 0 | 无变化

下次扫描：晚 19:00（如有事件到期/逾期会自动 AI 分析+推送）"
    wechat_push "$PUSH_TEXT"
    echo "[$TS] 无变化，摘要已推送" >> "$LOG"
fi

rm -f "$EVENTS_FILE"
echo "[$TS] event-calendar-check 完成" >> "$LOG"
