#!/bin/bash
#
# sweep-check.sh — wiki-sweep 粗筛脚本
# 纯 shell 实现，零 token，筛出问题项后交给 Claude 精析
#
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CRON_DIR="$(dirname "$SCRIPT_DIR")"
WIKI_DIR="$(dirname "$CRON_DIR")"
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
        item_status=$(grep "^status:" "$f" 2>/dev/null | awk '{print $2}')
        if [ "$item_status" = "active" ]; then
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

# --- Check 4: Topic 页面缺更新 ---
# 对每个 topic，提取其 tags，找到同标签但未在 topic 关联文章列表中的 sources
if [ -d "wiki/topics" ] && [ -d "wiki/sources" ]; then
    while IFS= read -r topic_file; do
        [ -z "$topic_file" ] && continue
        # 提取 topic 的 tags
        topic_tags=$(awk '/^---$/,/^---$/' "$topic_file" | grep "^tags:" | sed 's/tags: *\[//' | sed 's/\]//' | tr ',' '\n' | sed 's/^ *//' | sed 's/ *$//' | tr '\n' '|' | sed 's/|$//')
        [ -z "$topic_tags" ] && continue

        # 提取 topic 已关联的文件名
        related_files=$(grep -oE '\[\[[^]]+\]\]' "$topic_file" 2>/dev/null | sed 's/\[\[//;s/\]\]//' | tr '\n' '|' | sed 's/|$//')

        # 统计同标签 sources 的数量
        match_count=0
        unlisted=""
        for tag in $(echo "$topic_tags" | tr '|' ' '); do
            [ -z "$tag" ] && continue
            while IFS= read -r src; do
                [ -z "$src" ] && continue
                src_name=$(basename "$src" .md)
                # 检查是否已在 topic 的关联列表中
                if [ -z "$related_files" ] || ! echo "$related_files" | grep -q "$src_name"; then
                    unlisted="${unlisted} ${src_name}"
                    match_count=$((match_count + 1))
                fi
            done < <(grep -rl "$tag" wiki/sources/ 2>/dev/null | head -20 || true)
        done

        if [ "$match_count" -gt 0 ]; then
            topic_title=$(head -20 "$topic_file" | grep "^title:" | sed 's/title: *//' || basename "$topic_file" .md)
            ISSUES="${ISSUES}STALE_TOPIC|${topic_file}|${topic_title:-$(basename "$topic_file" .md)}|${match_count} 篇未关联
"
        fi
    done < <(find wiki/topics -name "*.md" 2>/dev/null || true)
fi

# --- Check 5: 孤立文章 ---
# 无标签、无关联、且未被任何 topic 引用的 sources
if [ -d "wiki/sources" ]; then
    # 收集所有 topic 引用的文件名
    all_referenced=""
    if [ -d "wiki/topics" ]; then
        all_referenced=$(grep -roh '\[\[[^]]*\]\]' wiki/topics/ 2>/dev/null | sed 's/\[\[//;s/\]\]//' | sort -u | tr '\n' '|')
    fi

    while IFS= read -r src; do
        [ -z "$src" ] && continue
        src_name=$(basename "$src" .md)

        # 检查是否已被 topic 引用
        if [ -n "$all_referenced" ] && echo "$all_referenced" | grep -q "$src_name"; then
            continue
        fi

        # 检查标签和关联
        tags_line=$(grep "^tags:" "$src" 2>/dev/null || true)
        related_line=$(grep "^related:" "$src" 2>/dev/null || true)
        has_tags="no"
        has_related="no"
        echo "$tags_line" | grep -q '\[' && has_tags="yes"
        echo "$tags_line" | grep -qv 'tags: *\[\]' && has_tags="yes"
        [ -n "$related_line" ] && echo "$related_line" | grep -q '\[\[' && has_related="yes"

        if [ "$has_tags" = "no" ] && [ "$has_related" = "no" ]; then
            title=$(head -20 "$src" | grep "^title:" | sed 's/title: *//' || echo "$src_name")
            ISSUES="${ISSUES}ORPHAN_SOURCE|${src}|${title}|无标签无关联
"
        fi
    done < <(find wiki/sources -name "*.md" 2>/dev/null || true)
fi

# --- Check 6: 断链检查 ---
# 提取所有 [[]] 链接，检查目标文件是否存在
if [ -d "wiki" ]; then
    while IFS= read -r ref; do
        [ -z "$ref" ] && continue
        # ref 格式: 源文件|链接目标
        src_file=$(echo "$ref" | cut -d'|' -f1)
        link_target=$(echo "$ref" | cut -d'|' -f2)

        # 跳过 raw/ 下的链接（原文归档，不强制存在）
        echo "$link_target" | grep -q "^raw/" && continue

        # 检查目标是否存在（尝试多种路径）
        found="no"
        for path in "$link_target" "wiki/${link_target}" "wiki/sources/$(basename "$link_target")" "wiki/topics/$(basename "$link_target")"; do
            if [ -f "$path" ]; then
                found="yes"
                break
            fi
        done

        if [ "$found" = "no" ]; then
            src_title=$(head -20 "$src_file" | grep "^title:" | sed 's/title: *//' || basename "$src_file" .md)
            ISSUES="${ISSUES}BROKEN_LINK|${src_file}|${src_title:-$(basename "$src_file" .md)}|${link_target}
"
        fi
    done < <(grep -roh '\[\[[^]]*\]\]' wiki/sources/ wiki/topics/ wiki/analysis/ 2>/dev/null | sed 's/\[\[//;s/\]\]//' | while read -r link; do
        # 找到包含此链接的源文件
        grep -rl "\[\[${link}\]\]" wiki/sources/ wiki/topics/ wiki/analysis/ 2>/dev/null | while read -r f; do
            echo "${f}|${link}"
        done
    done | sort -u | head -20)
fi

# --- Check 7: 待办分类预检 ---
# 提取跟踪项子标题，检查工作/个人区是否有语义匹配的错放项
# 粗筛只做关键词匹配，精筛由 Claude 做语义判断
if [ -f "todos/active.md" ]; then
    # 提取跟踪项子标题（### xxx），去掉括号内容
    tracking_keywords=""
    while IFS= read -r header; do
        [ -z "$header" ] && continue
        keyword=$(echo "$header" | sed 's/（.*//;s/(.*//' | sed 's/^ *//;s/ *$//')
        tracking_keywords="${tracking_keywords}${keyword}|"
    done < <(sed -n '/^## 跟踪项/,/^## /p' todos/active.md | grep "^### " | sed 's/^### //')

    if [ -n "$tracking_keywords" ]; then
        # 提取工作/个人区的待办（排除长期标题行和空行）
        while IFS= read -r item; do
            [ -z "$item" ] && continue
            # 对每个跟踪关键词做模糊匹配
            echo "$tracking_keywords" | tr '|' '\n' | while read -r kw; do
                [ -z "$kw" ] && continue
                if echo "$item" | grep -qi "$kw"; then
                    desc=$(echo "$item" | sed 's/^- \[[ x]\] *//' | sed 's/ `[^`]*`//g')
                    ISSUES="${ISSUES}TODO_MISPLACED|todos/active.md|${desc}|可能属于跟踪项：${kw}
"
                fi
            done
        done < <(sed -n '/^## 工作/,/^## 个人/p' todos/active.md | grep "^- \[" | head -20; sed -n '/^## 个人/,/^## 闹钟/p' todos/active.md | grep "^- \[" | head -20)
    fi
fi

# --- Check 9: 跨课题关联预检 ---
# 检查是否有多个课题共享标签但无 connection 记录
if [ -d "wiki/analysis" ] && [ -d "wiki/topics" ]; then
    # 提取已有关联中涉及的课题
    existing_connections=""
    if [ -f "wiki/connections/index.md" ]; then
        existing_connections=$(grep -oE '\[[^]]+\]' wiki/connections/index.md 2>/dev/null | tr -d '[]' | sort -u | tr '\n' '|' || true)
    fi

    # 提取各课题 report 的标签
    topic_tags=""
    while IFS= read -r report; do
        [ -z "$report" ] && continue
        topic_name=$(basename "$(dirname "$report")")
        tags=$(awk '/^---$/,/^---$/' "$report" | grep "^topic:" | sed 's/topic: *//' | tr -d '"' || true)
        if [ -n "$tags" ]; then
            topic_tags="${topic_tags}${topic_name}|${tags}
"
        fi
    done < <(find wiki/analysis -name "report.md" 2>/dev/null || true)

    # 提取 topics 的标签
    while IFS= read -r topic_file; do
        [ -z "$topic_file" ] && continue
        topic_name=$(basename "$topic_file" .md)
        tags=$(awk '/^---$/,/^---$/' "$topic_file" | grep "^tags:" | sed 's/tags: *\[//;s/\]//' | tr ',' '\n' | sed 's/^ *//;s/ *$//' | head -3 | tr '\n' ',' | sed 's/,$//' || true)
        if [ -n "$tags" ]; then
            topic_tags="${topic_tags}${topic_name}|${tags}
"
        fi
    done < <(find wiki/topics -name "*.md" 2>/dev/null || true)

    # 简单检测：不同课题间有共同标签且无 connection 记录
    if [ -n "$topic_tags" ]; then
        echo "$topic_tags" | while IFS='|' read -r name tags; do
            [ -z "$name" ] || [ -z "$tags" ] && continue
            for tag in $(echo "$tags" | tr ',' ' '); do
                [ -z "$tag" ] && continue
                matches=$(echo "$topic_tags" | grep -v "^${name}|" | grep "$tag" | cut -d'|' -f1 | sort -u | tr '\n' ',' | sed 's/,$//')
                if [ -n "$matches" ]; then
                    ISSUES="${ISSUES}CROSS_TOPIC|${name}|${matches}|共享标签: ${tag}
"
                fi
            done
        done
    fi
fi

# --- Check 8: 研究数据时效性预检 ---
# 扫描 materials/ 中 frontmatter 的 data_as_of 字段，检查是否超过 6 个月
if [ -d "wiki/analysis" ]; then
    SIX_MONTHS_AGO=$(date -v-6m "+%Y-%m-%d" 2>/dev/null || date -d "6 months ago" "+%Y-%m-%d")
    while IFS= read -r mat; do
        [ -z "$mat" ] && continue
        data_as_of=$(grep "^data_as_of:" "$mat" 2>/dev/null | awk '{print $2}' | tr -d '"')
        if [ -n "$data_as_of" ] && [[ "$data_as_of" < "$SIX_MONTHS_AGO" ]]; then
            title=$(head -20 "$mat" | grep "^title:" | sed 's/title: *//' || basename "$mat" .md)
            ISSUES="${ISSUES}STALE_DATA|${mat}|${title:-$(basename "$mat" .md)}|${data_as_of}
"
        fi
    done < <(find wiki/analysis -path "*/materials/*.md" -name "*.md" 2>/dev/null || true)
fi

# --- 输出标签统计（供 Claude 分析标签碎片） ---
if [ -d "wiki/sources" ]; then
    TAG_STATS=""
    while IFS= read -r line; do
        [ -z "$line" ] && continue
        TAG_STATS="${TAG_STATS}${line}
"
    done < <(grep -h "^tags:" wiki/sources/*.md 2>/dev/null | sed 's/tags: *\[//;s/\]//' | tr ',' '\n' | sed 's/^ *//;s/ *$//' | sort | uniq -c | sort -rn | head -30)

    if [ -n "$TAG_STATS" ]; then
        ISSUES="${ISSUES}TAG_STATS|all|标签统计|${TAG_STATS}
"
    fi
fi

# --- 输出结果 + 微信推送具体发现 + 清 pending 旧记录 ---
source "$CRON_DIR/config.sh" 2>/dev/null || true
PENDING="$CRON_DIR/pending.md"
TS=$(date "+%Y-%m-%d %H:%M:%S")

if [ -z "$ISSUES" ]; then
    echo "ALL CLEAR"
    MSG="🧹 知识库扫描（${TS}）：✅ ALL CLEAR，无问题"
    echo "- [$TS] 🧹 sweep: ALL CLEAR" >> "$PENDING"
else
    # 去掉末尾空行，保留 echo（wiki-cron 兼容）
    echo "$ISSUES" | sed '/^$/d'

    # 统计各类问题
    OVERDUE=$(echo "$ISSUES" | grep -c "OVERDUE_DECISION" || true)
    MISSING=$(echo "$ISSUES" | grep -c "MISSING_FOLLOWUP" || true)
    STALE=$(echo "$ISSUES" | grep -c "STALE_FOLLOWUP" || true)
    STALE_TOPIC_N=$(echo "$ISSUES" | grep -c "STALE_TOPIC" || true)
    ORPHAN=$(echo "$ISSUES" | grep -c "ORPHAN_SOURCE" || true)
    BROKEN=$(echo "$ISSUES" | grep -c "BROKEN_LINK" || true)
    MISPLACED=$(echo "$ISSUES" | grep -c "TODO_MISPLACED" || true)
    CROSS=$(echo "$ISSUES" | grep -c "CROSS_TOPIC" || true)
    STALE_DATA=$(echo "$ISSUES" | grep -c "STALE_DATA" || true)
    TOTAL_ISSUES=$(echo "$ISSUES" | grep -c "|" || true)

    # 构造具体消息（前 10 条问题详情，跳过 TAG_STATS）
    TOP_ISSUES=$(echo "$ISSUES" | grep -v "TAG_STATS" | head -10 | while IFS='|' read -r type file detail extra; do
        [ -z "$type" ] && continue
        case "$type" in
            OVERDUE_DECISION) echo "• ⏰ 到期决策：${detail}（${extra}）" ;;
            MISSING_FOLLOWUP) echo "• ❓ 缺跟踪：${detail} 缺 follow-ups" ;;
            STALE_FOLLOWUP) echo "• 🕐 跟踪逾期：${detail}（${extra}）" ;;
            STALE_TOPIC) echo "• 📑 topic 缺更新：${detail}（${extra}）" ;;
            ORPHAN_SOURCE) echo "• 🔗 孤立文章：${detail}" ;;
            BROKEN_LINK) echo "• 💔 断链：${detail} → ${extra}" ;;
            TODO_MISPLACED) echo "• 📋 待办错放：${detail}" ;;
            CROSS_TOPIC) echo "• 🔗 跨课题关联：${detail} ↔ ${extra}" ;;
            STALE_DATA) echo "• ⏰ 数据过期：${detail}（${extra}）" ;;
        esac
    done)

    MSG="🧹 知识库扫描（${TS}）：发现 ${TOTAL_ISSUES} 个问题
⏰到期决策${OVERDUE} ❓缺跟踪${MISSING} 🕐跟踪逾期${STALE} 💔断链${BROKEN} 🔗孤立${ORPHAN} 📑topic缺更新${STALE_TOPIC_N} ⏰数据过期${STALE_DATA}

${TOP_ISSUES}

说'扫一下'让 AI 精析处理。"

    echo "- [$TS] 🧹 sweep: 发现 ${TOTAL_ISSUES} 个问题（到期${OVERDUE}/断链${BROKEN}/逾期${STALE}）" >> "$PENDING"
fi

# 微信推送（直接 curl，不用 bark）
if [ -n "$WECHAT_ID" ] && [ -n "$WECHAT_PUSH_KEY" ]; then
    curl -s -X POST "${WECHAT_PUSH_SERVER}/api/wechat/push" \
        -H "Authorization: Bearer ${WECHAT_PUSH_KEY}" \
        -H "Content-Type: application/json" \
        -d "$(python3 -c "import json,sys; print(json.dumps({'wechat_id':sys.argv[1],'text':sys.argv[2]}))" "$WECHAT_ID" "$MSG")" >/dev/null 2>&1 &
fi

# 清理 pending.md 7 天前旧记录（防止无限增长）
if [ -f "$PENDING" ]; then
    SEVEN_DAYS_AGO=$(date -v-7d "+%Y-%m-%d" 2>/dev/null || date -d "7 days ago" "+%Y-%m-%d")
    awk -v cutoff="$SEVEN_DAYS_AGO" '
        /^#/ || /^>/ || /^<!--/ || /^-->/ || /^$/ { print; next }
        /\[20[0-9]{2}-[0-9]{2}-[0-9]{2}/ {
            match($0, /\[20[0-9]{2}-[0-9]{2}-[0-9]{2}/)
            date=substr($0, RSTART+1, 10)
            if (date >= cutoff) print
        }
    ' "$PENDING" > "$PENDING.tmp" 2>/dev/null && mv "$PENDING.tmp" "$PENDING" || rm -f "$PENDING.tmp"
fi
