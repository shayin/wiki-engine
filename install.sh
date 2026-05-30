#!/bin/bash
#
# install.sh — 一键安装 wiki-engine
#
# 用法:
#   ./install.sh ~/my-wiki              # 安装到指定目录
#   ./install.sh ~/my-wiki --with-cron  # 同时配置 crontab
#
# 流程:
#   1. 创建目录结构
#   2. 复制模板和脚本
#   3. 安装 skills 到 ~/.claude/skills/
#   4. 生成 cron 配置（Bark key + 微信推送）
#   5. 可选：配置 crontab
#

set -e

TARGET=""
WITH_CRON=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --with-cron) WITH_CRON=true; shift ;;
        -*)
            echo "未知参数: $1"
            echo "用法: $0 <目标目录> [--with-cron]"
            exit 1
            ;;
        *)
            if [ -z "$TARGET" ]; then
                TARGET="$1"
            else
                echo "未知参数: $1"
                exit 1
            fi
            shift
            ;;
    esac
done

if [ -z "$TARGET" ]; then
    echo "用法: $0 <目标目录> [--with-cron]"
    echo ""
    echo "示例:"
    echo "  $0 ~/my-wiki              # 安装到 ~/my-wiki"
    echo "  $0 ~/my-wiki --with-cron  # 安装 + 配置定时任务"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TARGET="$(cd "$(dirname "$TARGET")" 2>/dev/null && pwd)/$(basename "$TARGET")" || TARGET="$TARGET"

echo ""
echo "╔══════════════════════════════════╗"
echo "║     AI Wiki Engine 安装向导      ║"
echo "╚══════════════════════════════════╝"
echo ""
echo "  引擎目录: $SCRIPT_DIR"
echo "  目标目录: $TARGET"
echo ""

# ── 1. 创建目录结构 ──
echo "==> 创建目录结构..."
mkdir -p "$TARGET"/{inbox,raw,context,decisions,todos}
mkdir -p "$TARGET"/wiki/{sources,topics,analysis}
mkdir -p "$TARGET"/.cron/{logs,scripts}
echo "    ✓ 目录结构创建完成"

# ── 2. 复制模板文件 ──
echo "==> 复制模板..."
if [ -d "$SCRIPT_DIR/template" ]; then
    # wiki 初始文件（不覆盖已有）
    for f in wiki/index.md wiki/log.md todos/active.md; do
        if [ -f "$SCRIPT_DIR/template/$f" ] && [ ! -f "$TARGET/$f" ]; then
            mkdir -p "$(dirname "$TARGET/$f")"
            cp "$SCRIPT_DIR/template/$f" "$TARGET/$f"
        fi
    done
    echo "    ✓ 模板文件"
fi

# ── 3. 复制脚本 ──
if [ -d "$SCRIPT_DIR/template/.cron/scripts" ]; then
    cp "$SCRIPT_DIR/template/.cron/scripts/"*.sh "$TARGET/.cron/scripts/"
    chmod +x "$TARGET/.cron/scripts/"*.sh 2>/dev/null || true
    echo "    ✓ 定时任务脚本"
fi

# ── 4. 复制治理文件 ──
if [ -f "$SCRIPT_DIR/CLAUDE.md" ]; then
    cp "$SCRIPT_DIR/CLAUDE.md" "$TARGET/CLAUDE.md"
    echo "    ✓ CLAUDE.md"
fi

# ── 5. 生成 cron 配置 ──
if [ ! -f "$TARGET/.cron/config.sh" ]; then
    echo ""
    echo "==> 配置推送（留空跳过）"
    echo ""

    # Bark 推送
    read -p "  Bark Key（App Store 下载 Bark 获取，留空跳过）: " INPUT_BARK_KEY
    read -p "  Bark Group（默认 Wiki）: " INPUT_BARK_GROUP
    INPUT_BARK_GROUP="${INPUT_BARK_GROUP:-Wiki}"

    # 微信推送
    echo ""
    read -p "  微信推送 ID（企业微信用户 ID，留空跳过）: " INPUT_WECHAT_ID
    read -p "  微信推送 Key（推送服务认证密钥）: " INPUT_WECHAT_KEY
    read -p "  微信推送服务器地址（如 http://1.2.3.4:6022）: " INPUT_WECHAT_SERVER

    cat > "$TARGET/.cron/config.sh" << CONF
# Wiki Cron 配置

# ---- 推送 ----

# Bark 推送（留空则不推送手机通知）
BARK_KEY="${INPUT_BARK_KEY}"
BARK_GROUP="${INPUT_BARK_GROUP}"
BARK_SERVER="https://api.day.app"

# 微信推送（留空则不推送到微信）
WECHAT_ID="${INPUT_WECHAT_ID}"
WECHAT_PUSH_KEY="${INPUT_WECHAT_KEY}"
WECHAT_PUSH_SERVER="${INPUT_WECHAT_SERVER}"

# ---- 调度时间 ----

# digest: 每天处理 inbox 的时间（24小时制）
DIGEST_TIME="23:00"

# sweep: 知识库健康度扫描（星期 + 时间）
SWEEP_DAY="Sat"
SWEEP_TIME="23:15"

# review: 决策复盘（每天扫描到期决策）
REVIEW_TIME="23:15"

# todo-remind: 待办提醒（多个时间逗号分隔）
TODO_TIMES="11:00,23:00"
CONF
    echo ""
    echo "    ✓ cron 配置（.cron/config.sh）"
else
    echo "    ✓ cron 配置（已存在，跳过）"
fi

# ── 6. 安装 skills 到 CC ──
echo "==> 安装 Skills..."
CC_SKILLS_DIR="$HOME/.claude/skills"
if [ -d "$SCRIPT_DIR/skills" ]; then
    mkdir -p "$CC_SKILLS_DIR"
    SKILL_COUNT=0
    for skill_dir in "$SCRIPT_DIR/skills"/*/; do
        if [ -d "$skill_dir" ]; then
            skill_name=$(basename "$skill_dir")
            mkdir -p "$CC_SKILLS_DIR/$skill_name"
            cp "$skill_dir"SKILL.md "$CC_SKILLS_DIR/$skill_name/SKILL.md" 2>/dev/null || true
            # 复制附属文件（如 test-prompts.json）
            cp "$skill_dir"*.json "$CC_SKILLS_DIR/$skill_name/" 2>/dev/null || true
            SKILL_COUNT=$((SKILL_COUNT + 1))
            echo "    ✓ wiki-$skill_name"
        fi
    done
    echo "    共安装 ${SKILL_COUNT} 个 skills → $CC_SKILLS_DIR"
else
    echo "    ⚠ skills/ 目录不存在，跳过"
fi

# 检查 cangjie-skill 依赖（book2skill，用于书籍蒸馏）
echo "    检查 cangjie-skill 依赖..."
if [ -d "$CC_SKILLS_DIR/cangjie-skill" ]; then
    echo "    ✓ cangjie-skill（已存在，跳过）"
else
    echo "    ⚠ cangjie-skill 未安装"
    echo "      书籍蒸馏功能需要 cangjie-skill，请获取并安装到 $CC_SKILLS_DIR/cangjie-skill/"
fi

# 创建书籍 skill 全局目录和索引
BOOKS_DIR="$CC_SKILLS_DIR/books"
mkdir -p "$BOOKS_DIR"
if [ ! -f "$BOOKS_DIR/_index.md" ]; then
    cat > "$BOOKS_DIR/_index.md" << 'INDEX'
# Books Skill 索引

书籍研究蒸馏出的方法论 skill，通过 wiki-research 的书籍蒸馏流程自动生成。

<!-- 格式：- [课题名 - skill名](books/课题名-skill名.md) — 一句话摘要 -->
INDEX
    echo "    ✓ books/ 目录和索引已创建"
else
    echo "    ✓ books/ 索引（已存在，跳过）"
fi

# ── 7. 配置 crontab ──
if [ "$WITH_CRON" = true ]; then
    CRON_LINE="* * * * * cd \"$TARGET\" && .cron/scripts/cron-check.sh"

    # 检查是否已配置
    if crontab -l 2>/dev/null | grep -q "cron-check.sh"; then
        echo ""
        echo "==> crontab 已有 cron-check.sh 配置，跳过"
        echo "    如需更新，手动执行: crontab -e"
    else
        (crontab -l 2>/dev/null || true; echo "$CRON_LINE") | crontab -
        echo "    ✓ crontab 已配置（每分钟检查）"
    fi
fi

# ── 8. 注册 WIKI_ROOT 环境变量 ──
echo "==> 注册环境变量..."
SHELL_RC=""
if [ -n "$ZSH_VERSION" ]; then
    SHELL_RC="$HOME/.zshrc"
elif [ -n "$BASH_VERSION" ]; then
    SHELL_RC="$HOME/.bashrc"
else
    SHELL_RC="$HOME/.profile"
fi

# 移除旧的 WIKI_ROOT 配置（如有）
if [ -f "$SHELL_RC" ]; then
    sed -i.bak '/# wiki-engine$/,/WIKI_ROOT/d' "$SHELL_RC" 2>/dev/null || true
    rm -f "$SHELL_RC.bak"
fi

# 追加新的配置
cat >> "$SHELL_RC" << ENVCODE

# wiki-engine
export WIKI_ROOT="$TARGET"
ENVCODE

echo "    ✓ WIKI_ROOT=$TARGET → $SHELL_RC"

# ── 完成 ──
echo ""
echo "═══════════════════════════════════"
echo "  安装完成！"
echo "═══════════════════════════════════"
echo ""
echo "  目录: $TARGET"
echo "  环境变量: WIKI_ROOT=$TARGET"
echo ""
echo "  下一步:"
echo ""
echo "  1. 修改推送配置（可选）:"
echo "     vim $TARGET/.cron/config.sh"
echo "     # 配置 BARK_KEY / WECHAT_ID / WECHAT_PUSH_KEY"
echo ""
echo "  2. 配置个人上下文（可选）:"
echo "     vim $TARGET/context/finance.md"
echo ""
if [ "$WITH_CRON" = false ]; then
    echo "  3. 配置定时任务（可选）:"
    echo "     ./install.sh $TARGET --with-cron"
    echo ""
fi
echo "  开始使用:"
echo "    cd $TARGET"
echo "    claude"
echo "    > 发一个链接试试  # digest 自动处理"
echo ""
