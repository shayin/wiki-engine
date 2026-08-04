---
name: wiki-quant
description: 美股技术面分析能力。调用 wiki-engine/tools/quant-scanner 扫描当前触发的技术形态（16 种信号：双底/VCP/杯柄/RSI 背离等），统计形态历史胜率（含 holdout 验证 + Wilson 区间 + 自动结论），或跑 WorldQuant Alpha101 学术因子 IC 评估（20 个公式化 alpha + 信息系数 + 信息比率）。补上 wiki-research 流程中"技术面"一栏的硬数据缺口。当用户问"X 公司技术面怎样"、"跑一下形态扫描"、"X 历史胜率"、"X 形态准不准"、"X 的 alpha 因子"、"X 统计套利视角"时触发。
---

# wiki-quant — 美股技术面分析能力

> **定位**：补 wiki-research 流程中"技术面"维度的硬数据缺口。LLM 凭搜索讲技术面 = 主观，wiki-quant 调 quant-scanner = 硬数据。
>
> **调用方**：wiki-research（步骤 3「看多/看空信号对比表」技术面栏 + Alpha 因子扫描，强制）/ wiki-mine（候选股批量打分）/ 用户直接触发

## 工具依赖

quant-scanner 已物理迁移进 wiki-engine（2026-07-18），无独立仓库：

- **工具本体**：`wiki-engine/tools/quant-scanner/`（src/ tests/ docs/ + 16 个 signal + 回测 + 胜率统计）
- **桥接层**：`wiki-engine/tools/quant-scanner/wrappers/`
  - `run-scan.sh`：快速扫描当前触发形态（16 信号）
  - `run-stats.sh`：跑胜率统计生成 HTML 报告
  - `run-indicators.sh`：**单股全套指标**（~22 个含成交量 OBV/MFI/VWAP/布林/KDJ/ADX/量价背离）—— P0
  - `run-sector.sh`：**板块 RS 排名 + 轮动 + 启动信号**（找板块机会）—— P1
  - `run-market.sh`：**大盘趋势 + VIX + 风格轮动 + risk-on/off**（系统性背景）—— P2
  - `run-screen.sh`：**选股扫描**（breakout 突破+放量 / trend 趋势第二阶段 / momentum 短线动量）—— P1
  - `parse-html.py`：HTML → markdown 摘要（微信端兼容）

## 触发条件

**显式触发词**：
- "X 公司技术面怎么样 / 技术面扫描 / X 指标怎样"
- "跑一下 quant-scanner / 跑形态扫描"
- "X 历史胜率 / X 形态准不准"
- "这批股票批量扫一下"
- "板块轮动 / 哪个板块强 / 板块启动 / 找板块机会" → `run-sector.sh`（板块 RS + 轮动）
- "选股 / 突破股 / 趋势股 / 短线动量股" → `run-screen.sh breakout/trend/momentum`
- "大盘怎样 / 市场状态 / risk-on off / 风格轮动" → `run-market.sh`（大盘背景）
- "X 全套指标 / X 成交量 / X 的 MACD 布林 OBV" → `run-indicators.sh X`
- "X 的 alpha / X 的因子 / X 统计套利 / X 的 WorldQuant / X 的 IC" → 模式 4（Alpha 因子扫描）

**隐式触发**（由 wiki-research 自动调用）：
- wiki-research 步骤 3「反面论据检查」构造「看多/看空信号对比表」时，技术面一栏**必须**调用本 skill（模式 1）
- wiki-research 步骤 3「Alpha 因子扫描」段**必须**调用本 skill（模式 4，决策型个股研究）
- wiki-mine 挖出 ≥3 只候选股时，**建议**批量扫描

## 🔴 强制规则（2026-08-03）—— ADX 趋势强度过滤

所有技术面分析**必须先跑 `trend_regime` signal（ADX）判断趋势强度**，再决定 RSI/MACD 是否有效：
- **ADX≥25（强趋势）**：RSI 超买/超卖是**正常强势/弱势**，不是卖/买信号。看 **MACD 柱收敛/扩张 + 顶背离** 才是真信号。
- **ADX≤20（震荡市）**：RSI 超买/超卖**有效**（均值回归可参考）。
- **禁止**：在 ADX≥25 强趋势中机械说"RSI 超买该减"——震荡指标在趋势中失效（Murphy 第 10 章）。
- **历史教训（2026-08-03）**：用户质疑 RSI 超买合理性后验证，美团 ADX 30.0/BABA ADX 26.6 强趋势中 RSI 70+ 超买正常；PDD ADX 19.4 震荡中 RSI 才有效。`trend_regime` signal 本来就有（Wilder ADX + 道氏阶段），问题不是工具缺失而是分析时没调用。

## 三种调用模式

### 模式 1：快速扫描（看当前触发）

**场景**：用户问"NVDA 现在技术面怎样"，需要回答"当前触发了哪些形态"

**执行**：
```bash
bash /Users/shayin/data1/htdocs/project/mind/ai-wiki/wiki-engine/tools/quant-scanner/wrappers/run-scan.sh NVDA
```

**输出**：JSON，包含每个 signal 的 SignalResult（score -1~+1、details 形态类型、目标价等）

**AI 处理**：
- 提取 score ≥ 0.5 的强信号
- 按 signal_name 中文化（如 `major_reversal` → 重大反转形态，`DOUBLE_BOTTOM` → 双底）
- 按"强做多 / 弱做多 / 中性 / 弱做空 / 强做空"分类
- 引用 `details.pattern_type` 说明具体触发的是哪种细分形态

**输出格式**（给用户的回复）：
```
NVDA 当前技术面扫描（截至 YYYY-MM-DD）：

🟢 强做多信号：
- 道氏三阶段（dow_phases）：score 0.8，吸筹阶段
- 重大反转形态（major_reversal）：score 0.7，双底确认

🟡 弱做多信号：
- 趋势模板（trend_template）：score 0.5，满足 8 条中的 5 条

🔴 强做空信号：（无）

净方向：多
```

### 模式 2：胜率验证（看历史命中率）

**场景**：用户问"NVDA 历史上双底形态准不准"、"VCP 形态在 NVDA 上胜率多少"

**执行**：
```bash
# 多标的统计才有意义（单标的样本太小）
bash /Users/shayin/data1/htdocs/project/mind/ai-wiki/wiki-engine/tools/quant-scanner/wrappers/run-stats.sh NVDA AAPL TSLA --period 2y --horizon 20
```

脚本会输出 HTML 路径到 stdout 最后一行。然后 AI 解析：
```bash
HTML_PATH=$(bash run-stats.sh NVDA AAPL TSLA --period 2y)
/Users/shayin/data1/htdocs/project/mind/ai-wiki/wiki-engine/tools/quant-scanner/.venv/bin/python \
  /Users/shayin/data1/htdocs/project/mind/ai-wiki/wiki-engine/tools/quant-scanner/wrappers/parse-html.py \
  "$HTML_PATH"
```

**输出**：markdown 摘要，包含：
- 🎯 核心结论（自动生成）：最强形态、最弱形态、过拟合警示、样本量警告
- 📊 顶层信号总表（按 EV 降序）
- 🔍 二级细分表（信号 × 形态 × 方向）
- 🛡 holdout 段验证表（如果通过验证）

**AI 处理**：
- 直接把 markdown 摘要回用户（微信端可读）
- **不要**贴 HTML 链接（用户在 cf 微信端看不到）
- 关键结论必须翻译为非专业用户能懂的话（如"EV +8.13%" → "平均每笔赚 8.13%"）

**样本量底线**（强制告知）：
- 单标的 2y 数据通常触发 <20 事件，灰色级
- 至少 5 只标的 × 2y 起步，才有低置信级（50-200）样本
- 灰色级结论必须标注"仅供参考，不建议直接用于实盘"

### 模式 3：批量选股打分

**场景**：wiki-mine 挖出 5 只候选股，需要按"形态质量"排序

**执行**：对每只标的跑模式 1 快速扫描，然后按"强做多信号数量 + 综合分"排序

```bash
for ticker in NVDA AAPL TSLA AMD META; do
  echo "=== $ticker ==="
  bash run-scan.sh "$ticker" 2>&1 | head -20
done
```

**输出**：候选股排序表

| 标的 | 强做多 | 弱做多 | 中性 | 净方向 | 综合分 |
|------|-------|-------|------|--------|--------|
| NVDA | 2 | 1 | 0 | 多 | +0.8 |
| AAPL | 1 | 2 | 0 | 多 | +0.5 |
| TSLA | 0 | 1 | 2 | 平 | 0 |

### 模式 4：Alpha 因子扫描（学术公式 IC 评估）

**场景**：
- wiki-research 步骤 3 自动调用（决策型个股研究必填）
- 用户问"X 有哪些 alpha 因子有效 / X 的统计套利视角 / 跑一下 X 的 alpha"
- 需要客观的"统计结构"佐证主观形态判断

**执行**：
```bash
cd /Users/shayin/data1/htdocs/project/mind/ai-wiki/wiki-engine/tools/quant-scanner

# 单股评估
.venv/bin/python -m quant_scanner.scanner.cli factors eval --ticker NVDA --horizon 5

# 只跑指定 alpha
.venv/bin/python -m quant_scanner.scanner.cli factors eval --ticker NVDA --alpha 6

# 批量扫描（候选股池）
for t in NVDA AAPL TSLA META GOOGL; do
  echo "=== $t ==="
  .venv/bin/python -m quant_scanner.scanner.cli factors eval --ticker "$t" --horizon 5
done
```

**输出**：CLI 表格，82 个 alpha（论文 1-101 去除 19 个 IndNeutralize/cap 跳过）按 |IC| 降序排列，含 IC/IR/非NaN 列。`|IC| > 0.03` 标绿色。

**AI 处理**：
1. 提取 top 5 alpha（名称、IC、IR、方向）
2. 用 `wiki-engine/tools/quant-scanner/src/quant_scanner/factors/alpha101.py` 里的 docstring 解释每个公式在算什么（如 alpha_3 = 开盘价 vs 成交量的反向相关）
3. 判断整体置信度：
   - top 5 中 ≥ 3 个 |IC| > 0.05 → "统计结构强，技术面结论可信"
   - top 5 中 ≥ 3 个 |IC| < 0.02 → "无历史可类比结构，技术面结论降级"
   - top alpha 方向与 trend_template/vcp/can_slim 等 Signal 结论冲突 → 必须显式告知用户
4. 写入 `report.md` 详细分析段的 `### Alpha 因子扫描` 子段（格式见 `ai-wiki/CLAUDE.md` report.md 模板）
5. 落盘 `materials/alpha-factors-{YYYYMMDD}.md` 作为审计材料

**输出格式**（给用户的回复）：
```
NVDA Alpha 因子扫描（截至 YYYY-MM-DD，2 年样本，5 日前瞻）：

🎯 有效因子（|IC| > 0.05）：
- alpha_3（开盘价×成交量反向相关） IC=+0.07 IR=+0.52 → 印证量价背离结构
- alpha_12（VWAP 偏离×量能变化） IC=-0.06 IR=-0.41 → 反向因子

⚠️ 警惕：
- alpha_9 IC=+0.18 → 异常高，疑似过拟合或被挖烂

整体判断：量价结构因子持续有效，与 trend_template 看多结论一致，置信度高。
```

**学术标准速查**：
- IC = Information Coefficient（信息系数），因子值 vs 前瞻收益的 Spearman 秩相关
- |IC| > 0.03 有微弱信号 / > 0.05 有效 / > 0.10 警惕过拟合
- IR = IC 均值 / IC 标准差（信息比率），> 0.5 高质量

**与模式 1 的区别**：
- 模式 1（快速扫描）：看"当前触发了哪些形态"（定性、当下）
- 模式 4（Alpha 因子）：看"哪些数学结构在历史上预测过这只股票"（定量、回测）
- 两者互补：模式 1 给当前信号，模式 4 给信号的统计可信度

### 模式 4b：Alpha 因子多周期 IC 矩阵（找最佳持仓期）

**场景**：
- 用户问"X 的 alpha 适合短线还是长线 / X 各周期 IC / 哪个 alpha 适合 20 日持仓"
- wiki-research 步骤 3 当单周期 IC 边界模糊（如 top IC=0.04 介于有效和噪声之间）时，自动追加跑矩阵确认

**执行**：
```bash
cd /Users/shayin/data1/htdocs/project/mind/ai-wiki/wiki-engine/tools/quant-scanner

# 默认 5 个 horizon：1d/5d/10d/20d/60d
.venv/bin/python -m quant_scanner.scanner.cli factors matrix --ticker NVDA

# 自定义 horizon
.venv/bin/python -m quant_scanner.scanner.cli factors matrix --ticker NVDA --horizons 3,7,14,30,90

# 输出 HTML
.venv/bin/python -m quant_scanner.scanner.cli factors matrix --ticker NVDA --html reports/nvda-matrix.html
```

**输出**：矩阵表，每行一个 alpha，每列一个 horizon，单元格颜色：
- 深绿/深红：|IC| > 0.05（强信号）
- 浅绿/浅红：|IC| 0.03-0.05（弱信号）
- 灰色：|IC| < 0.03（噪声）

**AI 处理**：
1. 看每个 alpha 的「最佳周期」列，确定它适合的持有期
2. 找跨周期稳定的 alpha（如 1d/5d/10d/20d 都 > 0.05 = 多周期通用）
3. 找 regime 专属 alpha（只在某周期有效，如 IC@1d 强但 IC@60d=0 → 纯短线因子）
4. 报告写入 `report.md` 详细分析段的 `### Alpha 因子扫描` 子段下方追加 `**多周期矩阵**` 子项

**输出格式**（给用户）：
```
NVDA Alpha 多周期矩阵：
- alpha_3（量价反向相关）：1d=0.02 / 5d=+0.08 / 10d=+0.06 / 20d=+0.04 / 60d=+0.01 → 5 日短线最强
- alpha_6：60d=-0.05 → 长线反向因子，60 日持有期有效
- alpha_12：跨周期稳定（1d/5d/10d 均 >0.05）→ 通用因子
```

### 模式 5：出场策略（ExitStrategySignal）

**场景**：
- 用户问"X 还该持有吗 / X 该卖吗 / X 的止损价是多少 / X 持仓 N 天了怎么办"
- wiki-research 步骤 4「个人建议」段需要给止损/止盈价位时
- 持仓已建仓，需要持续监控出场信号

**设计依据**（HANDOFF 回测锚点，2026-07-19）：

| 策略 | EV | 盈亏比 | 结论 |
|------|-----|-------|------|
| **纯持有 60d（time exit）** | **+4.21%** | **2.12** | ✅ 最优 |
| 2×ATR 紧止损 | +0.34% | — | ❌ 60% 被洗出，截断赢家 |
| sell_signal 基底计数 | -0.32% | — | ❌ 过早退出 |

→ 默认参数对齐最优策略：宽 ATR 止损（3×ATR）+ 60d 时间止损为主，MA 信号只触发 REDUCE/WATCH 警告，不主动 EXIT。

**执行**：
```bash
cd /Users/shayin/data1/htdocs/project/mind/ai-wiki/wiki-engine/tools/quant-scanner

# 已建仓：传 entry_price + entry_date
.venv/bin/python -c "
import pandas as pd
from quant_scanner.data.loader import DataLoader
from quant_scanner.signals.exit_strategy import ExitStrategySignal

df = DataLoader().load('NVDA', period='1y')
sig = ExitStrategySignal(entry_price=180.0, entry_date='2026-06-01')
sr = sig.evaluate('NVDA', df)
print('action:', sr.details['action'])
print('current_stop:', sr.details['current_stop'])
print('target:', sr.details['target'])
print('exit_reasons:', sr.details['exit_reasons'])
print('reduce_reasons:', sr.details['reduce_reasons'])
"

# 未建仓：工具会用最近 60 日最低点占位（用于查看"如果现在入场，止损/止盈在哪"）
.venv/bin/python -c "
from quant_scanner.data.loader import DataLoader
from quant_scanner.signals.exit_strategy import ExitStrategySignal
df = DataLoader().load('NVDA', period='1y')
sr = ExitStrategySignal().evaluate('NVDA', df)
print(sr.details)
"
```

**输出字段**：
- `action`：`HOLD` / `WATCH`（跌破 MA5）/ `TIGHTEN_STOP`（跌破 MA13）/ `REDUCE`（跌破 MA30）/ `EXIT`（硬触发）
- `current_stop`：当前有效止损价（trailing max(initial, trailing)）
- `initial_stop`：入场时 ATR 止损 = entry − 3×ATR
- `trailing_stop`：移动止损 = max_high − 2.5×ATR
- `target`：止盈目标 = entry + 3×risk_per_share（3:1 风险比）
- `holding_days`：持仓交易日数
- `current_return_pct`：当前收益率
- `exit_reasons`：硬触发清仓原因（跌破止损 / 时间止损）
- `reduce_reasons`：软触发减仓原因（跌破 MA30）

**AI 处理**：
1. 读 `action` 字段决定建议：
   - `HOLD/WATCH` → 继续持有，关注 MA5 警告
   - `TIGHTEN_STOP` → 建议把止损上移到 `current_stop`
   - `REDUCE` → 建议减仓 1/3（不是清仓）
   - `EXIT` → 必须清仓，引用 `exit_reasons`
2. 给出具体价位：止损 `current_stop`、目标 `target`、当前距止损百分比
3. **禁止把"紧止损"建议改为"立刻清仓"**——违反回测最优策略

**输出格式**（给用户）：
```
NVDA 出场策略（入场 2026-06-01 @ $180，持有 48 天）：

🟢 当前建议：HOLD（继续持有）
- 当前价：$195.50（+8.6%）
- 止损价：$168.20（-14.0%，宽止损防洗出）
- 目标价：$240.00（+33%，3:1 风险比）
- 距目标进度：43%

⚠️ 监控点：
- 跌破 MA5=$193 → 警告
- 跌破 MA13=$185 → 建议紧止损到 $180
- 跌破 MA30=$175 → 减仓 1/3
- 持仓 60 日未达 +20% → 时间止损
```

**落盘**：写入 `analysis/{课题}/materials/exit-strategy-{YYYYMMDD}.md`

### 模式 6：仓位管理 × 因子联动（Position Sizing）

**场景**：
- 用户问"X 该买多少股 / 仓位多重 / 账户 10 万买 X 几股"
- wiki-research 步骤 4「个人建议」段需要给具体仓位时
- 决策型研究建议买入时，**必须**配合本模式输出仓位（不能只说"买"不说"买多少"）

**核心机制**（基于 Murphy 2% 铁律 + 因子 IC 强度）：

```
仓位 = (账户权益 × 风险%) × 因子权重 / 单股风险
```

- **风险%**：默认 2%（Murphy 铁律），高波动股（ATR/price > 4%）自动降到 1.6%
- **因子权重**（来自 Alpha 因子扫描 IC）：
  - |IC| ≥ 0.08 → 1.5x（强因子加仓 50%）
  - |IC| 0.05-0.08 → 1.2x（有效加仓 20%）
  - |IC| 0.03-0.05 → 1.0x（基础仓位）
  - |IC| < 0.03 → 0.7x（无信号减仓 30%）
- **封顶**：factor_weight 上限 1.5，下限 0.5（不能突破 2% 单笔风险上限）

**执行**：
```bash
cd /Users/shayin/data1/htdocs/project/mind/ai-wiki/wiki-engine/tools/quant-scanner

# 先跑 Alpha 因子扫描拿 IC（模式 4）
.venv/bin/python -m quant_scanner.scanner.cli factors eval --ticker NVDA --horizon 5
# → 假设 top alpha IC=0.08（强因子）

# 计算仓位
.venv/bin/python -c "
from quant_scanner.utils.position_sizing import (
    position_size, TradeSetup, ic_to_factor_weight, portfolio_risk_check, Position
)

# 1. 单股仓位（结合 IC + ATR）
setup = TradeSetup(entry_price=180, stop_loss=168, target_price=216, win_rate=0.5)
factor_weight, reason = ic_to_factor_weight(0.08)  # 强因子
shares = position_size(account_equity=100000, setup_or_risk=setup,
                      risk_budget_pct=0.02, atr=4.5, factor_weight=factor_weight)
print(f'建议买入: {int(shares)} 股 @{setup.entry_price} = \${int(shares)*180:,}')

# 2. 组合级风控（已建仓检查）
positions = [Position(ticker='NVDA', entry_price=180, stop_loss=168,
                     shares=int(shares), sector='tech')]
result = portfolio_risk_check(positions, account_equity=100000)
print(f'组合通过: {result.passed}, 总仓位: {result.total_exposure_pct*100:.1f}%')
"

# 或直接用 screen trend --plan（内置仓位计算）
.venv/bin/python -m quant_scanner.scanner.cli screen trend --plan --account 100000
```

**输出字段**：
- `shares`：建议买入股数
- `factor_weight`：因子权重（来自 `ic_to_factor_weight`）
- `weighted_risk_pct`：调整后风险%（基础 2% × factor_weight，封顶 3%）
- `portfolio_risk_check`：组合级检查（总仓位 ≤80%、总风险 ≤6%、行业 ≤40%）

**AI 处理**：
1. 必须先跑 Alpha 因子扫描（模式 4）拿到 top IC，再算仓位
2. 输出"账户 $X / 单股风险 $Y / 因子权重 Z / 建议买入 N 股"
3. **加仓必须满足 2:1 盈亏比**（reward_per_share ≥ 2 × risk_per_share）
4. 已建仓时跑 `portfolio_risk_check` 检查是否突破组合级约束

**输出格式**（给用户）：
```
NVDA 仓位建议（账户 $100,000）：

📊 单股仓位（IC=0.08 强因子 → ×1.5）：
- 入场价：$180
- 止损价：$168（单股风险 $12）
- 目标价：$216（3:1 盈亏比 ✓）
- 基础风险：$2,000（账户 2%）
- 因子加权后：$3,000（账户 3%，封顶）
- 建议买入：166 股 ≈ $29,880（账户 29.9%）

🛡 组合检查（假设 NVDA 是唯一持仓）：
- 总仓位：29.9% / 80% ✓
- 总风险：3.0% / 6% ✓
- 行业集中：科技 29.9% / 40% ✓
```

**禁止行为**：
- ❌ 不跑因子扫描直接给仓位（缺 factor_weight）
- ❌ factor_weight 突破 1.5 上限（违反 Murphy 单笔风险铁律）
- ❌ 单笔风险超账户 3%（即使强因子也封顶）

### 模式 7：横截面多标的因子（Cross-Sectional）

**场景**：
- 用户问"在这批候选股中，哪些股票相对强 / 相对弱"
- wiki-mine 挖出 ≥5 只候选股时（候选股池横截面打分）
- 单标的 IC（模式 4）只能看时序结构，无法回答"X vs Y 谁更优"

**核心差异**：
- 模式 4（单标的 IC）：每天 1 只股票，因子值 vs 前瞻收益的时序秩相关
- 模式 7（横截面 IC）：每天对 N 只股票做 rank corr，再取时间均值——衡量"因子能否区分不同股票的相对表现"

**执行**：
```bash
cd /Users/shayin/data1/htdocs/project/mind/ai-wiki/wiki-engine/tools/quant-scanner

# 至少 3 只标的（建议 ≥5 只，越多越可靠）
.venv/bin/python -m quant_scanner.scanner.cli factors cs-eval \
  --tickers NVDA,AAPL,MSFT,GOOGL,META,AMD,TSLA \
  --period 2y --horizon 5

# 自定义周期
.venv/bin/python -m quant_scanner.scanner.cli factors cs-eval \
  --tickers NVDA,AAPL,MSFT,GOOGL,META --horizon 20
```

**输出**：横截面 IC 表（按 |IC| 降序），含 IC/IR/N_days/强度。

**AI 处理**：
1. 看 top alpha：横截面 IC > 0.05 的因子能区分这批股票的相对表现
2. 配合模式 4（单标的）：同一 alpha 在单标的 IC 强但横截面 IC 弱 = 该 alpha 对这批股票不是"分化器"
3. 用于**选股排序**：横截面 IC 强的 alpha 可以作为多标的打分公式（top 排名 = 后续跑赢概率高）
4. 用于**配对交易**：横截面因子值极端（top vs bottom）可以做 long/short 对

**输出格式**（给用户）：
```
7 只科技股横截面因子扫描（2y 样本，5 日前瞻）：

🎯 横截面有效因子（|IC| > 0.05）：
- alpha_1（大盘因子）IC=-0.075 → 反向预测（因子高的票后续跑输）
- alpha_12（量价共振）IC=+0.073 → 正向因子，可作选股排序
- alpha_5/20/17 IC=+0.05~+0.07 → 中等强度

📊 选股应用：
- 按 alpha_12 排序当前 top 3：MSFT > AAPL > GOOGL（建议关注）
- 按 alpha_1 反向排序（低分优先）：TSLA > AMD > NVDA

⚠️ 样本量提醒：7 只标的 × 2y ≈ 1400 观察日，但横截面 N=7 偏小
建议扩到 ≥20 只同板块标的再下结论
```

**与单标的 IC（模式 4）的对比表**：

| 维度 | 模式 4 单标的 IC | 模式 7 横截面 IC |
|------|-----------------|----------------|
| 衡量 | 因子对该股票的时序预测力 | 因子能否区分多股票相对表现 |
| 样本 | 1 只 × N 天 | N 只 × N 天（每天 N 个观测） |
| 用途 | 单股决策（这股现在该不该买） | 选股排序（哪只票相对更优） |
| 互补 | 两者结合 = 完整的因子画像 | |

### 模式 8：Regime-conditional 因子（市场状态自适应）

**场景**：
- 用户问"X 在牛市/熊市/震荡市表现如何 / X 的因子是否依赖大盘"
- wiki-research 步骤 3 当单周期 IC 边界模糊（top IC=0.04）时，自动追加 regime 分析
- 决策型研究：当前 SPY 处于什么 regime → 该用哪个因子决策

**核心机制**：
- 用 SPY（或 QQQ）的 close vs MA200 + MA200 斜率分类 regime：
  - close > MA200 且 MA200 上行 → **bull**（牛市）
  - close < MA200 且 MA200 下行 → **bear**（熊市）
  - 其他 → **sideways**（震荡）
- 对每个 alpha 在 3 个 regime 下分别算 IC，找 **regime-adaptive 因子**：
  - **牛市专属**：bull IC ≥ 0.05，其他 < 0.03
  - **熊市专属**：bear IC ≥ 0.05，其他 < 0.03
  - **跨 regime 通用**：多个 regime 都 ≥ 0.05

**执行**：
```bash
cd /Users/shayin/data1/htdocs/project/mind/ai-wiki/wiki-engine/tools/quant-scanner

# 默认用 SPY 做 regime benchmark
.venv/bin/python -m quant_scanner.scanner.cli factors regime-eval --ticker NVDA --horizon 5

# 自定义周期
.venv/bin/python -m quant_scanner.scanner.cli factors regime-eval --ticker NVDA --period 3y --horizon 10
```

**输出**：表格含 Bull IC / Bear IC / Sideways IC / 自适应标签。

**AI 处理**：
1. 看当前 SPY 处于哪个 regime（bull/bear/sideways）
2. 选用 regime 专属因子做决策（当前 regime 有效的因子才信）
3. 如果某 alpha 是"跨 regime 通用"，置信度更高（不依赖大盘状态）
4. 如果是"牛市专属"但当前 SPY 在熊市 → 该 alpha 当前**无效**，技术面结论降级

**输出格式**（给用户）：
```
NVDA Regime 自适应分析（2y 样本，5d 前瞻）：

📊 当前 SPY 状态：牛市（bull=127d / bear=14d / sideways=110d）

🎯 牛市专属因子（bull IC ≥0.05，其他弱）：
- alpha_X：bull IC=+0.08，bear=-0.01，sideways=+0.02
  → 当前可用（SPY 处于牛市）
- alpha_Y：bull IC=-0.07，bear=+0.02 → 反向因子，当前看跌

🟢 跨 regime 通用因子（多 regime 都有效）：
- alpha_8：bull=+0.12，bear=-0.36，sideways=+0.01
  → 熊市更强（反向），震荡失效，慎用

⚠️ 当前无效因子（仅熊市/震荡专属）：
- alpha_5：bear=-0.49（强）但 bull=+0.01（弱）
  → 当前 SPY 牛市，该因子对 NVDA 暂不适用
```

**实操原则**：
- 技术面结论必须配合当前 regime：因子在不同 regime 下表现不同
- 单标的 IC（模式 4）是"全样本平均"，掩盖了 regime 差异
- Regime 分析揭示"什么时候用什么因子"

### 模式 9：SHAP 特征重要性（多因子联合贡献）

**场景**：
- 用户问"在 NVDA 上，哪几个 alpha 联合最有解释力 / alpha 之间交互效应如何"
- wiki-research 步骤 3 当 IC 边界模糊时（多个 alpha |IC|≈0.04），SHAP 揭示真正重要的
- 决策型研究：是否需要"组合 alpha"（多因子模型）做更准预测

**核心差异**：

| 维度 | IC（模式 4-8，单因子） | SHAP（模式 9，多因子） |
|------|----------------------|----------------------|
| 衡量 | 单 alpha vs 收益的秩相关 | 多 alpha 联合模型中各 alpha 边际贡献 |
| 模型 | 无（纯统计） | 树模型（GradientBoosting/XGBoost） |
| 交互 | 不捕捉 | 捕捉 alpha 间交互效应 |
| 输出 | IC ∈ [-1, 1] | mean abs SHAP（非负）+ 方向 |
| 泛化 | 不评估 | 给出 Test R² |

**执行**：
```bash
cd /Users/shayin/data1/htdocs/project/mind/ai-wiki/wiki-engine/tools/quant-scanner

# 默认用 sklearn GradientBoosting（无系统依赖）
.venv/bin/python -m quant_scanner.scanner.cli factors shap-eval --ticker NVDA --horizon 5

# 周期
.venv/bin/python -m quant_scanner.scanner.cli factors shap-eval --ticker NVDA --period 3y --horizon 10
```

**输出**：表格含 Alpha / mean |SHAP| / 方向（正/负/中性）/ 解读。附 Test R² 判断泛化能力。

**AI 处理**：
1. 看 Test R²：
   - R² > 0.15：模型泛化良好，SHAP 结论可信
   - R² 0.05-0.15：泛化偏弱，参考用
   - R² < 0.05：**alpha 组合无预测力，SHAP 不可靠**（强制告知用户）
2. 看 top 5 SHAP：高 mean |SHAP| = 在多因子模型中贡献大
3. 看方向：positive（正向）/ negative（反向）/ neutral（非线性）
4. 与单因子 IC 对比：
   - 单 IC 强 + SHAP 弱 = 该 alpha 与其他 alpha 信息冗余
   - 单 IC 弱 + SHAP 强 = 该 alpha 与其他 alpha 有交互效应（单看不出来）

**输出格式**（给用户）：
```
NVDA SHAP 特征重要性（2y 样本，5d 前瞻，223 观察日）：

📊 模型泛化：Test R²=-0.42 → 无泛化，alpha 组合对 NVDA 无预测力

🎯 Top 5 SHAP 贡献：
- alpha_18（绝对收益反转）：SHAP=0.0029（反向）
- alpha_2（-1 × delta((close-open)/delay(close,1))）：SHAP=0.0029（反向）
- alpha_17（量价动量）：SHAP=0.0023（正向）
- alpha_6（开收比反转）：SHAP=0.0023（反向）
- alpha_9（变化率）：SHAP=0.0022（正向）

⚠️ 关键判断：
R² 接近 0 意味着 20 个 alpha 联合也无法有效预测 NVDA 5 日收益。
这通常说明：① 当前 NVDA 走势无历史可类比结构；
② 或 5 日 horizon 太短，统计噪声大于信号。
建议：技术面结论降级，或改用更长 horizon（10d/20d）。
```

**何时启用 SHAP**：
- 单因子 IC 模式 4 给出矛盾结论时（top IC 方向不一致）
- 用户问"alpha 之间有没有交互效应"
- 决策型研究当 IC 边界模糊需要二次验证

### 模式 10：GP 因子挖掘（数据驱动挖新因子，方法 3 主菜）

**何时调用**：
- 用户问"能否挖一个针对 X 的新因子"
- Alpha101（方法 2）的 20 个现成公式都不好用，要找新的
- 研究需要定制化因子（如"针对 TSLA 的波动率反转结构"）

**与 SHAP 的衔接**：
- SHAP（模式 9）是「方法 3 入口」——评估已有 alpha 组合的边际贡献
- GP（模式 10）是「方法 3 主菜」——**挖出新因子**，再交回 SHAP 验证

**调用方式**：
```bash
cd wiki-engine/tools/quant-scanner
.venv/bin/python -m quant_scanner.scanner.cli factors gp-mine \
    --ticker NVDA --horizon 5
# 输出：最佳因子公式 + IC/IR + 进化历史
```

**核心机制**：
- 遗传规划（Koza 1992）在算子集（operators.py 的 50+ 算子）中搜索因子表达式
- 适应度 = |Spearman IC|（因子值 vs 前瞻收益）
- 选择/交叉/变异/精英保留 + 深度控制防膨胀
- 无外部依赖（不引 gplearn，~300 行紧凑自实现）

**输出**：
- `best_formula`：基因树序列化为公式字符串，如 `ts_decay_linear(ts_cov(ts_min(close, w=5), min_(volume, volume), w=20), w=5)`
- `best_ic` / `best_abs_ic` / `best_ir`
- `history`：每代最优个体（可看收敛轨迹）

**AI 处理**：
1. 看 |IC| 强度：
   - |IC| > 0.08：强信号，建议入库（加入 alpha101.py 或新 alpha 模块）
   - |IC| > 0.05：有效，值得进一步验证
   - |IC| < 0.03：未挖到可用因子
2. 看公式可读性：
   - 公式应能从金融逻辑上解释（不是纯数学噪声）
   - 例如 `sub(close, ts_mean(close, 20))` = 偏离均线（反转因子）
   - 不可解释的复杂公式 → 警惕过拟合
3. **必须用 SHAP 二次验证**：将 GP 挖出的因子加入 Alpha101 一起跑 shap-eval，确认其相对已有 alpha 的边际贡献 > 0.001（否则与已有 alpha 信息冗余）

**输出格式**（给用户）：
```
NVDA GP 因子挖掘（2y 样本，5d 前瞻，pop=50, gen=20）：

🧬 最佳因子公式：
ts_decay_linear(ts_cov(ts_min(close, w=5), min_(volume, volume), w=20), w=5)

📊 IC = +0.1210  |IC| = 0.1210  IR = +0.276  N=232
强信号因子（|IC|>0.08）

🔍 金融逻辑解读：
该公式可理解为「近 5 日最低价 × 量能波动率的 20 日加权协方差」——
量价协同的极端波动信号，符合「底部反转」叙事。

⚠️ 下一步：
建议用 shap-eval 验证此因子相对已有 20 个 alpha 的边际贡献。
如贡献 > 0.001，可入库为新 alpha_21。
```

**常见坑**：
- **小样本（N<100）IC 不可信**：跑出来的因子可能只是噪声拟合
- **种子敏感**：不同 random_state 可能挖出不同公式，跑 3-5 次取交集更稳健
- **过拟合风险**：GP 在金融数据上的过拟合是行业共识，必须用样本外验证（目前 mine() 用全样本 IC，未来需扩展为 train/test split）

### 模式 11：Harvey haircut / Deflated IC（多重检验校正）

**用途**：当 82 个 alpha 一起评估时，|IC| 最大的有**正向选择偏差**——你测了 82 次，最好那个本来就是概率上的极端值。Harvey haircut 把"N 次多重检验"考虑进去后，最大 |IC| 因子还显著吗？这是真信号 vs 伪信号候选的终极过滤器。

**学术依据**：
- Harvey, Liu, Zhu (2016) RFS *...and the Cross-Section of Expected Returns*
- Bailey, López de Prado (2014) *The Deflated Sharpe Ratio*（DIC 公式是 DSR 的 IC 版本）

**调用方式**：
```bash
cd wiki-engine/tools/quant-scanner
.venv/bin/python -m quant_scanner.scanner.cli factors deflate --ticker NVDA --horizon 5
# 输出：每个 alpha 的 IC/IR/E[max IR]/DIC p-value + haircut 摘要
```

**核心公式**：

```
E[max IR₀] = σ × [(1-γ)Φ⁻¹(1-1/N) + γΦ⁻¹(1-1/(N·e))]
DIC = (|IR| - E[max IR₀]) × √((T_eff-1) / (1 - skew·|IR| + (kurt/4)·|IR|²))
p_value = 1 - Φ(DIC)
```

其中 T_eff 是 Newey-West 调整后的有效独立样本数（滚动 IC 高度重叠，名义 T 严重高估）。

**输出字段**：
- `DIC 前 (|IC|>0.03)`：单测看似有效的因子数
- `DIC 后 (p<0.05)`：多重检验后仍显著的因子数（真信号）
- `Haircut rate`：被砍掉的比例（典型 60-90%）
- `top_survivors`：DIC 通过的因子
- `biggest_casualties`：IC 不错但 DIC 不过的伪信号候选

**AI 处理**：
1. 看 haircut rate：> 70% 说明大多数 IC 显著性是多重检验假象
2. 看 `top_survivors`：这些才是真正的 alpha，优先用于策略
3. 看 `biggest_casualties`：这些因子 IC 不错但不可信，警惕过度优化
4. **跨标的对比**：如果某 alpha 在多个标的都通过 DIC（如 alpha_72 在 NVDA 和 BABA 都过），可信度最高

**输出格式**（给用户）：
```
NVDA Harvey haircut 评估（82 alpha，5d 前瞻）：

📉 Haircut 摘要：
  DIC 前 |IC|>0.03：45 个
  DIC 后 p<0.05：5 个（alpha_72, alpha_94, alpha_30, alpha_61, alpha_29）
  Haircut rate：88.9%

🔍 关键发现：
  alpha_72 (IC=-0.1765, DIC p=0.005)：真信号，量价反转结构
  alpha_17 (IC=+0.0913, DIC p=0.098)：伪信号候选，多重检验后失去显著性
```

**与模式 4（IC eval）的关系**：
- 模式 4：单因子显著性检验（无多重检验校正）
- 模式 11（deflate）：多重检验校正后的真显著性
- **强制规则**：决策型研究必须同时报告模式 4 的 top IC 和模式 11 的 DIC 存活因子，否则有过度优化嫌疑

### 模式 12：Purged K-Fold CV（OOS 评估 + 多重检验双重过滤）

**用途**：模式 11 的进化版——Harvey haircut 只过滤多重检验偏差，但 IC 还是**全样本**（in-sample）。Purged K-Fold 把数据切成 K 折，每折在 OOS（样本外）数据上算 IC，得到真实的 OOS 信息比率 → 再叠加 Harvey haircut。**这是评估 alpha 真实预测力的金标准**。

**学术依据**：
- López de Prado (2018) AfML Ch 7 *Cross-Validation in Finance*
- 原始论文：López de Prado (2015) *The Probability of Backtest Overfitting*
- Dixon, Halperin, Bilokon (2016) *Purged K-Fold Cross Validation for Trading Rules*

**为什么金融数据不能用标准 K-Fold**：
- Triple-Barrier 标签：t 时刻标签依赖 [t, t+vb] 的价格 → 与 t+1, t+2... 的标签**重叠**
- 滚动 IC：forward returns 滑动窗口 → 训练集和测试集样本**标签共享**
- 结果：训练集"偷看"测试集信息 → IC 评估虚高 → 策略过拟合

**Purged K-Fold 解决方案**：
1. **Purge（清洗）**：训练集中移除所有标签窗口 ∩ 测试集 的样本
2. **Embargo（禁运）**：测试集之后再加 τ bars 缓冲（防自相关泄漏）

**调用方式**：
```bash
cd wiki-engine/tools/quant-scanner
.venv/bin/python -m quant_scanner.scanner.cli factors purged-cv --ticker NVDA --horizon 5
# 默认：purge=horizon bars, embargo=horizon/2 bars, 5 folds
```

**输出字段**：
- `OOS IC`：5 个 fold 的平均 OOS IC（每折用 test 集算 IC）
- `OOS IR`：OOS 信息比率 = mean / std（跨 fold）
- `E[max IR]`：N trials 零假设下期望最大 IR（同模式 11）
- `DIC p`：多重检验校正后的 OOS 显著性
- `OOS haircut rate`：OOS 仍被砍的比例（典型 > 90%，因为 OOS 比 IS 严格得多）
- `OOS |IR| 中位数`：所有 alpha 的 OOS IR 中位数（基准）

**AI 处理**：
1. 看 OOS |IR| 中位数：> 0.5 说明该股票普遍有 alpha 结构；< 0.2 说明整体偏噪声
2. 看 OOS haircut rate：> 95% 是正常的（多重检验 + OOS 双重严格）；不要因此否定工具
3. **存活因子（DIC 通过）**：这些是真正可用的 alpha，可信度比模式 11 的存活更高
4. **重大伤亡**：OOS IC 大但 DIC 不过 → 仍有过拟合风险，谨慎使用
5. **跨标的对比**：某 alpha 在多标的都通过 OOS DIC → 最高可信度

**与模式 11 的对比**：

| 维度 | 模式 11（deflate） | 模式 12（purged-cv） |
|------|------------------|--------------------|
| IC 来源 | 全样本（in-sample） | 5 折 OOS 平均 |
| 偏差类型 | 仅多重检验 | 多重检验 + 过拟合 |
| 严格度 | 中 | **高（金标准）** |
| 适合场景 | 快速筛选 | 最终决策前严格验证 |

**强制规则**：
- 决策型研究（高 stakes 买卖）**必须**用模式 12 验证模式 11 的存活因子是否在 OOS 下仍成立
- 模式 11 通过但模式 12 不过的 alpha：**降级使用**（小仓位、仅参考）
- 模式 12 通过的 alpha：**高可信度**，可放心用于模式 6 仓位管理

**输出格式**（给用户）：
```
NVDA Purged K-Fold OOS 评估（82 alpha，horizon=5d，5 folds）：

📊 OOS 摘要：
  OOS 有效 (|IC|>0.03)：42 个
  DIC 通过 (p<0.05)：3 个（alpha_42, alpha_4, alpha_7）
  OOS haircut rate：92.9%
  OOS |IR| 中位数：0.392

🔍 关键发现：
  alpha_42 (OOS IC=+0.096, OOS IR=+2.10, DIC p=0.04)：双重验证通过的真信号
  alpha_72 (OOS IC=+0.108 但 DIC p=0.15)：IS 强但 OOS 衰减，降级使用
```

**与 AfML 完整流水线的关系**：
- Triple-Barrier（`labels tb`）→ Meta-Labeling（`labels meta`）→ Purged K-Fold（`factors purged-cv`）
- 这三件套构成 López de Prado 推荐的 ML 金融流水线：标签生成 → 二级分类 → 严格 OOS 评估

### 模式 13：Secondary Model 闭环（AfML Ch 4 meta-labeling 真正闭环）

**用途**：模式 11/12 只告诉你"哪个 alpha 是真信号"，但不告诉你"具体下注多少"。Secondary Model 让 AfML 流水线**真正闭环**——primary 给方向、secondary 给信心、最终产出**可执行的仓位大小**。

**核心思想**（López de Prado AfML Ch 4）：
- 传统单一模型既预测方向又预测仓位，容易过拟合
- Meta-Labeling 拆开：Primary 拍方向（side ∈ {+1,-1}）、Secondary 拍信心（P ∈ [0,1]）
- 最终仓位 = side × confidence × max_position（从二值决策 → 连续决策）

**调用方式**：
```bash
cd wiki-engine/tools/quant-scanner

# 端到端（70/30 时序切分，输出 OOS lift）
.venv/bin/python -m quant_scanner.scanner.cli ml secondary --ticker NVDA \
  --primary-alpha alpha_42 --top-features 10 --model gbm

# Purged K-Fold 严格评估（5 folds，输出每折 lift）
.venv/bin/python -m quant_scanner.scanner.cli ml cv --ticker NVDA \
  --primary-alpha alpha_42 --top-features 10 --model gbm --n-splits 5
```

**参数**：
- `--primary-alpha`：方向源（默认 alpha_42，可用 `factors eval` 查 top IC）
- `--top-features N`：按 |IC| 选 top N 个 feature（防过拟合，默认 10）
- `--model lr|gbm`：secondary 分类器（lr=LogisticRegression / gbm=GradientBoosting，默认 gbm）
- `--tp/sl/vb`：Triple-Barrier 屏障参数（默认 2×ATR / 2×ATR / 10bars）

**输出字段**：
- `Primary baseline 命中率`：primary 单独的 OOS 精度（不看 secondary）
- `Secondary 过滤后命中率`：高 confidence 子集的精度
- `Lift` = Secondary - Primary（正值 = secondary 有效）
- `过滤率`：被 secondary 砍掉的低 confidence 样本比例
- 每折的 `Lift`：CV 模式下看跨 fold 稳定性

**典型结果**（NVDA + alpha_42 + GBM + top-10, 2y 样本）：

| Fold | Primary P | Secondary P | Lift | Filter% |
|------|-----------|-------------|------|---------|
| 0 | 57.1% | 58.7% | +1.5pp | 23% |
| 1 | 52.0% | 61.9% | +9.9pp | 36% |
| 2 | 79.6% | 83.0% | +3.4pp | 46% |
| 3 | 45.9% | 51.9% | +6.0pp | 47% |
| 4 | 53.1% | 54.1% | +1.0pp | 38% |
| **平均** | **57.6%** | **61.9%** | **+4.37pp** | **38%** |

**学术对标**：López de Prado 原书典型 lift +3-7pp，我们 NVDA +4.37pp 完全达标。

**AI 处理**：
1. **先跑 `factors purged-cv`（模式 12）** 选出 OOS 通过 DIC 的 top alpha 作 primary
2. **跑 `ml cv`** 看 secondary 在该 primary 上是否有 lift（mean_lift > 0 = 闭环有效）
3. **跨 fold 看稳定性**：所有 fold 同向（全正/全负）= 稳定信号；忽正忽负 = 过拟合或数据不足
4. **lift 接近 0 时降级**：
   - 5 fold 全 ≈ 0（如 NVDA +0.0pp × 4 + 单 fold +9.4pp）= LR 退化，换 GBM
   - 5 fold 全负 = primary 无信号（与 deflated_ic 结论交叉验证）
5. **过滤率太低（<10%）说明模型退化**（总是预测 P>0.5）→ 减少 features 或换模型

**与模式 6（仓位管理）的关系**：
- 模式 6 给的是固定公式仓位（account × risk% × factor_weight / 单股风险）
- 模式 13 给的是 ML 学到的 confidence（动态、自适应）
- **强制规则**：决策型研究建议「模式 6 + 模式 13 联合」：
  - 模式 6 给出 base 仓位（基于 Murphy 2% + factor_weight）
  - 模式 13 给出 confidence（≥0.7 重仓、0.5-0.7 标准、<0.5 减仓或不押）
  - 最终仓位 = base × confidence（ML 信心 × 风控规则双重过滤）

**强制规则**：
- **闭环验证**：如果研究用模式 11/12 推荐了某 alpha 做 primary，**必须**跑模式 13 的 `ml cv` 验证 secondary 是否真有 lift（>0 = 闭环可信；≤0 = 该 alpha 在 ML 下不能产生超额收益，降级使用）
- **不能凭 IC 推荐 primary**：IC 高 ≠ ML 闭环有效；必须用模式 13 的 CV lift 实证

**与 GP 挖掘（模式 10）的衔接**：
- GP 挖出的新因子 → 先跑 `factors purged-cv`（模式 12）验证 OOS 显著性
- 通过后 → 作为 primary 跑 `ml cv`（模式 13）验证作为方向源的 ML 价值
- 双重通过 → 入库 alpha101.py 作为正式 alpha

**输出格式**（给用户）：
```
NVDA AfML 闭环验证（primary=alpha_42, GBM+top10, 5-fold Purged CV）：

📊 Secondary model 效果：
  Primary 单独命中率：57.6%
  Secondary 过滤后：61.9%
  Lift：+4.37pp（论文典型 +3-7pp，达标）
  过滤率：38%（38% 低信心信号被砍掉）

🔍 关键发现：
  - 5 fold 全部正 lift（+1.0 到 +9.9pp），信号稳定
  - Fold 1 单 fold +9.9pp（最强），Fold 4 +1.0pp（最弱）
  - Top features: alpha_8, alpha_10, alpha_72, alpha_94...（与 deflated_ic 存活因子部分重合）

💡 仓位建议：
  - 当前 alpha_42 信号 → side = +1（做多）
  - Secondary confidence = 0.78（当前时点）
  - 建议仓位 = 模式 6 base 仓位 × 0.78（缩仓 22%）
```

## 微信端兼容规则

**关键**：cf 微信端看不到 HTML，所有输出必须转 markdown。

1. **禁用**：不要回 "HTML 报告生成于 /path/x.html" 然后让用户自己看
2. **强制**：跑完 stats 后立即调用 `parse-html.py` 转 markdown，把摘要回给用户
3. **HTML 文件**：仍生成在 `mind/tmp/` 留底（用户在 PC 端会话可看），但**不依赖**用户打开

## 与 wiki-research 的集成

wiki-research 步骤 3「反面论据检查」段的「看多/看空信号对比表」中：

| 维度 | 看多信号 | 看空信号 | 净方向 |
|------|---------|---------|--------|
| 基本面 | ... | ... | |
| **技术面** | **调用 wiki-quant 模式 1**，列出 score ≥ 0.5 的做多信号 | **列出 score ≤ -0.5 的做空信号** | **由工具计算，不靠 LLM 主观** |
| 情绪/资金面 | ... | ... | |
| 估值 | ... | ... | |

**强制规则**：
- 决策型研究（涉及买卖建议）：**必须**调用 wiki-quant 模式 1
- 知识型研究（纯方法论）：可选
- 输出写入 `materials/technicals.md` 作为审计材料
- 如果 quant-scanner 工具不可用（如标的非美股、数据缺失），明确告知用户"技术面数据缺失"，不要用搜索结果代替

## 输出落盘规则

| 模式 | 落盘位置 | 用途 |
|------|---------|------|
| 快速扫描 | `analysis/{课题}/materials/technicals-scan-{YYYYMMDD}.md` | 研究审计材料 |
| 胜率验证 | `analysis/{课题}/materials/technicals-stats-{YYYYMMDD}.md` | 研究审计材料 + 长期复用 |
| 批量选股 | `analysis/{课题}/materials/batch-scan-{YYYYMMDD}.md` | 选股决策依据 |
| Alpha 因子 | `analysis/{课题}/materials/alpha-factors-{YYYYMMDD}.md` | 统计套利视角审计材料 |
| 出场策略 | `analysis/{课题}/materials/exit-strategy-{YYYYMMDD}.md` | 持仓监控 + 止损止盈价位 |
| 仓位管理 | `analysis/{课题}/materials/position-sizing-{YYYYMMDD}.md` | 具体买入股数 + 组合风控 |
| 横截面因子 | `analysis/{课题}/materials/cs-factors-{YYYYMMDD}.md` | 多标的选股排序 |
| Regime 因子 | `analysis/{课题}/materials/regime-factors-{YYYYMMDD}.md` | 当前 SPY 状态适用的因子 |
| SHAP 特征 | `analysis/{课题}/materials/shap-factors-{YYYYMMDD}.md` | 多因子联合贡献 + 泛化能力 |

落盘后，在主报告 `report.md` 的「## 个人建议」节加一行引用：
```
> 技术面硬数据：详见 [technicals-{YYYYMMDD}](materials/...)；当前触发 N 个强信号，净方向 多。
> Alpha 因子扫描：详见 [alpha-factors-{YYYYMMDD}](materials/...)；top alpha IC=±0.XX，与 Signal 方向一致/冲突。
```

## 16 个信号速查（中英文对照）

| 英文 | 中文 | 类别 |
|------|------|------|
| trend_template | 趋势模板 | 持续 |
| vcp | VCP 波动率收缩 | 持续 |
| pivot_point | 中枢点 | 持续 |
| base_counting / sell_signals | 基底计数 | 卖出 |
| cup_handle | 杯柄形态 | 反转 |
| market_direction | 市场方向 | 大盘 |
| trend_regime | 趋势制度 | 持续 |
| major_reversal | 重大反转（双顶/底/头肩/三重/圆弧/V型/岛形） | 反转 |
| continuation | 持续形态（旗形/三角/楔形/矩形） | 持续 |
| oscillator_timing | 振荡器（RSI/MACD 经典+隐藏背离+失败摆动+极值） | 择时 |
| rs_rating | RS 相对强度评级 | 选股 |
| can_slim | CAN SLIM 七字母 | 综合（需外部数据） |
| new_high_supply | 新高 + 供需 | 综合（需外部数据） |
| dow_phases | 道氏三阶段 | 大盘 |
| support_resistance | 支撑/阻力角色互换 | 反转 |
| exit_strategy | 出场策略（ATR + 移动止损 + 时间止损） | 出场 |

## 默认参数（不传时）

- `--period 2y`：回溯 2 年（平衡样本量 + 数据时效）
- `--horizon 20`：前瞻 20 个交易日（约 1 个月，中短线）
- `--price-only`：只用纯价格信号（跳过 can_slim/rs_rating/new_high_supply，避免外部 API 慢）
- `--holdout-months 6`：6 个月 holdout（2y 数据下切 18m 训练 + 6m 验证）

## 输出质量门槛

1. **必须解释术语**：首次出现 EV/Wilson/PF/MAE/MFE 时加简短中文解释（参考 wiki-engine CLAUDE.md 术语规则）
2. **样本量警告**：灰色级（<20）结论必须标注"仅供参考"
3. **过拟合警示**：训练段 vs holdout 段方向反转时，必须显式提醒
4. **不要自作主张**：工具说什么写什么，LLM 不发明数字
5. **中英对照**：表格同时保留英文原名（代码追溯用）

## 不适用场景

- 非美股（quant-scanner 数据源是 yfinance + SEC EDGAR，仅支持美股）
- 已退市/停牌标的
- 上市不足 1 年的新股（数据不足以跑统计）
- 加密货币、外汇、商品（不在 quant-scanner 范围）

## 故障兜底

- quant-scanner 工具不可用（vEnv 损坏/路径变更）：明确告知"技术面工具暂不可用"，回退到 wiki-research 原有流程（LLM 凭搜索）
- 单标的 API 失败：跳过该标的，继续其他
- HTML 解析失败：返回原始 HTML 路径 + 主要内容截图

## 与其他 skill 的关系

| skill | 关系 |
|-------|------|
| wiki-research | 被调用方（步骤 3 技术面栏强制调用） |
| wiki-mine | 调用方（候选股批量打分） |
| wiki-lens | 互补（lens 看方法论跨界，quant 看形态硬数据） |
| wiki-sweep | 可被调用（扫描时检查形态触发） |
