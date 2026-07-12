#!/bin/bash
#
# wiki-cron.sh — 定时任务 wrapper
#
# 模式:
#   默认（AI 模式）: 直接调用 Claude，完整分析
#   --coarse（粗筛模式）: shell 先筛，有问题才调 Claude
#
# 用法:
#   wiki-cron.sh <skill-name> [--coarse]
#
# 示例:
#   wiki-cron.sh wiki-sweep           # AI 模式
#   wiki-cron.sh wiki-sweep --coarse   # 粗筛模式
#   wiki-cron.sh wiki-digest           # digest 始终走粗筛（inbox 空时零 token）
#

set -e

# cron 环境 PATH 精简，需手动添加 claude 路径
export PATH="/Applications/cmux.app/Contents/Resources/bin:$PATH"

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
# 通知构建工具：从粗筛结果生成可读摘要
# ================================================================
build_coarse_summary() {
    local check_result="$1"
    local task="$2"
    local summary=""

    case "$task" in
        wiki-digest)
            local count=$(echo "$check_result" | grep "^PENDING_FILES" | head -1 | cut -d'|' -f2 || echo "?")
            local files=$(echo "$check_result" | grep "^inbox/" | sed 's/inbox\///;s/\.md$//' | head -5)
            if [ -n "$files" ]; then
                summary="待处理 ${count} 篇：$(echo "$files" | sed -n '1h;2,$H;${g;s/\n/、/g;p}')"
            else
                summary="待处理 ${count} 篇文章"
            fi
            ;;
        wiki-sweep)
            local total=$(echo "$check_result" | grep -cE "^[A-Z_]+\|" 2>/dev/null || true)
            local types=$(echo "$check_result" | cut -d'|' -f1 | sort -u 2>/dev/null || true)

            # 按类型统计
            local n_followup=0 n_maint=0
            echo "$types" | while read -r t; do
                case "$t" in
                    OVERDUE_DECISION|MISSING_FOLLOWUP|STALE_FOLLOWUP|TODO_MISPLACED) n_followup=$((n_followup + 1)) ;;
                    STALE_TOPIC|ORPHAN_SOURCE|BROKEN_LINK|TAG_STATS|STALE_DATA|CROSS_TOPIC) n_maint=$((n_maint + 1)) ;;
                esac
            done

            # 简单统计各类型数量
            # 注意：grep -c 无匹配时退出码非零，配合 set -e 会触发 || 分支
            # 不能用 || echo "0"（会追加第二个 0 导致 "0\n0"），
            # 用 || true 只重置退出码，grep -c 自己已经输出了 0
            local overdue=$(echo "$check_result" | grep -c "^OVERDUE_DECISION" || true)
            local missing=$(echo "$check_result" | grep -c "^MISSING_FOLLOWUP" || true)
            local stale=$(echo "$check_result" | grep -c "^STALE_FOLLOWUP" || true)
            local broken=$(echo "$check_result" | grep -c "^BROKEN_LINK" || true)
            local orphan=$(echo "$check_result" | grep -c "^ORPHAN_SOURCE" || true)

            summary="发现 ${total} 个问题"
            if [ "$overdue" -gt 0 ]; then summary="${summary}，到期决策${overdue}个"; fi
            if [ "$missing" -gt 0 ]; then summary="${summary}，缺跟进${missing}个"; fi
            if [ "$stale" -gt 0 ]; then summary="${summary}，跟进过期${stale}个"; fi
            if [ "$broken" -gt 0 ]; then summary="${summary}，断链${broken}条"; fi
            if [ "$orphan" -gt 0 ]; then summary="${summary}，孤立文章${orphan}篇"; fi
            summary="${summary}。Claude 分析中，稍后通知结果"
            ;;
        wiki-review)
            local count=$(echo "$check_result" | grep -c "^OVERDUE_DECISION" || true)
            if [ "$count" -gt 0 ]; then
                local titles=$(echo "$check_result" | grep "^OVERDUE_DECISION" | cut -d'|' -f3 | head -3 | tr '\n' '、')
                summary="${count} 个决策待复盘：${titles}"
            else
                summary="发现需要关注的问题"
            fi
            ;;
        *)
            summary="粗筛发现问题，正在处理"
            ;;
    esac
    echo "$summary"
}

# 从 Claude 输出提取通知（比纯 NOTIFY 解析更鲁棒）
extract_notify() {
    local output="$1"
    local task="$2"
    local fallback_info="$3"
    local summary=""

    # 优先提取 NOTIFY 行
    while IFS= read -r line; do
        if [[ "$line" == NOTIFY:* ]]; then
            summary="${line#NOTIFY: }"
            break
        fi
    done <<< "$output"

    # 如果提取到了 NOTIFY，直接返回
    if [ -n "$summary" ]; then
        echo "$summary"
        return
    fi

    # 没有 NOTIFY 行，尝试从输出提取关键信息
    # 查找包含"扫描完成"、"处理了"、"发现"等关键词的行
    local key_lines=$(echo "$output" | grep -E "(扫描完成|处理了|发现.*个|已处理|已创建|已修复|已更新)" | head -5 || true)
    if [ -n "$key_lines" ]; then
        # 取第一行关键信息
        summary=$(echo "$key_lines" | head -1 | sed 's/^[[:space:]]*//')
        echo "$summary"
        return
    fi

    # 最终 fallback：用粗筛信息
    if [ -n "$fallback_info" ]; then
        echo "$fallback_info"
        return
    fi

    echo "已处理"
}

# ================================================================
# AI 模式：直接调 Claude
# ================================================================
if [ "$USE_COARSE" = false ]; then
    echo "[$TIMESTAMP] 开始执行 /$TASK（AI 模式）" >> "$LOG_FILE"

    # ============================================================
    # wiki-sweep V2 特殊处理
    # ============================================================
    if [ "$TASK" = "wiki-sweep" ]; then
        # 1. 前置快照（sweep 前状态）
        if [ -x "$SCRIPT_DIR/snapshot-backup.sh" ]; then
            "$SCRIPT_DIR/snapshot-backup.sh" >> "$LOG_FILE" 2>&1 || true
        fi

        # 2. 检查今日重研究触发
        RESEARCH_TRIGGER=""
        if [ -x "$SCRIPT_DIR/research-trigger.sh" ]; then
            RESEARCH_TRIGGER=$("$SCRIPT_DIR/research-trigger.sh" 2>/dev/null || echo "NO_RESEARCH_TODAY")
            echo "[$(date "+%Y-%m-%d %H:%M:%S")] 重研究触发：$RESEARCH_TRIGGER" >> "$LOG_FILE"
        fi

        # 3. 构造 prompt（含 cron 标记 + 重研究触发）
        RESEARCH_PROMPT=""
        if [ -n "$RESEARCH_TRIGGER" ] && [ "$RESEARCH_TRIGGER" != "NO_RESEARCH_TODAY" ]; then
            RESEARCH_PROMPT="

今日有以下定期重研究触发，请在 sweep 完成后（阶段 8 之后）依次执行 wiki-research 完整流程：
$RESEARCH_TRIGGER

重研究报告输出到 analysis/{标的}/research-log/$(date '+%Y-%m').md，tracker analysis 区追加 [auto-research: $(date '+%Y-%m-%d')]。"
        fi

        PROMPT="/$TASK

由定时任务自动触发（cron 模式 · V2 全自动）。执行 sweep V2 流程，但**聚焦 P0，10 分钟内完成**：
1. **只处理 P0**：OVERDUE_DECISION（到期决策）、BROKEN_LINK（断链）、STALE_FOLLOWUP（跟踪项逾期>30天）、STALE_DATA（数据过期>6月）
2. **跳过低优先级**：ORPHAN_SOURCE（孤立文章）、TAG_STATS（标签统计）、STALE_TOPIC（topic 缺更新）、CROSS_TOPIC（跨课题关联）——不紧急，留待手动处理
3. P0 变更写入 wiki/changelog.md
4. 关键事实翻转（P0）实时推微信（wechat-push.sh urgent）
5. sweep 跑完调用 wechat-push.sh daily-report 推送日报告
6. 不需要用户确认任何变更
7. **时间预算 10 分钟**——P0 处理完就结束，不纠缠低优先级$RESEARCH_PROMPT"

        OUTPUT=$(claude -p "$PROMPT" 2>&1 | sed 's/\x1b\[[0-9;]*[a-zA-Z]//g') || true

        echo "$OUTPUT" >> "$LOG_FILE"
        echo "" >> "$LOG_FILE"

        # sweep V2 自己负责微信推送（P0 实时 + 日报告），这里不重复推送
        NOTIFY_SUMMARY=$(extract_notify "$OUTPUT" "$TASK" "")

        # 仍写入 pending 用于对话内提醒
        PENDING_FILE="$CRON_DIR/pending.md"
        echo "- [$(date "+%Y-%m-%d %H:%M")] ${TASK_LABEL}: ${NOTIFY_SUMMARY}" >> "$PENDING_FILE"

        # 系统通知（非微信）
        osascript -e "display notification \"${NOTIFY_SUMMARY}\" with title \"📚 知识库·${TASK_LABEL}\"" 2>/dev/null || true

        # 后置快照（sweep 后状态，用于明日对比）
        if [ -x "$SCRIPT_DIR/snapshot-backup.sh" ]; then
            # 同日快照已存在，会自动跳过；不做重复
            true
        fi

        echo "[$(date "+%Y-%m-%d %H:%M:%S")] sweep V2 执行完毕" >> "$LOG_FILE"
        echo "---" >> "$LOG_FILE"
        record_run
        exit 0
    fi

    # ============================================================
    # 其他任务（digest / review）走原流程
    # ============================================================
    PROMPT="/$TASK

由定时任务自动触发（cron 模式）。请执行完整的 skill 流程，不需要用户确认。"
    OUTPUT=$(claude -p "$PROMPT" 2>&1 | sed 's/\x1b\[[0-9;]*[a-zA-Z]//g') || true

    echo "$OUTPUT" >> "$LOG_FILE"
    echo "" >> "$LOG_FILE"

    # 提取通知摘要
    NOTIFY_SUMMARY=$(extract_notify "$OUTPUT" "$TASK" "")

    # 推送通知（digest/review 仍用 bark_push，sweep V2 自己推微信）
    bark_push "📚 知识库·${TASK_LABEL}" "$NOTIFY_SUMMARY"
    osascript -e "display notification \"${NOTIFY_SUMMARY}\" with title \"📚 知识库·${TASK_LABEL}\"" 2>/dev/null || true
    # 写入 pending
    PENDING_FILE="$CRON_DIR/pending.md"
    echo "- [$(date "+%Y-%m-%d %H:%M")] ${TASK_LABEL}: ${NOTIFY_SUMMARY}" >> "$PENDING_FILE"

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

# 粗筛发现问题 → 先发一条预通知（让用户知道正在处理什么）
COARSE_SUMMARY=$(build_coarse_summary "$CHECK_RESULT" "$TASK")
bark_push "📚 知识库·${TASK_LABEL}·开始" "$COARSE_SUMMARY" "minuet"
osascript -e "display notification \"${COARSE_SUMMARY}\" with title \"📚 知识库·${TASK_LABEL}\"" 2>/dev/null || true

# 调 Claude 处理
echo "[$(date "+%Y-%m-%d %H:%M:%S")] 粗筛发现问题，启动 Claude" >> "$LOG_FILE"

if [ "$TASK" = "wiki-digest" ]; then
    INBOX_FILES=$(echo "$CHECK_RESULT" | grep "^inbox/" || true)
    INBOX_COUNT=$(echo "$CHECK_RESULT" | grep "^PENDING_FILES" | head -1 | cut -d'|' -f2 || echo "?")
    PROMPT="/$TASK

inbox 中有 ${INBOX_COUNT} 篇待处理文章：
${INBOX_FILES}

请逐个处理这些文章，对每篇执行完整的 digest 流程。"
else
    PROMPT="/$TASK

粗筛已发现以下问题（请基于这些结果直接分析，不要再全量扫描）：
${CHECK_RESULT}"
fi

OUTPUT=$(claude -p "$PROMPT" 2>&1 | sed 's/\x1b\[[0-9;]*[a-zA-Z]//g') || true

echo "$OUTPUT" >> "$LOG_FILE"
echo "" >> "$LOG_FILE"

# 提取通知摘要（带粗筛 fallback）
NOTIFY_SUMMARY=$(extract_notify "$OUTPUT" "$TASK" "$COARSE_SUMMARY → 已完成")

# 发结果通知
bark_push "📚 知识库·${TASK_LABEL}·完成" "$NOTIFY_SUMMARY"
osascript -e "display notification \"${NOTIFY_SUMMARY}\" with title \"📚 知识库·${TASK_LABEL}\"" 2>/dev/null || true
PENDING_FILE="$CRON_DIR/pending.md"
echo "- [$(date "+%Y-%m-%d %H:%M")] ${TASK_LABEL}: ${NOTIFY_SUMMARY}" >> "$PENDING_FILE"

echo "[$(date "+%Y-%m-%d %H:%M:%S")] 执行完毕（粗筛模式）" >> "$LOG_FILE"
echo "---" >> "$LOG_FILE"
record_run
