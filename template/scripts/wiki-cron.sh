#!/bin/bash
#
# wiki-cron.sh — 定时任务 wrapper（两阶段模式）
# 阶段1: shell 粗筛（零 token）
# 阶段2: Claude 精析（仅粗筛发现问题时触发）
#
# 用法:
#   ./scripts/wiki-cron.sh <skill-name>
#
# 示例:
#   ./scripts/wiki-cron.sh wiki-sweep
#   ./scripts/wiki-cron.sh wiki-review
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

echo "[$TIMESTAMP] 开始执行 /$TASK（阶段1: 粗筛）" >> "$LOG_FILE"

# ---- 阶段1: Shell 粗筛 ----
CHECK_SCRIPT=""
case "$TASK" in
    wiki-sweep)  CHECK_SCRIPT="$SCRIPT_DIR/sweep-check.sh" ;;
    wiki-review) CHECK_SCRIPT="$SCRIPT_DIR/review-check.sh" ;;
esac

CHECK_RESULT="ALL CLEAR"
if [ -n "$CHECK_SCRIPT" ] && [ -x "$CHECK_SCRIPT" ]; then
    CHECK_RESULT=$("$CHECK_SCRIPT" 2>&1) || true
fi

echo "$CHECK_RESULT" >> "$LOG_FILE"

# 根据粗筛结果决定是否调用 Claude
if [ "$CHECK_RESULT" = "ALL CLEAR" ]; then
    echo "[$(date "+%Y-%m-%d %H:%M:%S")] 粗筛通过，跳过 Claude（零 token 消耗）" >> "$LOG_FILE"
    echo "---" >> "$LOG_FILE"

    # 记录运行时间戳（ALL CLEAR 也要记录，否则 cron-check 会反复触发）
    LAST_RUNS="$LOG_DIR/last-runs.txt"
    NOW_EPOCH=$(date "+%s")
    if [ -f "$LAST_RUNS" ]; then
        grep -v "^${TASK}=" "$LAST_RUNS" > "$LAST_RUNS.tmp" || true
        echo "${TASK}=${NOW_EPOCH}" >> "$LAST_RUNS.tmp"
        mv "$LAST_RUNS.tmp" "$LAST_RUNS"
    else
        echo "${TASK}=${NOW_EPOCH}" > "$LAST_RUNS"
    fi

    # 直接通知
    osascript -e "display notification \"${TASK}: 一切正常\" with title \"AI Wiki\"" 2>/dev/null || true
    exit 0
fi

# ---- 阶段2: Claude 精析 ----
echo "[$(date "+%Y-%m-%d %H:%M:%S")] 粗筛发现问题，启动 Claude 精析" >> "$LOG_FILE"

# 构造 prompt，附带粗筛结果
PROMPT="/$TASK

粗筛已发现以下问题（请基于这些结果直接分析，不要再全量扫描）：
${CHECK_RESULT}"

OUTPUT=$(claude -p "$PROMPT" 2>&1) || true

# 将完整输出写入日志
echo "$OUTPUT" >> "$LOG_FILE"
echo "" >> "$LOG_FILE"

# 解析 NOTIFY: 标记行
NOTIFY_SUMMARY=""
while IFS= read -r line; do
    if [[ "$line" == NOTIFY:* ]]; then
        msg="${line#NOTIFY: }"
        NOTIFY_SUMMARY="$msg"
        osascript -e "display notification \"$msg\" with title \"AI Wiki\" subtitle \"$TASK\"" 2>/dev/null || true
    fi
done <<< "$OUTPUT"

# 如果没有 NOTIFY: 行，发一个默认通知
if [ -z "$NOTIFY_SUMMARY" ]; then
    osascript -e "display notification \"${TASK}: 发现问题，已处理\" with title \"AI Wiki\"" 2>/dev/null || true
fi

TIMESTAMP_END=$(date "+%Y-%m-%d %H:%M:%S")
echo "[$TIMESTAMP_END] 执行完毕" >> "$LOG_FILE"
echo "---" >> "$LOG_FILE"

# 记录本次运行时间戳
LAST_RUNS="$LOG_DIR/last-runs.txt"
NOW_EPOCH=$(date "+%s")
if [ -f "$LAST_RUNS" ]; then
    grep -v "^${TASK}=" "$LAST_RUNS" > "$LAST_RUNS.tmp" || true
    echo "${TASK}=${NOW_EPOCH}" >> "$LAST_RUNS.tmp"
    mv "$LAST_RUNS.tmp" "$LAST_RUNS"
else
    echo "${TASK}=${NOW_EPOCH}" > "$LAST_RUNS"
fi
