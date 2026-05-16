#!/bin/bash
#
# cron-check.sh — 每分钟检查，补跑错过的定时任务
# 轻量：只读 last-runs.txt + 日期比较，零 token
#
# 逻辑：
#   计算「最近一次应该运行的时间」
#   如果 last_runs 记录早于该时间 → 补跑
#   如果 last_runs 记录晚于或等于 → 跳过
#   如果调度时间还没到 → 跳过（不抢跑）
#

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WIKI_DIR="$(dirname "$SCRIPT_DIR")"
LOG_DIR="$WIKI_DIR/wiki/.cron-logs"
LAST_RUNS="$LOG_DIR/last-runs.txt"
LOCK_FILE="$LOG_DIR/.check.lock"

mkdir -p "$LOG_DIR"

# 防并发：如果上一次 check 还在跑（<10 分钟），跳过
if [ -f "$LOCK_FILE" ]; then
    lock_age=$(( $(date "+%s") - $(stat -f "%m" "$LOCK_FILE" 2>/dev/null || echo "0") ))
    [ "$lock_age" -lt 600 ] && exit 0
fi
touch "$LOCK_FILE"
trap 'rm -f "$LOCK_FILE"' EXIT

NOW_EPOCH=$(date "+%s")
LOG_FILE="$LOG_DIR/$(date "+%Y-%m-%d").log"
TS=$(date "+%Y-%m-%d %H:%M:%S")

# --- 工具函数：读 last-run ---
get_last_run() {
    local task="$1"
    if [ -f "$LAST_RUNS" ]; then
        grep "^${task}=" "$LAST_RUNS" 2>/dev/null | tail -1 | cut -d= -f2 || echo "0"
    else
        echo "0"
    fi
}

# --- 工具函数：macOS 日期转 epoch ---
to_epoch() {
    date -j -f "%Y-%m-%d %H:%M" "$1" "+%s" 2>/dev/null || echo "0"
}

# ============================================================
# wiki-sweep: 每周六 23:15
# ============================================================
SWEEP_LAST=$(get_last_run "wiki-sweep")
[ -z "$SWEEP_LAST" ] && SWEEP_LAST=0

# 计算最近一个周六的日期（DOW: 1=Mon ... 6=Sat, 7=Sun）
DOW=$(date "+%u")
DAYS_BACK=$(( (DOW - 6 + 7) % 7 ))  # Sat=0, Sun=1, Mon=2, ..., Fri=6
LAST_SATURDAY=$(date -v-${DAYS_BACK}d "+%Y-%m-%d")
SWEEP_DUE=$(to_epoch "${LAST_SATURDAY} 23:15")

# 条件：调度时间已过 AND 上次运行早于调度时间
if [ "$SWEEP_DUE" -gt 0 ] && [ "$NOW_EPOCH" -ge "$SWEEP_DUE" ] && [ "$SWEEP_LAST" -lt "$SWEEP_DUE" ]; then
    echo "[$TS] 补跑 wiki-sweep" >> "$LOG_FILE"
    cd "$WIKI_DIR" && ./scripts/wiki-cron.sh wiki-sweep || true
else
    echo "[$TS] sweep: 跳过（下次调度：周六 23:15）" >> "$LOG_FILE"
fi

# ============================================================
# wiki-review: 每月 1 号和 15 号 23:15
# ============================================================
REVIEW_LAST=$(get_last_run "wiki-review")
[ -z "$REVIEW_LAST" ] && REVIEW_LAST=0

# 计算最近一个调度日期
DAY=$(date "+%d")
MONTH=$(date "+%m")
YEAR=$(date "+%Y")

if [ "$DAY" -ge 16 ]; then
    REVIEW_DATE="${YEAR}-${MONTH}-15"
elif [ "$DAY" -ge 2 ]; then
    REVIEW_DATE="${YEAR}-${MONTH}-01"
else
    # 今天是 1 号，上一次调度是上个月 15 号
    PREV=$(date -v-1m "+%Y-%m")
    REVIEW_DATE="${PREV}-15"
fi

REVIEW_DUE=$(to_epoch "${REVIEW_DATE} 23:15")

if [ "$REVIEW_DUE" -gt 0 ] && [ "$NOW_EPOCH" -ge "$REVIEW_DUE" ] && [ "$REVIEW_LAST" -lt "$REVIEW_DUE" ]; then
    echo "[$TS] 补跑 wiki-review" >> "$LOG_FILE"
    cd "$WIKI_DIR" && ./scripts/wiki-cron.sh wiki-review || true
else
    echo "[$TS] review: 跳过（下次调度：${REVIEW_DATE} 23:15）" >> "$LOG_FILE"
fi

# ============================================================
# wiki-digest: 每天 23:00（仅在 inbox 有文件时触发 Claude）
# ============================================================
DIGEST_LAST=$(get_last_run "wiki-digest")
[ -z "$DIGEST_LAST" ] && DIGEST_LAST=0

TODAY=$(date "+%Y-%m-%d")
DIGEST_DUE=$(to_epoch "${TODAY} 23:00")

if [ "$DIGEST_DUE" -gt 0 ] && [ "$NOW_EPOCH" -ge "$DIGEST_DUE" ] && [ "$DIGEST_LAST" -lt "$DIGEST_DUE" ]; then
    # 先检查 inbox 是否有文件
    INBOX_COUNT=0
    if [ -d "$WIKI_DIR/inbox" ]; then
        for f in "$WIKI_DIR"/inbox/*.md; do
            [ -f "$f" ] && INBOX_COUNT=$((INBOX_COUNT + 1))
        done
    fi
    if [ "$INBOX_COUNT" -gt 0 ]; then
        echo "[$TS] 补跑 wiki-digest（inbox 有 ${INBOX_COUNT} 篇）" >> "$LOG_FILE"
        cd "$WIKI_DIR" && ./scripts/wiki-cron.sh wiki-digest || true
    else
        # inbox 空，也要记录时间戳防止重复检查
        echo "[$TS] digest: inbox 空，跳过" >> "$LOG_FILE"
        echo "wiki-digest=$(date "+%s")" >> "$LAST_RUNS"
    fi
else
    echo "[$TS] digest: 跳过（下次调度：今天 23:00）" >> "$LOG_FILE"
fi

# ============================================================
# todo-remind: 每天 11:00 和 23:00
# ============================================================
HOUR=$(date "+%H")
if [ "$HOUR" -ge 11 ] && [ "$HOUR" -lt 12 ]; then
    TODO_KEY="todo-morning"
elif [ "$HOUR" -ge 23 ]; then
    TODO_KEY="todo-evening"
else
    TODO_KEY=""
fi

if [ -n "$TODO_KEY" ]; then
    TODO_LAST=$(get_last_run "$TODO_KEY")
    [ -z "$TODO_LAST" ] && TODO_LAST=0
    TODO_DUE=$(to_epoch "${TODAY} ${HOUR}:00")
    if [ "$TODO_LAST" -lt "$TODO_DUE" ]; then
        echo "[$TS] 执行待办提醒" >> "$LOG_FILE"
        "$SCRIPT_DIR/todo-remind.sh" || true
        echo "${TODO_KEY}=$(date "+%s")" >> "$LAST_RUNS"
    else
        echo "[$TS] todo-remind: 今日已提醒，跳过" >> "$LOG_FILE"
    fi
fi
