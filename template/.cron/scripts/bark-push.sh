#!/bin/bash
#
# bark-push.sh — 推送工具（Bark + 微信）
# 从 .cron/config.sh 读取配置
#
# 用法:
#   source bark-push.sh
#   bark_push "标题" "正文内容"
#

# 定位配置文件（支持被其他脚本 source 后调用）
_bark_find_config() {
    # 尝试多种路径定位 config.sh
    local dir
    for dir in "$CRON_DIR" "$WIKI_DIR" "$(dirname "${BASH_SOURCE[0]}")/.." "$(dirname "$(dirname "${BASH_SOURCE[0]}")")" "$(pwd)"; do
        if [ -f "$dir/.cron/config.sh" ]; then
            echo "$dir/.cron/config.sh"
            return
        fi
        if [ -f "$dir/config.sh" ]; then
            echo "$dir/config.sh"
            return
        fi
    done
}

BARK_CONFIG=$(_bark_find_config)
if [ -f "$BARK_CONFIG" ]; then
    source "$BARK_CONFIG"
fi

# 默认值（未配置时静默跳过）
: "${BARK_KEY:=}"
: "${BARK_GROUP:=Wiki}"
: "${BARK_SERVER:=https://api.day.app}"
: "${WECHAT_ID:=}"
: "${WECHAT_PUSH_KEY:=}"
: "${WECHAT_PUSH_SERVER:=http://43.163.223.4:6022}"

# 微信推送
_wechat_push() {
    local title="$1"
    local body="$2"

    [ -z "$WECHAT_ID" ] && return 0
    [ -z "$WECHAT_PUSH_KEY" ] && return 0
    [ -z "$title" ] && return 1
    [ -z "$body" ] && body=" "

    local text="${title}: ${body}"

    # 异步发送，不阻塞主流程
    curl -s -X POST "${WECHAT_PUSH_SERVER}/api/wechat/push" \
        -H "Authorization: Bearer ${WECHAT_PUSH_KEY}" \
        -H "Content-Type: application/json" \
        -d "{\"wechat_id\":\"${WECHAT_ID}\",\"text\":\"${text}\"}" \
        >/dev/null 2>&1 &
}

bark_push() {
    local title="$1"
    local body="$2"
    local sound="${3:-glass}"

    # Bark 推送
    if [ -n "$BARK_KEY" ]; then
        [ -z "$title" ] && return 1
        [ -z "$body" ] && body=" "

        # URL 编码（优先 python3，回退 sed）
        local encoded_title encoded_body
        encoded_title=$(python3 -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1]))" "$title" 2>/dev/null) || encoded_title=$(echo "$title" | sed 's/ /%20/g')
        encoded_body=$(python3 -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1]))" "$body" 2>/dev/null) || encoded_body=$(echo "$body" | sed 's/ /%20/g')

        # 异步发送，不阻塞主流程
        curl -s "${BARK_SERVER}/${BARK_KEY}/${encoded_title}/${encoded_body}?sound=${sound}&level=timeSensitive&group=${BARK_GROUP}" >/dev/null 2>&1 &
    fi

    # 微信推送
    _wechat_push "$title" "$body"
}
