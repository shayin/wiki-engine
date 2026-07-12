#!/bin/bash
#
# wechat-push.sh — 微信推送工具（wiki-sweep V2 专用）
#
# 替代 bark-push.sh 的微信优先推送。所有变更都推微信。
#
# 用法（被其他脚本 source 后调用）：
#   source wechat-push.sh
#   wechat_push_text "标题" "正文"               # 简单推送
#   wechat_push_urgent "事件标题" "事件描述"      # P0 紧急推送（独立消息）
#   wechat_push_daily_report "$CHANGELOG_FILE"   # 从 changelog.md 生成日报告
#   wechat_push_summary "变更 X 条，全部自动处理"  # 摘要推送
#
# 直接执行：
#   wechat-push.sh text "标题" "正文"
#   wechat-push.sh daily-report [changelog.md路径]
#   wechat-push.sh urgent "事件" "描述"
#

# ============================================================
# 配置加载
# ============================================================
_wechat_find_config() {
    local dir
    for dir in "$CRON_DIR" "$(dirname "$(dirname "${BASH_SOURCE[0]}")")" "$(pwd)/.cron"; do
        if [ -f "$dir/config.sh" ]; then
            echo "$dir/config.sh"
            return
        fi
    done
}

WECHAT_CONFIG=$(_wechat_find_config)
if [ -f "$WECHAT_CONFIG" ]; then
    # shellcheck disable=SC1090
    source "$WECHAT_CONFIG"
fi

: "${WECHAT_ID:=}"
: "${WECHAT_PUSH_KEY:=}"
: "${WECHAT_PUSH_SERVER:=http://43.163.223.4:6022}"

# 微信消息长度上限（保守值，避免服务端截断）
WECHAT_MSG_MAX_LEN=1500

# ============================================================
# 核心推送函数
# ============================================================
_wechat_send() {
    local text="$1"
    [ -z "$WECHAT_ID" ] && return 0
    [ -z "$WECHAT_PUSH_KEY" ] && return 0
    [ -z "$text" ] && return 1

    # 转义 JSON 特殊字符
    local json_text
    json_text=$(printf '%s' "$text" | python3 -c "
import json, sys
print(json.dumps(sys.stdin.read())[1:-1])
" 2>/dev/null || echo "$text")

    # 异步发送，不阻塞主流程
    curl -s -X POST "${WECHAT_PUSH_SERVER}/api/wechat/push" \
        -H "Authorization: Bearer ${WECHAT_PUSH_KEY}" \
        -H "Content-Type: application/json" \
        -d "{\"wechat_id\":\"${WECHAT_ID}\",\"text\":\"${json_text}\"}" \
        >/dev/null 2>&1 &
}

# ============================================================
# 长消息分段
# ============================================================
_wechat_send_long() {
    local text="$1"
    local total=${#text}

    if [ "$total" -le "$WECHAT_MSG_MAX_LEN" ]; then
        _wechat_send "$text"
        return
    fi

    # 分段发送
    local offset=0
    local part=1
    local total_parts=$(( (total + WECHAT_MSG_MAX_LEN - 1) / WECHAT_MSG_MAX_LEN ))

    while [ "$offset" -lt "$total" ]; do
        local chunk="${text:offset:WECHAT_MSG_MAX_LEN}"
        local header="[$part/$total_parts] "
        _wechat_send "${header}${chunk}"
        offset=$((offset + WECHAT_MSG_MAX_LEN))
        part=$((part + 1))
    done
}

# ============================================================
# 公开 API（source 后调用）
# ============================================================

# 简单文本推送
# 用法：wechat_push_text "标题" "正文"
wechat_push_text() {
    local title="$1"
    local body="${2:-}"

    [ -z "$title" ] && return 1
    [ -z "$body" ] && body=" "

    local text="${title}: ${body}"
    _wechat_send_long "$text"
}

# P0 紧急推送（独立消息，独立标题）
# 用法：wechat_push_urgent "事件标题" "事件描述"
wechat_push_urgent() {
    local event="$1"
    local desc="${2:-}"

    [ -z "$event" ] && return 1

    local text="🔥 [P0 关键变更] ${event}"
    if [ -n "$desc" ]; then
        text="${text}

${desc}"
    fi
    _wechat_send_long "$text"
}

# 摘要推送（无具体变更时用）
# 用法：wechat_push_summary "今日 N 条变更，全部自动处理完毕"
wechat_push_summary() {
    local summary="${1:-今日无变更}"
    local text="📚 Wiki 自动维护 · $(date '+%Y-%m-%d')

${summary}"
    _wechat_send "$text"
}

# 从 changelog.md 生成日报告并推送
# 用法：wechat_push_daily_report [changelog.md路径]
wechat_push_daily_report() {
    local changelog="${1:-$(dirname "$(dirname "$CRON_DIR")")/wiki/changelog.md}"
    local today
    today=$(date '+%Y-%m-%d')

    [ ! -f "$changelog" ] && return 1

    # 提取今日段落（## YYYY-MM-DD ... 到下一个 ## 或文件末尾）
    local report
    report=$(python3 <<PYEOF
import re, sys
today = "$today"
changelog = "$changelog"

try:
    with open(changelog, 'r', encoding='utf-8') as f:
        content = f.read()
except Exception as e:
    print(f"读取失败：{e}", file=sys.stderr)
    sys.exit(1)

# 匹配 ## YYYY-MM-DD 段落
pattern = r'^## ' + re.escape(today) + r'[^\n]*\n(.*?)(?=^## |\Z)'
m = re.search(pattern, content, re.MULTILINE | re.DOTALL)
if not m:
    print("NO_CHANGES_TODAY")
    sys.exit(0)

section = m.group(1)

# 统计各档变更数
p0_count = len(re.findall(r'\bauto-(?:state)\b', section))
p1_count = len(re.findall(r'\bauto-(?:update|disputed|conn)\b', section))
p1_count += len(re.findall(r'\bauto-close-pending\b', section))
p1_count += len(re.findall(r'\bauto-closed\b', section))
p2_count = len(re.findall(r'\bauto-(?:fix|refresh)\b', section))
total = p0_count + p1_count + p2_count

# 输出报告
header = f"📚 Wiki 自动维护 · {today}"
summary = f"📊 共 {total} 条变更（P0 ×{p0_count} / P1 ×{p1_count} / P2 ×{p2_count}）"

print(f"{header}\n\n{summary}\n")

# 直接输出 section 内容
print(section.strip())
PYEOF
)

    if [ "$report" = "NO_CHANGES_TODAY" ]; then
        wechat_push_summary "今日无变更，知识库状态稳定"
        return 0
    fi

    _wechat_send_long "$report"
}

# ============================================================
# 直接执行模式
# ============================================================
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
    case "${1:-}" in
        text)
            shift
            wechat_push_text "$@"
            ;;
        urgent)
            shift
            wechat_push_urgent "$@"
            ;;
        summary)
            shift
            wechat_push_summary "$@"
            ;;
        daily-report)
            shift
            wechat_push_daily_report "$@"
            ;;
        *)
            cat <<EOF
用法: wechat-push.sh <命令> [参数]

命令:
  text "标题" "正文"                 简单文本推送
  urgent "事件" "描述"               P0 紧急推送
  summary "摘要文本"                 无具体变更时推送摘要
  daily-report [changelog.md路径]   从 changelog 生成日报告

source 模式可用函数:
  wechat_push_text "标题" "正文"
  wechat_push_urgent "事件" "描述"
  wechat_push_summary "摘要"
  wechat_push_daily_report [路径]
EOF
            exit 1
            ;;
    esac
fi
