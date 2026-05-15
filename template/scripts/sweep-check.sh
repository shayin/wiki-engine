#!/bin/bash
#
# sweep-check.sh — wiki-sweep 粗筛脚本
# 纯 shell 实现，零 token，筛出问题项后交给 Claude 精析
#
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WIKI_DIR="$(dirname "$SCRIPT_DIR")"
cd "$WIKI_DIR"

TODAY=$(date "+%Y-%m-%d")
ISSUES=""

# --- Check 1: 到期未复盘的决策 ---
if [ -d "decisions" ]; then
    while IFS= read -r f; do
        [ -z "$f" ] && continue
        review_date=$(grep "^review_date:" "$f" 2>/dev/null | awk '{print $2}' | tr -d '"')
        if [ -n "$review_date" ]; then
            if [[ "$review_date" < "$TODAY" ]] || [[ "$review_date" == "$TODAY" ]]; then
                title=$(head -20 "$f" | grep "^title:" | sed 's/title: *//' || basename "$f" .md)
                ISSUES="${ISSUES}OVERDUE_DECISION|${f}|${title:-$(basename "$f" .md)}|${review_date}
"
            fi
        fi
    done < <(grep -rl "status: open" decisions/ 2>/dev/null || true)
fi

# --- Check 2: 研究报告缺少 follow-ups ---
if [ -d "wiki/analysis" ]; then
    while IFS= read -r f; do
        [ -z "$f" ] && continue
        topic_dir=$(dirname "$f")
        if [ ! -d "${topic_dir}/follow-ups" ]; then
            ISSUES="${ISSUES}MISSING_FOLLOWUP|${f}|$(basename "$topic_dir")
"
        fi
    done < <(find wiki/analysis -name "report.md" 2>/dev/null || true)
fi

# --- Check 3: 活跃跟进项超过 30 天未更新 ---
if [ -d "wiki/analysis" ]; then
    THIRTY_DAYS_AGO=$(date -v-30d "+%Y-%m-%d" 2>/dev/null || date -d "30 days ago" "+%Y-%m-%d")
    while IFS= read -r f; do
        [ -z "$f" ] && continue
        status=$(grep "^status:" "$f" 2>/dev/null | awk '{print $2}')
        if [ "$status" = "active" ]; then
            # 从 tracking_records 中取最后日期
            last_date=$(grep -E "^- 20[0-9]{2}-" "$f" 2>/dev/null | tail -1 | grep -oE "20[0-9]{2}-[0-9]{2}-[0-9]{2}" || true)
            if [ -n "$last_date" ] && [[ "$last_date" < "$THIRTY_DAYS_AGO" ]]; then
                ISSUES="${ISSUES}STALE_FOLLOWUP|${f}|$(basename "$f" .md)|${last_date}
"
            elif [ -z "$last_date" ]; then
                # 从未记录过跟踪
                ISSUES="${ISSUES}STALE_FOLLOWUP|${f}|$(basename "$f" .md)|从未跟踪
"
            fi
        fi
    done < <(find wiki/analysis -path "*/follow-ups/*.md" 2>/dev/null || true)
fi

# --- 输出结果 ---
if [ -z "$ISSUES" ]; then
    echo "ALL CLEAR"
else
    # 去掉末尾空行
    echo "$ISSUES" | sed '/^$/d'
fi
