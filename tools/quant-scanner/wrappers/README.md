# quant-scanner 桥接层（wrappers）

## 定位

quant-scanner 项目本体在**上一级目录**（`wiki-engine/tools/quant-scanner/`，含 src/ tests/ docs/）。
本目录（`wrappers/`）只放 wiki-engine 调用 quant-scanner 的入口脚本。

## 文件清单

| 文件 | 用途 |
|------|------|
| `run-scan.sh` | 跑 `quant-scanner scan`，输出当前触发形态（JSON） |
| `run-stats.sh` | 跑 `quant-scanner stats`，生成 HTML 胜率报告 |
| `parse-html.py` | HTML → markdown 摘要（核心结论 + 顶层表），微信端兼容关键 |

## 用法

```bash
cd wiki-engine/tools/quant-scanner/wrappers

# 快速扫描
./run-scan.sh NVDA
./run-scan.sh NVDA AAPL TSLA --workers 4

# 胜率统计（默认 2y，price-only）
HTML_PATH=$(./run-stats.sh NVDA AAPL TSLA)
python3 parse-html.py "$HTML_PATH"   # 转 markdown
```

## 路径策略

脚本用相对路径定位项目根（`$(dirname "$0")/..`），不依赖绝对路径。整个 `wiki-engine/tools/quant-scanner/` 目录可以整体迁移，wrappers 仍能工作。

## 与 ai-tools 的关系

2026-07-18 物理迁移前，quant-scanner 曾位于 `ai-tools/quant-scanner/`（独立项目）。现已合并到 wiki-engine 下，ai-tools 中无副本。
