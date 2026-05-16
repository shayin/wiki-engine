#!/bin/bash
#
# wiki-cron.sh — 定时任务 wrapper
#
# 模式:
#   默认（AI 模式）: 直接调用 Claude，完整分析
#   --coarse（粗筛模式）: shell 先筛，有问题才调 Claude
#
# 用法:
#   $SCRIPT_DIR/wiki-cron.sh <skill-name> [--coarse]
#
# 示例:
#   wiki-cron.sh wiki-sweep           # AI 模式
#   wiki-cron.sh wiki-sweep --coarse  # 粗筛模式
#   wiki-cron.sh wiki-digest          # digest/review 始终走粗筛（空时零 token）
#

set -e

TASK=""
USE_COARSE=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --coarse) USE_COARSE=true; shift ;;
        wiki-*|*)  TASK="$1"; shift ;;
    esac
done

if [ -z "$TASK" ]; then
    echo "用法: $0 <skill-name> [--coarse]"
    echo "  可用: wiki-sweep, wiki-review, wiki-digest"
    echo "  --coarse: 先 shell 粗筛，有问题才调 Claude（默认直接调 Claude）"
    exit 1
fi

# digest 和 review 始终走粗筛（inbox 经常为空 / 大部分天无到期决策，省 token 有意义）
if [ "$TASK" = "wiki-digest" ] || [ "$TASK" = "wiki-review" ]; then
    USE_COARSE=true
fi

# 确定工作目录
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CRON_DIR="$(dirname "$SCRIPT_DIR")"
WIKI_DIR="$(dirname "$CRON_DIR")"
cd "$WIKI_DIR"

LOG_DIR="$CRON_DIR/logs"
mkdir -p "$LOG_DIR"

TIMESTAMP=$(date "+%Y-%m-%d %H:%M:%S")
LOG_FILE="$LOG_DIR/$(date "+%Y-%m-%d").log"

# 加载 Bark 推送工具
source "$SCRIPT_DIR/bark-push.sh" 2>/dev/null || true

# 任务中文映射
TASK_LABEL=""
case "$TASK" in
    wiki-sweep)   TASK_LABEL="扫描" ;;
    wiki-review)  TASK_LABEL="复盘" ;;
    wiki-digest)  TASK_LABEL="入库" ;;
    *)            TASK_LABEL="$TASK" ;;
esac

# 记录运行时间戳（函数，多处调用）
record_run() {
    local runs="$LOG_DIR/last-runs.txt"
    local now=$(date "+%s")
    if [ -f "$runs" ]; then
        grep -v "^${TASK}=" "$runs" > "$runs.tmp" || true
        echo "${TASK}=${now}  # $(date "+%Y-%m-%d %H:%M")" >> "$runs.tmp"
        mv "$runs.tmp" "$runs"
    else
        echo "${TASK}=${now}  # $(date "+%Y-%m-%d %H:%M")" > "$runs"
    fi
}

# ================================================================
# AI 模式：直接调 Claude
# ================================================================
if [ "$USE_COARSE" = false ]; then
    echo "[$TIMESTAMP] 开始执行 /$TASK（AI 模式）" >> "$LOG_FILE"

    PROMPT="/$TASK

由定时任务自动触发（cron 模式）。请执行完整的 skill 流程，不需要用户确认。"
    OUTPUT=$(claude -p "$PROMPT" 2>&1 | sed 's/\x1b\[[0-9;]*[a-zA-Z]//g' | LC_ALL=C sed 's/[^\x20-\x7E\xA0-\xFF]//g') || true

    echo "$OUTPUT" >> "$LOG_FILE"
    echo "" >> "$LOG_FILE"

    # 解析 NOTIFY
    NOTIFY_SUMMARY=""
    while IFS= read -r line; do
        if [[ "$line" == NOTIFY:* ]]; then
            NOTIFY_SUMMARY="${line#NOTIFY: }"
        fi
    done <<< "$OUTPUT"

    # 推送通知
    if [ -n "$NOTIFY_SUMMARY" ]; then
        bark_push "📚 知识库·${TASK_LABEL}" "$NOTIFY_SUMMARY"
        osascript -e "display notification \"${NOTIFY_SUMMARY}\" with title \"📚 知识库·${TASK_LABEL}\"" 2>/dev/null || true
        # 写入 pending
        PENDING_FILE="$CRON_DIR/pending.md"
        echo "- [$(date "+%Y-%m-%d %H:%M")] $NOTIFY_SUMMARY" >> "$PENDING_FILE"
    else
        bark_push "📚 知识库·${TASK_LABEL}" "已处理，查看日志"
        osascript -e "display notification \"已处理\" with title \"📚 知识库·${TASK_LABEL}\"" 2>/dev/null || true
    fi

    echo "[$(date "+%Y-%m-%d %H:%M:%S")] 执行完毕（AI 模式）" >> "$LOG_FILE"
    echo "---" >> "$LOG_FILE"
    record_run
    exit 0
fi

# ================================================================
# 粗筛模式：shell 先筛，有问题才调 Claude
# ================================================================
echo "[$TIMESTAMP] 开始执行 /$TASK（粗筛模式）" >> "$LOG_FILE"

CHECK_SCRIPT=""
case "$TASK" in
    wiki-sweep)   CHECK_SCRIPT="$SCRIPT_DIR/sweep-check.sh" ;;
    wiki-review)  CHECK_SCRIPT="$SCRIPT_DIR/review-check.sh" ;;
    wiki-digest)  CHECK_SCRIPT="$SCRIPT_DIR/inbox-check.sh" ;;
esac

CHECK_RESULT="ALL CLEAR"
if [ -n "$CHECK_SCRIPT" ] && [ -x "$CHECK_SCRIPT" ]; then
    CHECK_RESULT=$("$CHECK_SCRIPT" 2>&1) || true
fi

echo "$CHECK_RESULT" >> "$LOG_FILE"

if [ "$CHECK_RESULT" = "ALL CLEAR" ]; then
    echo "[$(date "+%Y-%m-%d %H:%M:%S")] 粗筛通过，跳过 Claude（零 token）" >> "$LOG_FILE"
    echo "---" >> "$LOG_FILE"
    record_run
    osascript -e "display notification \"${TASK_LABEL}: 一切正常\" with title \"AI Wiki\"" 2>/dev/null || true
    exit 0
fi

# 粗筛发现问题，调 Claude
echo "[$(date "+%Y-%m-%d %H:%M:%S")] 粗筛发现问题，启动 Claude" >> "$LOG_FILE"

if [ "$TASK" = "wiki-digest" ]; then
    INBOX_FILES=$(echo "$CHECK_RESULT" | grep "^inbox/" || true)
    PROMPT="/$TASK

inbox 中有 $(echo "$CHECK_RESULT" | grep "^PENDING_FILES" | cut -d'|' -f2) 篇待处理文章：
${INBOX_FILES}

请逐个处理这些文章，对每篇执行完整的 digest 流程。"
else
    PROMPT="/$TASK

粗筛已发现以下问题（请基于这些结果直接分析，不要再全量扫描）：
${CHECK_RESULT}"
fi

OUTPUT=$(claude -p "$PROMPT" 2>&1 | sed 's/\x1b\[[0-9;]*[a-zA-Z]//g' | LC_ALL=C sed 's/[^\x20-\x7E\xA0-\xFF]//g') || true

echo "$OUTPUT" >> "$LOG_FILE"
echo "" >> "$LOG_FILE"

NOTIFY_SUMMARY=""
while IFS= read -r line; do
    if [[ "$line" == NOTIFY:* ]]; then
        NOTIFY_SUMMARY="${line#NOTIFY: }"
    fi
done <<< "$OUTPUT"

if [ -n "$NOTIFY_SUMMARY" ]; then
    bark_push "📚 知识库·${TASK_LABEL}" "$NOTIFY_SUMMARY"
    osascript -e "display notification \"${NOTIFY_SUMMARY}\" with title \"📚 知识库·${TASK_LABEL}\"" 2>/dev/null || true
    PENDING_FILE="$CRON_DIR/pending.md"
    echo "- [$(date "+%Y-%m-%d %H:%M")] $NOTIFY_SUMMARY" >> "$PENDING_FILE"
else
    bark_push "📚 知识库·${TASK_LABEL}" "已处理，查看日志"
    osascript -e "display notification \"已处理\" with title \"📚 知识库·${TASK_LABEL}\"" 2>/dev/null || true
fi

echo "[$(date "+%Y-%m-%d %H:%M:%S")] 执行完毕（粗筛模式）" >> "$LOG_FILE"
echo "---" >> "$LOG_FILE"
record_run
