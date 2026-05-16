#!/bin/bash
#
# bark-push.sh — Bark 手机推送工具
# 供 cron 定时脚本调用，与 CC 工作推送（Coding 分组）明确区分
#
# 用法:
#   source "$(dirname "$0")/bark-push.sh"
#   bark_push "标题" "正文内容"
#   bark_push "标题" "正文内容" "音效"
#

BARK_KEY="pYFF7nTSRUCAWEfaTcCW5h"
BARK_GROUP="Wiki"
BARK_SERVER="https://api.day.app"

bark_push() {
    local title="$1"
    local body="$2"
    local sound="${3:-glass}"

    [ -z "$title" ] && return 1
    [ -z "$body" ] && body=" "

    # URL 编码（优先 python3，回退 sed）
    local encoded_title encoded_body
    encoded_title=$(python3 -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1]))" "$title" 2>/dev/null) || encoded_title=$(echo "$title" | sed 's/ /%20/g; s/ /%E2%80%A2/g')
    encoded_body=$(python3 -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1]))" "$body" 2>/dev/null) || encoded_body=$(echo "$body" | sed 's/ /%20/g')

    # 异步发送，不阻塞主流程
    curl -s "${BARK_SERVER}/${BARK_KEY}/${encoded_title}/${encoded_body}?sound=${sound}&level=timeSensitive&group=${BARK_GROUP}" >/dev/null 2>&1 &
}
