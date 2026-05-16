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
#   4. 生成 cron 配置（Bark key）
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
mkdir -p "$TARGET"/wiki/.cron/logs
mkdir -p "$TARGET"/scripts
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
if [ -d "$SCRIPT_DIR/template/scripts" ]; then
    cp "$SCRIPT_DIR/template/scripts/"*.sh "$TARGET/scripts/"
    chmod +x "$TARGET/scripts/"*.sh 2>/dev/null || true
    echo "    ✓ 定时任务脚本"
fi

# ── 4. 复制治理文件 ──
if [ -f "$SCRIPT_DIR/CLAUDE.md" ]; then
    cp "$SCRIPT_DIR/CLAUDE.md" "$TARGET/CLAUDE.md"
    echo "    ✓ CLAUDE.md"
fi

# ── 5. 生成 cron 配置 ──
if [ ! -f "$TARGET/wiki/.cron/config.sh" ]; then
    if [ -f "$SCRIPT_DIR/template/wiki/.cron/config.sh.example" ]; then
        cp "$SCRIPT_DIR/template/wiki/.cron/config.sh.example" "$TARGET/wiki/.cron/config.sh"
    else
        cat > "$TARGET/wiki/.cron/config.sh" << 'CONF'
# Wiki Cron 配置

# Bark 推送（填你的 Bark key，留空则不推送手机通知）
BARK_KEY=""
BARK_GROUP="Wiki"
BARK_SERVER="https://api.day.app"
CONF
    fi
    echo "    ✓ cron 配置（wiki/.cron/config.sh）"
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

# ── 7. 配置 crontab ──
if [ "$WITH_CRON" = true ]; then
    CRON_LINE="* * * * * cd \"$TARGET\" && ./scripts/cron-check.sh"

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

# ── 完成 ──
echo ""
echo "═══════════════════════════════════"
echo "  安装完成！"
echo "═══════════════════════════════════"
echo ""
echo "  目录: $TARGET"
echo ""
echo "  下一步:"
echo ""
echo "  1. 配置 Bark 推送（可选）:"
echo "     vim $TARGET/wiki/.cron/config.sh"
echo "     # 填入 BARK_KEY"
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
