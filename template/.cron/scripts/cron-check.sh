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
CRON_DIR="$(dirname "$SCRIPT_DIR")"
WIKI_DIR="$(dirname "$CRON_DIR")"
LOG_DIR="$CRON_DIR/logs"
LAST_RUNS="$LOG_DIR/last-runs.txt"
LOCK_FILE="$LOG_DIR/.check.lock"

mkdir -p "$LOG_DIR"

# 加载配置
CONFIG="$CRON_DIR/config.sh"
if [ -f "$CONFIG" ]; then
    source "$CONFIG"
fi

# 默认值（未配置时使用）
: "${DIGEST_TIME:=23:00}"
: "${SWEEP_DAY:=Sat}"
: "${SWEEP_TIME:=23:15}"
: "${REVIEW_TIME:=23:15}"
: "${TODO_TIMES:=11:00,23:00}"
TODAY=$(date "+%Y-%m-%d")

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

# --- 工具函数 ---
get_last_run() {
    local task="$1"
    if [ -f "$LAST_RUNS" ]; then
        grep "^${task}=" "$LAST_RUNS" 2>/dev/null | tail -1 | cut -d= -f2 || echo "0"
    else
        echo "0"
    fi
}

to_epoch() {
    date -j -f "%Y-%m-%d %H:%M" "$1" "+%s" 2>/dev/null || echo "0"
}

# 星期几转数字（1=Mon ... 7=Sun）
day_to_dow() {
    case "$1" in
        Mon) echo 1 ;; Tue) echo 2 ;; Wed) echo 3 ;; Thu) echo 4 ;;
        Fri) echo 5 ;; Sat) echo 6 ;; Sun) echo 7 ;;
        *) echo 6 ;;  # 默认周六
    esac
}

# ============================================================
# wiki-sweep: 每周指定日 TIME
# ============================================================
SWEEP_LAST=$(get_last_run "wiki-sweep")
[ -z "$SWEEP_LAST" ] && SWEEP_LAST=0

TARGET_DOW=$(day_to_dow "$SWEEP_DAY")
CURRENT_DOW=$(date "+%u")
DAYS_BACK=$(( (CURRENT_DOW - TARGET_DOW + 7) % 7 ))
LAST_TARGET_DAY=$(date -v-${DAYS_BACK}d "+%Y-%m-%d")
SWEEP_DUE=$(to_epoch "${LAST_TARGET_DAY} ${SWEEP_TIME}")

if [ "$SWEEP_DUE" -gt 0 ] && [ "$NOW_EPOCH" -ge "$SWEEP_DUE" ] && [ "$SWEEP_LAST" -lt "$SWEEP_DUE" ]; then
    echo "[$TS] 补跑 wiki-sweep" >> "$LOG_FILE"
    "$SCRIPT_DIR/wiki-cron.sh" wiki-sweep || true
else
    echo "[$TS] sweep: 跳过（下次调度：${SWEEP_DAY} ${SWEEP_TIME}）" >> "$LOG_FILE"
fi

# ============================================================
# wiki-review: 每天 REVIEW_TIME
# ============================================================
REVIEW_LAST=$(get_last_run "wiki-review")
[ -z "$REVIEW_LAST" ] && REVIEW_LAST=0

REVIEW_DUE=$(to_epoch "${TODAY} ${REVIEW_TIME}")

if [ "$REVIEW_DUE" -gt 0 ] && [ "$NOW_EPOCH" -ge "$REVIEW_DUE" ] && [ "$REVIEW_LAST" -lt "$REVIEW_DUE" ]; then
    echo "[$TS] 补跑 wiki-review" >> "$LOG_FILE"
    "$SCRIPT_DIR/wiki-cron.sh" wiki-review || true
else
    echo "[$TS] review: 跳过（下次调度：今天 ${REVIEW_TIME}）" >> "$LOG_FILE"
fi

# ============================================================
# wiki-digest: 每天指定时间
# ============================================================
DIGEST_LAST=$(get_last_run "wiki-digest")
[ -z "$DIGEST_LAST" ] && DIGEST_LAST=0

DIGEST_DUE=$(to_epoch "${TODAY} ${DIGEST_TIME}")

if [ "$DIGEST_DUE" -gt 0 ] && [ "$NOW_EPOCH" -ge "$DIGEST_DUE" ] && [ "$DIGEST_LAST" -lt "$DIGEST_DUE" ]; then
    INBOX_COUNT=0
    if [ -d "$WIKI_DIR/inbox" ]; then
        for f in "$WIKI_DIR"/inbox/*.md; do
            [ -f "$f" ] && INBOX_COUNT=$((INBOX_COUNT + 1))
        done
    fi
    if [ "$INBOX_COUNT" -gt 0 ]; then
        echo "[$TS] 补跑 wiki-digest（inbox 有 ${INBOX_COUNT} 篇）" >> "$LOG_FILE"
        "$SCRIPT_DIR/wiki-cron.sh" wiki-digest || true
    else
        echo "[$TS] digest: inbox 空，跳过" >> "$LOG_FILE"
        echo "wiki-digest=$(date "+%s")  # $(date "+%Y-%m-%d %H:%M")" >> "$LAST_RUNS"
    fi
else
    echo "[$TS] digest: 跳过（下次调度：今天 ${DIGEST_TIME}）" >> "$LOG_FILE"
fi

# ============================================================
# todo-remind: 每天指定时间（逗号分隔多个）
# ============================================================
HOUR=$(date "+%H")
MINUTE=$(date "+%M")
TODO_KEY=""

for t in $(echo "$TODO_TIMES" | tr ',' ' '); do
    t_hour=$(echo "$t" | cut -d':' -f1 | sed 's/^0//')
    t_min=$(echo "$t" | cut -d':' -f2)
    # 当前小时匹配调度小时，且没超过 60 分钟
    if [ "$HOUR" -ge "$t_hour" ] && [ "$(( HOUR * 60 + MINUTE ))" -lt "$(( t_hour * 60 + 60 ))" ]; then
        TODO_KEY="todo-${t_hour}${t_min}"
        TODO_DUE=$(to_epoch "${TODAY} ${t}")
        break
    fi
done

if [ -n "$TODO_KEY" ]; then
    TODO_LAST=$(get_last_run "$TODO_KEY")
    [ -z "$TODO_LAST" ] && TODO_LAST=0
    if [ "$TODO_LAST" -lt "$TODO_DUE" ]; then
        echo "[$TS] 执行待办提醒" >> "$LOG_FILE"
        "$SCRIPT_DIR/todo-remind.sh" || true
        echo "${TODO_KEY}=$(date "+%s")  # $(date "+%Y-%m-%d %H:%M")" >> "$LAST_RUNS"
    else
        echo "[$TS] todo-remind: 今日已提醒，跳过" >> "$LOG_FILE"
    fi
fi
