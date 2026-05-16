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
    declare -A broken_links
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
            key="${src_file}|${link_target}"
            if [ -z "${broken_links[$key]}" ]; then
                broken_links["$key"]=1
                src_title=$(head -20 "$src_file" | grep "^title:" | sed 's/title: *//' || basename "$src_file" .md)
                ISSUES="${ISSUES}BROKEN_LINK|${src_file}|${src_title:-$(basename "$src_file" .md)}|${link_target}
"
            fi
        fi
    done < <(grep -roh '\[\[[^]]*\]\]' wiki/sources/ wiki/topics/ wiki/analysis/ 2>/dev/null | sed 's/\[\[//;s/\]\]//' | while read -r link; do
        # 找到包含此链接的源文件
        grep -rl "\[\[${link}\]\]" wiki/sources/ wiki/topics/ wiki/analysis/ 2>/dev/null | while read -r f; do
            echo "${f}|${link}"
        done
    done | sort -u | head -20)
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

# --- 输出结果 ---
if [ -z "$ISSUES" ]; then
    echo "ALL CLEAR"
else
    # 去掉末尾空行
    echo "$ISSUES" | sed '/^$/d'
fi
