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

# 强制 UTF-8 locale，避免 cut -c 在 POSIX locale 下按字节切劈中文（乱码根因）
export LANG="${LANG:-en_US.UTF-8}"
export LC_ALL="${LC_ALL:-en_US.UTF-8}"

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

# 防并发：用 mkdir 作为原子锁（替代 test-then-touch 的 TOCTOU 竞态）
# mkdir 在文件系统层是原子操作，两个并发实例只有一个能成功
if ! mkdir "$LOCK_FILE" 2>/dev/null; then
    # 锁已存在，检查是否过期（>10 分钟视为僵尸锁）
    # stat 失败时视为"未知状态"，强制接管（旧代码 echo "0" 导致 lock_age 巨大锁失效）
    lock_mtime=$(stat -f "%m" "$LOCK_FILE" 2>/dev/null || echo "")
    if [ -n "$lock_mtime" ] && [ $(( $(date "+%s") - lock_mtime )) -lt 600 ]; then
        exit 0
    fi
    # 锁过期或 stat 失败，强制接管
    rmdir "$LOCK_FILE" 2>/dev/null || rm -rf "$LOCK_FILE"
    mkdir "$LOCK_FILE" 2>/dev/null || exit 0
fi
trap 'rmdir "$LOCK_FILE" 2>/dev/null' EXIT

NOW_EPOCH=$(date "+%s")
TODAY=$(date "+%Y-%m-%d")
LOG_FILE="$LOG_DIR/${TODAY}.log"
TS=$(date "+%Y-%m-%d %H:%M:%S")

# --- 工具函数 ---
get_last_run() {
    local task="$1"
    if [ -f "$LAST_RUNS" ]; then
        grep "^${task}=" "$LAST_RUNS" 2>/dev/null | tail -1 | cut -d= -f2 | cut -d' ' -f1 || echo "0"
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
# wiki-sweep: 每天或每周指定日 TIME
# ============================================================
SWEEP_LAST=$(get_last_run "wiki-sweep")
[ -z "$SWEEP_LAST" ] && SWEEP_LAST=0

if [ "$SWEEP_DAY" = "Daily" ]; then
    # 每天模式：和 review 一样的逻辑
    SWEEP_DUE=$(to_epoch "${TODAY} ${SWEEP_TIME}")
else
    # 每周模式：计算最近的目标星期几
    TARGET_DOW=$(day_to_dow "$SWEEP_DAY")
    CURRENT_DOW=$(date "+%u")
    DAYS_BACK=$(( (CURRENT_DOW - TARGET_DOW + 7) % 7 ))
    LAST_TARGET_DAY=$(date -v-${DAYS_BACK}d "+%Y-%m-%d")
    SWEEP_DUE=$(to_epoch "${LAST_TARGET_DAY} ${SWEEP_TIME}")
fi

if [ "$SWEEP_DUE" -gt 0 ] && [ "$NOW_EPOCH" -ge "$SWEEP_DUE" ] && [ "$SWEEP_LAST" -lt "$SWEEP_DUE" ]; then
    echo "[$TS] 补跑 wiki-sweep" >> "$LOG_FILE"
    "$SCRIPT_DIR/wiki-cron.sh" wiki-sweep || true
else
    echo "[$TS] sweep: 跳过（下次调度：${SWEEP_DAY} ${SWEEP_TIME}）" >> "$LOG_FILE"
fi

# ============================================================
# wiki-review: 每天指定时间
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
        # 记录处理前的文章标题（处理后文章移走，先存）
        INBOX_TITLES=""
        for f in "$WIKI_DIR"/inbox/*.md; do
            [ -f "$f" ] || continue
            t=$(head -3 "$f" | grep -E "^# |^title:" | head -1 | sed 's/^# *//;s/^title: *//' | cut -c1-50)
            [ -z "$t" ] && t=$(basename "$f" .md)
            INBOX_TITLES="${INBOX_TITLES}- ${t}
"
        done
        echo "[$TS] 补跑 wiki-digest（inbox 有 ${INBOX_COUNT} 篇）" >> "$LOG_FILE"
        "$SCRIPT_DIR/wiki-cron.sh" wiki-digest || true
        # 微信推送处理结果（文章标题列表）
        MSG="📥 知识库入库（${TS}）：处理了 ${INBOX_COUNT} 篇文章

${INBOX_TITLES}已生成知识卡片入库 sources/，原文移至 raw/。"
        echo "- [$TS] 📥 digest: 处理 ${INBOX_COUNT} 篇" >> "$CRON_DIR/pending.md"
        if [ -n "$WECHAT_ID" ] && [ -n "$WECHAT_PUSH_KEY" ]; then
            curl -s -X POST "${WECHAT_PUSH_SERVER}/api/wechat/push" \
                -H "Authorization: Bearer ${WECHAT_PUSH_KEY}" \
                -H "Content-Type: application/json" \
                -d "$(python3 -c "import json,sys; print(json.dumps({'wechat_id':sys.argv[1],'text':sys.argv[2]}))" "$WECHAT_ID" "$MSG")" >/dev/null 2>&1 &
        fi
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
    # 当前时间在调度时间之后，且不超过 60 分钟窗口
    now_min=$(( 10#$HOUR * 60 + 10#$MINUTE ))
    due_min=$(( t_hour * 60 + t_min ))
    if [ "$now_min" -ge "$due_min" ] && [ "$now_min" -lt "$(( due_min + 60 ))" ]; then
        TODO_KEY="todo-${t_hour}${t_min}"
        TODO_DUE=$(to_epoch "${TODAY} ${t}")
        break
    fi
done

if [ -n "$TODO_KEY" ]; then
    TODO_LAST=$(get_last_run "$TODO_KEY")
    [ -z "$TODO_LAST" ] && TODO_LAST=0
    if [ "$TODO_LAST" -lt "$TODO_DUE" ]; then
        # 原子闸门：用 noclobber 创建当日 flag 文件，成功才推（根治竞态双推）
        # 即便两个 cron 实例同时进入此分支，只有一个能成功创建 flag，另一个必失败 → 跳过
        TODO_FLAG="$LOG_DIR/.${TODO_KEY}-$(date '+%Y%m%d').done"
        if ( set -o noclobber; : > "$TODO_FLAG" ) 2>/dev/null; then
            echo "[$TS] 执行待办提醒" >> "$LOG_FILE"
            "$SCRIPT_DIR/todo-remind.sh" --summary || true
            echo "${TODO_KEY}=$(date "+%s")  # $(date "+%Y-%m-%d %H:%M")" >> "$LAST_RUNS"
        else
            echo "[$TS] todo-remind: 今日已推送（flag 已建），跳过" >> "$LOG_FILE"
        fi
    else
        echo "[$TS] todo-remind: 今日已提醒，跳过" >> "$LOG_FILE"
    fi
fi

# ============================================================
# event-calendar-check: 每天指定时间（逗号分隔，早晚各一次）
# 扫描 macro-tracker 事件日历，有变化调 AI 分析 + 微信推送完整内容
# ============================================================
EC_KEY=""
for t in $(echo "${EVENT_CALENDAR_TIMES:-11:30,19:00}" | tr ',' ' '); do
    t_hour=$(echo "$t" | cut -d':' -f1 | sed 's/^0//')
    t_min=$(echo "$t" | cut -d':' -f2)
    now_min=$(( 10#$HOUR * 60 + 10#$MINUTE ))
    due_min=$(( t_hour * 60 + t_min ))
    if [ "$now_min" -ge "$due_min" ] && [ "$now_min" -lt "$(( due_min + 60 ))" ]; then
        EC_KEY="event-calendar-${t_hour}${t_min}"
        EC_DUE=$(to_epoch "${TODAY} ${t}")
        break
    fi
done

if [ -n "$EC_KEY" ]; then
    EC_LAST=$(get_last_run "$EC_KEY")
    [ -z "$EC_LAST" ] && EC_LAST=0
    if [ "$EC_LAST" -lt "$EC_DUE" ]; then
        echo "[$TS] 执行 event-calendar-check（${EC_KEY}）" >> "$LOG_FILE"
        "$SCRIPT_DIR/event-calendar-check.sh" || true
        echo "${EC_KEY}=$(date "+%s")  # $(date "+%Y-%m-%d %H:%M")" >> "$LAST_RUNS"
    else
        echo "[$TS] event-calendar: 今日该时段已跑，跳过" >> "$LOG_FILE"
    fi
fi

# ============================================================
# todo-remind (per-minute): 每分钟检查带 remind 标记的待办
# ============================================================
"$SCRIPT_DIR/todo-remind.sh" || true
