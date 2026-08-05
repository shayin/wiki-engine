# quant-scanner

美股短线/中线量化扫描器。聚焦三个痛点：

1. **板块选择（beta）**：行业动量、资金流向
2. **个股筛选（alpha）**：CAN SLIM、SEPA、趋势模板
3. **短线择时**：VCP 形态、突破信号、移动止损

## 设计哲学

借鉴 `ai-tools/ai-hedge-fund/v2/` 三条原则：

- **Signal 输出统一 `[-1, +1]`**：任何信号都归一化为打分，便于组合
- **Point-in-time 数据**：回测中绝不引入未来信息
- **成本 day-1 算**：每笔交易扣除佣金 + 滑点

放弃 v2 的重型组件（Black-Litterman 组合优化、Almgren-Chriss 执行模型）——个人投资者用不到。

## 目录结构

```
quant-scanner/
├── src/
│   ├── data/          # 数据加载层（yfinance + parquet 缓存）
│   ├── signals/       # 信号系统（BaseSignal ABC + 各实现）
│   │   ├── base.py
│   │   ├── trend_template.py    # Minervini 趋势模板 8 条规则
│   │   ├── vcp.py               # VCP 形态（半衰法则 + footprint 标签 + 量能干涸）
│   │   ├── pivot_point.py       # 中枢点精确买点（4 特征 + 突破触发）
│   │   ├── sell_signals.py      # 基底计数 + 强势/弱势卖出
│   │   └── can_slim.py          # CAN SLIM 7 字母打分
│   ├── features/      # 特征工程
│   ├── scanner/       # 扫描引擎 + CLI
│   ├── reporter/      # HTML 报告
│   ├── backtest/      # 事件驱动回测（point-in-time + 滑点佣金）
│   └── utils/
│       └── position_sizing.py   # 期望值 + 二换一 + 交错止损 + 50/80 法则
├── reports/           # 每日扫描 HTML 报告
├── data_cache/        # 日线数据 parquet 缓存
└── tests/             # 32 个测试（含 backtest/VCP/pivot/position_sizing）
```

## 快速开始

```bash
# 安装（建议用 python3.12）
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e .

# 扫描默认 watchlist（NVDA, AAPL, TSLA, META, MSFT, GOOGL, BABA, PDD, TME, QCOM）
quant-scanner scan

# 扫描指定股票
quant-scanner scan TSLA NVDA AAPL

# 扫描 watchlist 文件
quant-scanner scan --watchlist data/watchlist.txt

# 生成 HTML 报告
quant-scanner scan -o reports/today.html

# 回测（默认扫描周频）
quant-scanner backtest --tickers NVDA,AAPL,META,TSLA --start 2022-01-01 --end 2025-12-31

# 单只股票详情
quant-scanner details NVDA
```

## 信号系统

| 信号 | 阈值 | 触发语义 |
|------|------|---------|
| `trend_template` | ≥ 0.875 | 第二阶段（8 条规则至少 7 条通过） |
| `vcp` | ≥ 0.70 | VCP 形态确认（5 维度：趋势+前期涨幅+收缩形态+量能+位置） |
| `pivot_point` | ≥ 0.70 + 突破 | 中枢点精确买点触发（action: BUY/READY/WAIT） |
| `base_counting` | ≤ -0.50 | 基底计数卖出（第 5-6 基底或弱势信号） |
| `can_slim` | ≥ 0.70 | CAN SLIM 7 字母综合打分 |

每个信号都输出 `value ∈ [-1, +1]` + `passed` 布尔 + `details` 字典 + `reasons` 列表。

## 与三本蒸馏书的关系

代码实现直接挂载三本蒸馏书的方法论（cangjie-skill 流程产出）：

| 书 | Skill | 代码 |
|---|---|---|
| 股票魔法师 Ⅱ（Minervini） | VCP / 趋势模板 / 中枢点 / 基底计数 / 期望值 | `signals/{vcp,trend_template,pivot_point,sell_signals}.py` + `utils/position_sizing.py` |
| 笑傲股市（O'Neil） | CAN SLIM 7 字母 / 杯柄形态 / 大盘方向 M / RS Rating | `signals/can_slim.py`（待升级，详见 `ai-wiki/wiki/analysis/笑傲股市/INDEX.md`） |
| 金融市场技术分析（Murphy） | 道氏/形态/振荡指标/市场阶段 | 通用 TA 框架（蒸馏中） |

蒸馏书 SKILL.md 路径：
- `ai-wiki/wiki/analysis/股票魔法师II/`
- `ai-wiki/wiki/analysis/笑傲股市/`
- `ai-wiki/wiki/analysis/金融市场技术分析/`

全局软链接：`~/.claude/skills/books/股票魔法师II-*.md`

## 每日盘后 cron（可选）

```bash
# 安装 crontab（北京时间早 7 点 = 美东盘后）
crontab -l > /tmp/cron.txt
echo "0 7 * * 2-6 cd /Users/shayin/data1/htdocs/project/mind/ai-wiki && bash .cron/custom/quant-scanner/quant-scanner-daily.sh >> .cron/custom/quant-scanner/logs/cron.log 2>&1" >> /tmp/cron.txt
crontab /tmp/cron.txt
```

## 与 wiki 体系整合

```
quant-scanner 产出候选股
    ↓
wiki-research 对前 3 名做深度验证
    ↓
wiki-lens 匹配行为偏差/宏观透镜
    ↓
decisions/ 记录决策
    ↓
每周复盘（scan 历史准确率、信号衰减、参数迭代）
```

## 现实预期

- 第一版胜率预期 40-50%，这是个人量化的常态
- **价值不在"发明 alpha"，而在纪律化执行**
- VCP/CAN SLIM 等公开策略有效性在持续下降，真正优势来自与 wiki 体系的交叉验证

## 数据源

- 行情：yfinance（免费够用）
- 基本面：yfinance fundamentals（EPS、机构持仓等）
- 未来可切换到 Financial Datasets API 或 polygon.io

## 不做什么

- ❌ 不预测股价
- ❌ 不做 HFT / 低延迟
- ❌ 不做期权策略
- ❌ 不自动下单（永远人工决策）

## License

Personal use only.
