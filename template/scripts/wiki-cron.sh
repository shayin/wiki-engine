#!/bin/bash
#
# wiki-cron.sh — 定时任务 wrapper
# 包装 claude -p 命令，解析 NOTIFY: 标记行，发送 macOS 通知
#
# 用法:
#   ./scripts/wiki-cron.sh <skill-name>
#
# 示例:
#   ./scripts/wiki-cron.sh wiki-sweep
#   ./scripts/wiki-cron.sh wiki-review
#
# 在 crontab 中使用:
#   0 10 * * 0 cd /path/to/ai-wiki && ./scripts/wiki-cron.sh wiki-sweep
#

set -e

TASK="${1:-}"

if [ -z "$TASK" ]; then
    echo "用法: $0 <skill-name>"
    echo "  可用: wiki-sweep, wiki-review"
    exit 1
fi

# 确定工作目录（脚本所在目录的上一级 = wiki 根目录）
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WIKI_DIR="$(dirname "$SCRIPT_DIR")"
cd "$WIKI_DIR"

# 日志目录
LOG_DIR="$WIKI_DIR/wiki/.cron-logs"
mkdir -p "$LOG_DIR"

TIMESTAMP=$(date "+%Y-%m-%d %H:%M:%S")
LOG_FILE="$LOG_DIR/$(date "+%Y-%m-%d").log"

echo "[$TIMESTAMP] 开始执行 /$TASK" >> "$LOG_FILE"

# 执行 claude 命令，捕获输出
OUTPUT=$(claude -p "/$TASK" 2>&1) || true

# 将完整输出写入日志
echo "$OUTPUT" >> "$LOG_FILE"
echo "" >> "$LOG_FILE"

# 解析 NOTIFY: 标记行
NOTIFY_SUMMARY=""
while IFS= read -r line; do
    if [[ "$line" == NOTIFY:* ]]; then
        msg="${line#NOTIFY: }"
        NOTIFY_SUMMARY="$msg"
        # 发送 macOS 通知
        osascript -e "display notification \"$msg\" with title \"AI Wiki\" subtitle \"$TASK\"" 2>/dev/null || true
    fi
done <<< "$OUTPUT"

# 如果没有 NOTIFY: 行，发一个默认通知
if [ -z "$NOTIFY_SUMMARY" ]; then
    osascript -e "display notification \"$TASK 执行完毕\" with title \"AI Wiki\"" 2>/dev/null || true
fi

TIMESTAMP_END=$(date "+%Y-%m-%d %H:%M:%S")
echo "[$TIMESTAMP_END] 执行完毕" >> "$LOG_FILE"
echo "---" >> "$LOG_FILE"
