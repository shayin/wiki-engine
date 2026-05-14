#!/bin/bash
#
# wiki-engine 部署脚本
# 用法: ./deploy.sh --target <安装目录>
#
# 示例:
#   ./deploy.sh --target ~/my-wiki
#   ./deploy.sh --target /path/to/wiki
#

set -e

TARGET=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --target)
            TARGET="$2"
            shift 2
            ;;
        *)
            echo "未知参数: $1"
            echo "用法: $0 --target <安装目录>"
            exit 1
            ;;
    esac
done

if [ -z "$TARGET" ]; then
    echo "用法: $0 --target <安装目录>"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENGINE_DIR="$(dirname "$SCRIPT_DIR")"

# 转为绝对路径
TARGET="$(cd "$(dirname "$TARGET")" 2>/dev/null && pwd)/$(basename "$TARGET")" || TARGET="$TARGET"

echo "==> 部署 wiki-engine"
echo "    目标目录: $TARGET"
echo "    引擎目录: $ENGINE_DIR"

# 创建目标目录
mkdir -p "$TARGET"

# 复制模板目录结构
if [ -d "$ENGINE_DIR/template" ]; then
    echo "==> 复制模板..."
    cp -r "$ENGINE_DIR/template/"* "$TARGET/"
    echo "    ✓ inbox/"
    echo "    ✓ raw/"
    echo "    ✓ wiki/"
fi

# 复制治理文件
if [ -f "$ENGINE_DIR/CLAUDE.md" ]; then
    cp "$ENGINE_DIR/CLAUDE.md" "$TARGET/"
    echo "    ✓ CLAUDE.md"
fi

echo "==> 完成！"
echo ""
echo "下一步："
echo "  cd $TARGET"
echo "  开始使用 digest skill 处理文章"
