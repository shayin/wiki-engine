# 议题: quant-scanner 回测胜率统计迭代要做什么

**日期**: 2026-07-18
**参与方**: Pilot (cc) × Copilot (codex)
**讨论方式**: 单窗口主驾-副驾模式
**仓库**: `/Users/shayin/data1/htdocs/project/mind/ai-tools/quant-scanner/`

## 状态
已达成 ✅（R10 无异议定稿）

---

## 议题背景（Pilot R1，盲测模式，仅描述现状与开放问题，不抛倾向）

### 项目现状
quant-scanner 是美股量化扫描器，已完成 P0-P5 迭代（203 测试全过）：
- 16 个信号（trend_template / vcp / pivot_point / sell_signals / cup_handle / market_direction / trend_regime / major_reversal / continuation / oscillator_timing / rs_rating / can_slim / new_high_supply / dow_phases / support_resistance + backtest engine）
- **形态扩展已完成**：major_reversal 现有 10 种形态（DOUBLE/TOP_BOTTOM、HEAD_SHOULDERS_TOP/BOTTOM、TRIPLE_TOP/BOTTOM、ROUNDING_TOP/BOTTOM、V_TOP/BOTTOM、ISLAND_TOP/BOTTOM）；continuation 现有 6 种（BULL/BEAR_FLAG、ASCENDING/DESCENDING/SYMMETRIC_TRIANGLE、RISING/FALLING_WEDGE、RECTANGLE）；oscillator_timing 现有 5 类（RSI 经典背离、MACD 经典背离、隐藏背离、失败摆动、超买超卖）
- 并发扫描（Scanner.scan / DataLoader.load_batch ThreadPool）
- HTML 报告（CANSLIM 字母条 + 形态徽章 + 综合分柱状图）

### 现有 backtest 模块（src/quant_scanner/backtest/engine.py）
事件驱动 + point-in-time + 滑点佣金。关键数据结构：

```python
@dataclass
class TradeRecord:
    ticker: str
    entry_date: pd.Timestamp
    entry_price: float  # 含滑点
    shares: float
    exit_date: pd.Timestamp | None
    exit_price: float | None  # 含滑点
    return_pct: float | None  # 含 commission + slippage 净收益
    exit_reason: str  # signal_exit / stop_loss / time_limit / end_of_data
```

`BacktestReport` 已有：n_trades / closed_trades / win_rate / avg_return / total_return / max_drawdown / sharpe / profit_factor / summary_dict / per_ticker_stats。

冒烟测试：72 笔交易，胜率 52.8%，平均 +5.86%，盈亏比 2.37。

### 关键 gap（事实陈述）
1. **TradeRecord 没有记录"开仓瞬间的信号 snapshot"**。`_composite_score()` 只算综合分，丢弃了各 signal 的 value / passed / details（pattern_type / phase / state 等都丢了）。
2. **退出也只看综合分 < exit_threshold**，不区分是哪个 signal 触发退出（exit_reason 字段固定 4 种枚举值，没和具体 signal 绑定）。
3. **统计只有"全局"和"per_ticker"两个维度**，没有 per_signal / per_pattern_type / per_exit_reason 切片。
4. **没有过拟合检测机制**：样本量小、形态分布不均时（如岛形反转可能 1 年只触发 3-5 次），单形态胜率没有置信区间。

### 4 个开放问题（讨论重点，**不预设答案**）

1. **要做什么**：哪些胜率统计指标最有价值？胜率/盈亏比/期望值/平均持仓/最大单笔亏损/夏普/Calmar？统计口径是按"形态触发"还是"按形态开仓"？怎么避免过拟合（数据窥探、样本量不足、形态触发频率极低时的统计噪音）？

2. **怎么做**：扩展 backtest engine 在 TradeRecord 上加 signal snapshot 字段，还是独立写个 stats 模块从 BacktestReport.trades 反推？样本量要求（最少多少笔才给统计、低于阈值标"低置信度"）？PIT 合规要点（开仓瞬间的 signal snapshot 必须严格按 pit_date 截断，避免未来函数）？

3. **形态 vs 信号切片粒度**：major_reversal 一个 signal 里有 10 种 pattern_type，continuation 有 6 种，oscillator_timing 有 5 类。统计时是按 signal 维度切（major_reversal 整体胜率）还是按 pattern_type 维度切（DOUBLE_TOP 单独胜率 vs HEAD_SHOULDERS_TOP 单独胜率）？还是两层都要？跨 signal 比较（major_reversal vs continuation 谁更准）和 signal 内比较（同一 signal 内哪种 pattern_type 最强）哪个更优先？

4. **从"能用"到"好用"**：什么交互/可视化让用户真的会用？CLI 子命令（`quant-scanner stats --by signal --period 2y`）？HTML 报告加形态胜率表格 + 柱状图？最低样本量阈值标灰？置信区间显示？要不要做"形态 vs 市场环境"交叉（牛市/熊市/震荡的形态胜率差异）？

### 已知约束
- 测试驱动的开发习惯（项目内每加一个功能必带测试）
- 项目内已有 reporter/html.py（Apple 风模板 + Jinja2）和 backtest/reporter.py（matplotlib SVG 权益曲线）可复用
- 不要引入 plotly 等重依赖（matplotlib + Jinja2 已够）
- 现有 203 测试不能破

OK @ 2026-07-18 21:28 Pilot R1

## Round 2 - Copilot

### 1. 先区分"信号有效性"与"策略成交归因"

我不建议仅在 `TradeRecord` 加 snapshot 后就直接宣称"某 signal 胜率"。现有开仓是综合分超过阈值且通过 `max_positions` 排序后的结果；同一笔成交通常同时带有多个信号。把这笔净收益分别记给每个当时为正的 signal，只能得到"该 signal 出现在被组合策略选中交易中的条件表现"，不是该 signal 的独立命中率，更不能据此直接调权。

应同时产出两张事实表：

- `TradeRecord.entry_snapshot`：用于策略归因。记录开仓时每个 signal 的 `value`、`passed`、有限白名单后的 `details`（如 `pattern_type`、`confirmed`、`state`）及 `composite_score`；平仓也记录 `exit_snapshot` 或至少记录当日各 signal 值，才能解释 `signal_exit`。
- 独立的 `SignalEvent`：每次调仓日、每只股票、每个满足定义的 signal/形态触发都记录，即使最终未成交（被综合分、仓位上限或已有持仓淘汰）。事件在 t 日按 PIT 快照生成，随后用预先固定的观察窗口计算前瞻标签，例如 5/20/60 日收益、最大不利波动、是否先达目标或止损。

前者回答"组合策略的已成交单，哪些上下文表现好"；后者才回答"这个形态本身出现后，历史上通常怎样"。两者都需要，但第一期应先完成 `TradeRecord` 快照和 `SignalEvent` 基础表；从 `BacktestReport.trades` 事后反推不可行，原始 `SignalResult.details` 已在 `_composite_score()` 中丢失。

### 2. 统计口径应先固定，避免把胜率做成误导性 KPI

每个切片的主指标应是：样本数、胜率、平均净收益、收益中位数、期望值、平均盈/亏、profit factor、平均持仓天数，以及 Wilson 95% 胜率区间。胜率只能作为展示指标；低胜率但高期望值的趋势跟随形态不应被误判为无效。

`per_signal` 与 `per_pattern_type` 两层都应有，但层级关系必须明确：

- 顶层按 signal：回答 major_reversal、continuation 等模块在组合中的条件表现。
- 第二层按 `signal_name + pattern_type + direction`：回答 `DOUBLE_BOTTOM`、`BULL_FLAG` 等具体触发器。方向不可省略；当前 `major_reversal`、`continuation`、`oscillator_timing` 都可能给出负值，不能把空头形态和多头开仓交易混在同一"胜率"里。
- `pattern_type is null` 不应删除，应归为"非形态/未确认"，否则只统计已确认形态会抬高结果。

低样本不要用一个武断阈值把数据隐藏。建议 `< 30` 标"探索性"，30–99 标"低置信度"，`>=100` 才进入默认排序；表中始终显示 n 与 Wilson 区间。形态事件还需定义去重规则，例如同 ticker、同 pattern_type 在持仓期或冷却窗口内只算一次，否则一个持续数周的形态会被重复计数，虚增样本和置信度。

### 3. PIT 不只意味着 df 截断，验证流程也必须隔离

当前引擎虽以 `df.loc[df.index <= current_date]` 截断价格数据，但 `_composite_score()` 未显式传 `pit_date`；`CANSLIM`、`RSRating` 等已经提供 PIT 参数时，应由统一评估入口明确传入，不能依赖其启发式判断。snapshot 必须在调用 signal 后立即冻结为纯 Python 标量/JSON 安全结构，禁止保留可变 `details` 引用或未来重新计算。

更大的风险是研究流程的数据窥探：不能在同一段 2020–2025 数据上看完所有 pattern 切片，再据此挑形态、调阈值并仍报告原始胜率。最低可行防线是按时间做 train / validation / final holdout，所有阈值、冷却期、最低样本门槛和展示排序只在训练段决定，最终只报告未参与选择的 holdout；若要持续迭代，则改为 walk-forward。另需在报告元数据中锁定 universe、数据版本、参数、信号版本与回测区间，否则不同批次的"同一形态胜率"不可复现。

HTML/CLI 可以随后直接消费聚合后的 DataFrame：默认显示 signal 总表、可展开 pattern 二级表、灰显低置信度并展示区间；"市场环境交叉"应留到基础事件样本足够后再做，否则会把已稀疏的 pattern 样本继续切碎。

OK @ 2026-07-18 21:30 Copilot R2

## Round 3 - Pilot

R2 切中了我 R1 没说透的核心问题：**"策略成交归因"与"信号有效性"是两个不同问题**，现有 `BacktestReport.trades` 只能回答前者（还是带噪声的）。我接受这个二分。下面亮出我的倾向并对 R2 做几处补充/质疑。

### 1. 二分共识接受，但范围要收窄

R2 提议第一期同时产出 `TradeRecord.entry_snapshot/exit_snapshot` 和独立 `SignalEvent` 两张表。我同意两者都需要，但**第一期只做 SignalEvent，不做 entry_snapshot 扩展**。理由：

- `entry_snapshot` 回答的"组合策略归因"问题，在当前综合分加权策略下几乎无干净答案——正如 R2 指出，同一笔成交带多个 signal，无法归因。即便记下 snapshot，也只能做"描述性切片"，无法做"因果归因"。投入产出比低。
- `SignalEvent` 才是真正回答用户痛点（"哪个形态历史上准"）的表。先把这张表做对，再决定要不要回去补 entry_snapshot。
- 范围收窄能降低第一期复杂度：只新增 stats 模块 + SignalEvent 采集器，不动 `TradeRecord` / engine 闭环。

### 2. SignalEvent 的采集边界——R2 说"每次调仓日都采集"，我要收紧

R2 提议"每次调仓日、每只股票、每个满足定义的 signal/形态触发都记录"。这对全 SP500 × 16 信号 × 每日 × 5 年会是**天文级数据量**（粗算 500 × 16 × 1260 ≈ 1000 万事件），且大部分 signal 每日都触发（trend_template 几乎所有 trending 股票每天都 pass）。

**我提议改为"事件触发型采集"**：
- 只记录**状态翻转**或**新增形态确认**：例如 RSI 经典背离昨日未确认今日确认、双底颈线突破当日、VCP 收缩阶段从 2 → 1 当日。
- 对持续型信号（trend_template 的 pass 状态、trend_regime 的 state）只在**状态切换日**记录一次。
- 这样样本量降 ~95%，且每条事件都有"可观察、可回测、可去重"的语义边界。

需要为每个 signal 定义"什么是一次事件"（去重窗口 + 触发条件）。这是**第二期**的工作——第一期先用最简单的规则：同 ticker × 同 signal × 同 pattern_type，在持仓窗口或 20 个交易日内只算一次。

### 3. 统计口径——R2 的指标清单接受，但"期望值"和"胜率"哪个主排名要明确

R2 列了 9 个指标 + Wilson 区间。同意全部纳入，但**默认排序键**我主张用"期望值（EV = 平均净收益）"，胜率仅作展示。原因：

- 趋势形态（VCP、杯柄）胜率常在 35-45%，但 EV 显著为正（赚的笔大）；反转形态（双底）胜率可能 55%+ 但 EV 接近 0。
- 默认按胜率排序会让用户误判"双底 > VCP"，这正是 R2 警告的"低胜率高 EV 不应被误判"。
- 默认排序：EV 降序；次级展示胜率列 + Wilson 区间。

### 4. 低样本分层——R2 的三档接受，阈值再讨论

R2 三档（<30 探索 / 30-99 低置信 / ≥100 默认）。我接受分层，但阈值放到**用户可配置**（CLI `--min-sample`），默认值我倾向 `<20` 标灰、`20-49` 探索、`50-199` 低置信、`≥200` 默认。理由：

- 100 笔门槛对美股 5 年回测 + 单形态太严苛（岛形可能 5 年才 30 次）。
- Wilson 区间本身已经表达不确定性，分层只是展示层灰度，不需要太严。
- 配合去重窗口，单形态有效样本应该比"原始触发次数"低得多——阈值不能拍脑袋。

### 5. PIT 隔离——R2 的 train/holdout 切分接受，但 walk-forward 第一期不做

R2 提议 train/validation/final holdout，或 walk-forward。我接受"最终 holdout"作为防过拟合底线：

- 第一期：回测区间强制切出最后 12 个月作为 **final holdout**（不参与任何阈值/排序决策，只在最终报告中独立展示）。
- 阈值/排序决策只允许在 train 段做。
- walk-forward（滚动重训）是**第二期**——它需要"参数化阈值"才能滚动，而第一期我们还没产出可调参数。

### 6. 对 R2 的两处质疑

**6.1 "pattern_type is null 不应删除"**：R2 这点正确，但落地要细化——`trend_template`、`vcp`、`pivot_point` 等没有 pattern_type 的 signal，其"形态维度"本来就是 signal_name 本身。建议分两类处理：
- 形态类 signal（major_reversal / continuation / oscillator_timing）：pattern_type 为空时归"未确认"桶。
- 非形态类 signal（trend_template / can_slim 等）：直接按 signal_name 切，不走 pattern_type 二级。

**6.2 "direction 不可省略"**：R2 说得对。但现有部分 signal（如 oscillator_timing 的"超买超卖"）方向是**后续价格走势决定**而非 signal 本身给出——这类 signal 在 t 日无法定义 direction。建议：
- direction 字段优先从 signal 的 value 符号 + details 推导；
- 推不出来时记 `direction=unknown`，统计时单独成桶，不混入多/空。

### 7. 第一期交付清单（我的提议）

1. 新增 `stats/events.py`：`SignalEvent` dataclass + `SignalEventCollector`（在 engine 调仓日按翻转/确认规则采集）
2. 新增 `stats/analyzer.py`：事件前瞻标签（5/20/60 日 forward return / MAE / hit stop / hit target）+ Wilson 区间 + 分层
3. 新增 `stats/reporter.py`：HTML 形态胜率表（顶层 signal 总表 + 二级 pattern_type 表）+ CLI `stats` 子命令
4. 新增 `tests/test_stats_*.py`：合成数据测事件去重、前瞻标签、Wilson、分层、PIT 合规
5. **不动** `TradeRecord` / `BacktestReport` / engine 闭环（entry_snapshot 留给第二期）
6. final holdout 切分（最后 12 个月独立报告）

### 8. 留给 R4 的两个开放点

- **事件采集 vs 全量快照**：我主张事件型采集（省 95% 数据 + 语义清晰），但担心遗漏"持续型 signal 的持续期收益"。R2 的"每次调仓日都采集"更完整但数据爆炸。有无折中方案？
- **前瞻标签的窗口选择**：5/20/60 日是否合适？美股短线（Minervini）持仓常 5-20 天，中线（O'Neil）20-60 天。要不要按 signal 类型给不同窗口（短线信号看 5/10/20，中线看 20/60/120）？

OK @ 2026-07-18 21:35 Pilot R3

## Round 4 - Copilot

我会核对 Pilot 的 R3 与现有回测入口的实际边界，重点检验第一期范围是否能避免"看似归因、实则误归因"。
我接受"第一期以 SignalEvent 为主"的收敛，但不同意把它做成完全独立于 engine 的旁路模块。`SignalEventCollector` 若自行重放信号，很容易与实际回测产生两套评估语义；而当前 `_composite_score()` 也未显式传 `pit_date`。更稳妥的最小改动是将其改为一次性返回 `score + {signal_name: SignalResult}`，engine 在既有调仓日评估循环中把结果交给可选 collector。这样不改 `TradeRecord`，却保证 stats 与策略使用同一份 PIT 信号结果，并让测试可以断言"同日事件快照值等于策略评分输入"。

### 1. 事件型采集可行，但应是"触发事件 + 固定检查点"的混合方案

Pilot 对全量逐日保存的体量顾虑成立，特别是保存完整 `details` 时；但纯状态翻转会系统性遗漏持续型信号的有效期，也无法回答"trend_template 连续通过第 1、20、60 个交易日的条件表现是否不同"。

建议第一期采用：

- 形态/离散信号：确认日、新 `pattern_type`、方向翻转时生成事件；20 个交易日冷却仅用于同 ticker × signal × pattern_type × direction 的重复事件。
- 持续型状态信号：状态切换日生成事件，另在状态持续第 20、60 个交易日生成 checkpoint；不在每个调仓日重复记录。
- 每条记录只保存标准化字段与白名单详情，不保存原始 `patterns` 列表、reasons 或 DataFrame；这既控制数据量，也避免 schema 随 signal 内部细节漂移。
- 记录 `event_kind=transition|confirmation|checkpoint`。分析默认只展示 transition/confirmation，checkpoint 是用于检验持续期与衰减的辅助切片，绝不混入同一胜率分母。

因此"每次调仓日全量采集"不应作为默认，但也不能仅靠翻转事件。将调仓频率写入事件元数据，且以 trading-day 计数冷却/窗口，避免周频和日频结果混淆。

### 2. 前瞻标签最关键的是可交易时点，而非先争论 5/20/60

现有 engine 在 `current_date` 截断数据、计算当日收盘信号、又以同日 `close` 开仓。这对收盘后才能确认的形态存在执行时点偏乐观的问题。新的 SignalEvent 若用"t 日 close 到 t+20 close"计算收益，会把这个偏差固化成"形态胜率"。

事件的第一期合同应明确为：

- 信号在 t 日收盘后确认；
- 假设在下一可交易日 `open` 成交，并应用同样的滑点与双边佣金；
- 5/20/60 指从实际 entry session 起算的交易日；
- 方向取 event 生成时的 `SignalResult.value` 符号；`unknown` 只能作为诊断桶，不进入多空命中率主表；
- horizon 不足的尾部事件标为 right-censored，不能当失败或零收益计入分母。

我倾向所有 signal 统一报告 5/20/60，而不在第一期按 signal 类型选不同窗口。后者会引入额外自由度：先看结果再决定"某类信号该看哪个窗口"，本身就是数据窥探。可在展示层按信号类别突出一个预先登记的主窗口，但原始三窗口始终并列。

另外，`hit_target / hit_stop` 不宜作为第一期核心标签：它要求明确每类 pattern 的 target/stop 定义，并处理同一根 K 线同时触及目标与止损时的路径不可观测问题。第一期先以净 forward return、MAE/MFE 为主；仅对原本就提供 `target` 且方向明确的形态，后续再增加带清晰 OHLC 成交假设的 target/stop 分析。

### 3. 12 个月 holdout 是最低线，但不宜"强制"到所有运行

最终 12 个月独立展示的方向正确，但"强制"会使短回测或短生命周期策略直接失去可用样本。应设为默认的 `--holdout-months 12`，并校验训练段、holdout 段各自满足最小交易日数；不满足时显式拒绝生成"已验证"结论，只输出探索性全样本报告并标注无独立验证。

还应把 holdout 放在事件生成后、聚合前切分：同一事件只能归属其 entry 日期所在分段，绝不能让训练段事件的 60 日标签跨入 holdout，否则最终一年仍泄漏进模型选择。这个边界测试比 Wilson 公式测试更优先。

OK @ 2026-07-18 21:40 Copilot R4

## Round 5 - Pilot

R4 的三点全部接受，其中"engine 集成而非旁路"和"t+1 open 成交时点"是真正的工程性纠偏，避免第一期就埋下语义分裂和乐观偏差。下面明确接受细节 + 一处保留意见，然后进入共识草稿。

### 1. engine 集成——完全接受

R4 正确指出独立 SignalEventCollector 会产生"两套评估语义"。我接受改造方案：

- `_composite_score()` 改为同时返回 `(score, {signal_name: SignalResult})`，或新增 `_evaluate_all_signals()` 方法返回完整 SignalResult 列表 + composite score。
- engine 在既有调仓日循环里把这份 SignalResult 列表交给可选的 `collector.on_eval_date(ticker, date, signal_results)` 回调。
- collector 自己负责"事件触发判定 + 去重 + checkpoint"，engine 不感知事件逻辑。
- 测试断言："同日同 ticker 的 SignalResult 等于策略评分输入"——这条断言是 stats 模块的可信度基石。
- 顺带把 `_composite_score()` 显式传 `pit_date` 也做了（R2 提的 PIT 合规点）。

这样 engine 闭环、TradeRecord、BacktestReport 都不动，只在调仓日多一个 collector 回调。

### 2. 混合采集（transition + confirmation + checkpoint）——接受，但 checkpoint 第一期可选

接受 `event_kind=transition|confirmation|checkpoint` 分类，以及"分析默认只展示 transition/confirmation，checkpoint 不混入胜率分母"。

一处保留：**持续型信号的 20/60 日 checkpoint 第一期标记为 "可选"**——如果实现成本超预算就推迟。理由：trend_template / trend_regime 这类持续型 signal 的"持续期衰减分析"是研究型问题，不是第一期"哪个形态准"的痛点。第一期保证 transition/confirmation 完整，checkpoint 留接口不强制实现。

### 3. 可交易时点 t+1 open——完全接受，这是 R4 最大的贡献

R4 戳穿了现有 engine 的乐观偏差（t 日 close 确认 + 同日 close 开仓）。这不仅是 stats 模块的问题，也是回测引擎本身的问题。第一期 stats 模块**独立**采用保守时点：

- 事件 t 日 close 后确认。
- 前瞻标签从 t+1 open 起算，应用 engine 同款 slippage + commission。
- forward return = (t+N close - t+1 open × (1+slippage)) × (1-commission) / ... 严格按 engine 公式。
- right-censored 事件标 `horizon_status=censored`，不计入胜率分母，单独展示"样本不足"列。
- direction=unknown 单独成桶，不进多空主表。

**对 engine 本身的乐观偏差修复留作第二期**——第一期 stats 用独立时点，保证统计结果不被乐观偏差污染。这一点需要在第一期报告里**显式标注**："stats 时点比 engine 更保守，两者结果不可直接比较"。

### 4. holdout 默认不强制——接受

接受 R4 方案：
- `--holdout-months 12` 默认，用户可改 `--holdout-months 0` 关闭。
- 训练段/holdout 段各自校验最小交易日数（如 ≥ 126），不满足则不生成"已验证"结论。
- **关键**：holdout 切分在事件生成后、聚合前做。同一事件按 entry 日期归属分段。
- **边界测试**：禁止训练段事件的 forward window 跨入 holdout——若 t 日事件 + 60 日 forward 跨边界，标 censored 不入分母。
- 这个边界测试比 Wilson 公式测试优先级更高（R4 的判断正确）。

### 5. hit_target/hit_stop 推后——接受

完全接受。第一期用净 forward return + MAE/MFE。target/stop 分析作为第二期，只对原 signal details 含 target 字段（major_reversal / continuation / cup_handle 等）启用。

### 6. 共识基本达成，进入草稿

R4 的反馈全部被采纳，无未解决分歧。剩余两点（checkpoint 可选、engine 本身乐观偏差留作第二期）是范围决定而非原则分歧。我现在写共识草稿，请 Copilot 质询。

OK @ 2026-07-18 21:45 Pilot R5

---

## 最终共识（草稿，R7 修订版）

### 优先级与范围

**第一期（"能用"）：SignalEvent 采集 + 前瞻标签 + 基础统计 + holdout**
**第二期（"好用"）：entry_snapshot 扩展 + target/stop 分析 + walk-forward + engine 乐观偏差修复 + checkpoint 持续期分析 + 空头收益模拟**

### 第一期技术合同

**1. engine 最小改造**：
- `_composite_score()` 显式接收 `pit_date` 参数；通过 `PITAwareProtocol` adapter 兼容旧 signal（见第 10 条）
- 新增或修改方法一次性返回 `(composite_score, {signal_name: SignalResult})`
- **stats collector 独立按日频（`stats_eval_freq="D"`）PIT 评估，不复用 engine 周频调仓循环**（R8/R9 修订）
- collector 提供 `on_daily_eval(ticker, date, signal_results)` 回调，engine 不直接调用（engine 闭环与 stats 各跑一遍，分别披露）
- 不动 TradeRecord / BacktestReport / engine 闭环

**2. SignalEvent 数据结构**（stats/events.py）：
```python
@dataclass
class SignalEvent:
    event_id: str  # uuid
    ticker: str
    signal_name: str
    event_type: str  # 统一二级键：DOUBLE_TOP / BULL_FLAG / RSI_CLASSIC_DIVERGENCE / TREND_TEMPLATE / ...
    pattern_type: str | None  # 兼容字段，仅形态类填，HTML 徽章用；stats 分析用 event_type
    direction: Literal["long", "short", "unknown"]
    confirmed_date: pd.Timestamp  # t 日
    entry_date: pd.Timestamp  # t+1，用于前瞻标签起算
    event_kind: Literal["transition", "confirmation", "checkpoint"]
    signal_value: float  # [-1, +1]
    details_whitelist: dict  # event_type/neckline/target/phase/state 等稳定字段
    holding_context: dict  # 调仓频率、冷却窗口、持仓状态
```

**3. 事件采集规则**：
- **EventExtractor 映射**：每个 signal 配套一个 extractor，签名 `extract(signal_result: SignalResult) -> list[ExtractedEvent]`（R8/R9 修订：返回 list 允许同日多事件）：
  - major_reversal / continuation：`event_type = details["pattern_type"]`（DOUBLE_TOP / BULL_FLAG 等）
  - oscillator_timing：`event_type = RSI_CLASSIC_DIVERGENCE / MACD_CLASSIC_DIVERGENCE / HIDDEN_DIVERGENCE / FAILURE_SWING / EXTREME`（从 divergence/macd_divergence/hidden_divergence/failure_swing/extreme 字段推导；同日 RSI + MACD 同时命中生成两条独立 event）
  - 非形态类 signal：`event_type = signal_name.upper()`（TREND_TEMPLATE / CANSLIM 等）
- **去重**：同 ticker × signal × event_type × direction × 20 个交易日冷却；同日同方向但不同 event_type 视为两个独立事件
- 持续型 signal（trend_template / trend_regime / market_direction 等）：transition 日触发（状态切换） + 20/60 日 checkpoint（**第一期可选**）
- 不再以"pattern_type 为空"推断"未确认"；未触发 extractor 规则的事件标 `event_type=UNCONFIRMED`
- direction 从 signal_value 符号推导，推不出来标 unknown

**4. 前瞻标签合同**（stats/analyzer.py）：
- 事件 t 日 close 后确认，t+1 open 成交（应用 engine 同款 slippage + commission）
- forward return 窗口：5/20/60 交易日（统一，所有 signal 一样，避免数据窥探）
- horizon 不足或跨 holdout 边界 → `horizon_status=censored`，不计入分母
- 指标：n / 胜率 / 平均净收益（EV，默认排序键降序）/ 中位数 / 平均盈/亏 / profit factor / 平均持仓（仅对成交型事件，事件型用 horizon）
- Wilson 95% 置信区间始终显示

**5. 低样本分层**（用户可配置，默认）：
- n < 20：标灰
- 20 ≤ n < 50：探索性
- 50 ≤ n < 200：低置信度
- n ≥ 200：默认展示
- CLI `--min-sample` 可改阈值

**6. holdout 切分**（防过拟合最低线）：
- `--holdout-months 12`（默认，可关闭）
- 训练段、holdout 段各自需 ≥ 126 交易日
- 切分在事件生成后、聚合前
- 训练段事件的 forward window 不得跨入 holdout（跨则 censored）
- 不满足时只输出全样本探索性报告 + 显式标注"无独立验证"

**7. 统计切片层级**：
- 顶层：signal 总表（major_reversal 整体 EV/胜率/n）
- 二级：signal × event_type × direction（DOUBLE_BOTTOM/long、RSI_CLASSIC_DIVERGENCE/long 单独）
- direction=short / unknown / event_type=UNCONFIRMED 各自单独成桶，**第一期仅作诊断展示，不进主排名**
- 跨 signal 比较用顶层表；signal 内比较用二级表

**8. 第一期 long-only 合同**：
- 主排名表只含 `direction=long` 事件
- direction=short / unknown 事件存档并诊断展示（"样本量 N，未做收益模拟"）
- 空头 short-adjusted return + 借券成本 + 空头执行 → 第二期
- 报告披露必须注明："第一期只统计 long 方向"

**9. 交付物**：
- `src/quant_scanner/stats/events.py`（SignalEvent + Collector + EventExtractor 映射）
- `src/quant_scanner/stats/analyzer.py`（前瞻标签 + Wilson + 分层）
- `src/quant_scanner/stats/reporter.py`（HTML 形态胜率表 + CLI 子命令）
- `src/quant_scanner/stats/pit_adapter.py`（PITAwareProtocol + evaluate_pit 探测）
- `tests/test_stats_events.py` / `tests/test_stats_analyzer.py` / `tests/test_stats_reporter.py` / `tests/test_stats_pit_compliance.py`
- 修改 `backtest/engine.py`（collector 回调 + pit_date adapter 调用）
- 现有 203 测试不破

**10. PIT adapter 合规层**：
- 新增 `PITAwareProtocol`（Python Protocol）：`def evaluate_pit(ticker, df, pit_date) -> SignalResult`
- engine 通过 `getattr(signal, 'evaluate_pit', None)` 探测：
  - 实现了 → 调用它传 pit_date
  - 未实现 → 回退原 `evaluate(ticker, df)`，log.warning 一次
- 第一期只把外部数据依赖型 signal（CANSLIM / RSRating / MarketDirection 等）改造为实现 `evaluate_pit`
- **必测项**：
  1. evaluate_pit 不读 pit_date 之后的数据（mock loader 验证）
  2. 未实现 evaluate_pit 的 signal 走回退不抛 TypeError
  3. 同 signal 不同 pit_date 对同一段 df 产生不同 SignalResult（外部数据型）

**11. 报告披露要求**（防误读）：
- 必须显式标注："stats 时点（t+1 open）比 engine 闭环（t close）更保守，两者不可直接比较"
- 必须标注："第一期只统计 long 方向；bearish/unknown 仅诊断展示"
- 必须标注 holdout 状态（已验证 / 全样本探索性 / 样本不足）
- 必须展示 Wilson 区间，胜率列仅作展示不作排序
- 低置信度行灰显

## Round 6 - Copilot（质询）

我会按质询要求检查草稿是否把统计语义、可交易时点和第一期范围真正闭合，重点找会导致结果被误读的遗漏。
有两处必须在定稿前补齐，否则第一期统计会产生错误分桶或错误收益含义。

1. "`pattern_type` 为空即未确认"不适用于现有 oscillator_timing。代码中 `major_reversal`、`continuation` 会写入 `details["pattern_type"]`，但 `oscillator_timing` 的已触发事件只写 `divergence`、`macd_divergence`、`hidden_divergence`、`failure_swing` 等字段，并不写 `pattern_type`。按草稿规则，RSI/MACD 背离、隐藏背离、失败摆动都会被错误归入"未确认"，二级表无法实现 R1 所述的五类统计。

建议在交付物中明确增加 signal-specific 的 `EventExtractor`/规范化映射：统一产出 `event_type`、`direction`、`confirmed`。`pattern_type` 仅作为形态类事件的展示维度；oscillator 使用例如 `RSI_CLASSIC_DIVERGENCE`、`MACD_CLASSIC_DIVERGENCE`、`HIDDEN_DIVERGENCE`、`FAILURE_SWING`、`EXTREME` 的 `event_type`。不能以字段缺失推断"未确认"。

2. 草稿定义了 `direction`，却未定义收益的方向归一化与策略边界。当前 engine 只有 long 开仓逻辑，负 `signal_value` 的形态不会进入现有成交闭环；而 SignalEvent 若同时统计 `DOUBLE_TOP/short`，其"forward return / EV"必须明确为 short-adjusted 净收益（`-raw_return` 后扣成本），且应披露为"假设可做空的信号研究"，不能与 long-only 策略收益直接并列。若第一期不模拟空头、借券成本和空头执行，应只统计 long 方向，并把 bearish/unknown 事件作为诊断数据而非主排名；二者必须择一写入合同。

此外，`pit_date` 显式传递需要一个兼容层：`BaseSignal.evaluate` 的抽象签名未含该参数，项目内只有部分 signal 接受它。不能直接对全部 signal 统一传 `pit_date`，否则会对多数现有实现抛 `TypeError`；应通过统一 adapter/protocol 逐步支持，且把"外部数据型 signal 的 PIT 测试"列为必测项。

OK @ 2026-07-18 21:50 Copilot R6

## Round 7 - Pilot（响应质询）

三处质疑全部成立，都是会在实现时立即翻车的工程性问题。逐一修改草稿。

### 质询 1 接受——引入 event_type 统一维度

放弃"pattern_type 为空 = 未确认"的简单规则。改为：

- 新增 `event_type` 字段，与 `pattern_type` 并列。
- 每个 signal 配套一个 `EventExtractor` 子类（或 dict 映射），负责从 SignalResult.details 提取 `(event_type, direction, confirmed)`：
  - major_reversal / continuation：`event_type = details["pattern_type"]`（DOUBLE_TOP / BULL_FLAG 等）
  - oscillator_timing：`event_type = RSI_CLASSIC_DIVERGENCE / MACD_CLASSIC_DIVERGENCE / HIDDEN_DIVERGENCE / FAILURE_SWING / EXTREME`（从 divergence / macd_divergence / hidden_divergence / failure_swing / extreme 字段推导）
  - 非形态类 signal：`event_type = signal_name.upper()`（TREND_TEMPLATE / CANSLIM 等）
- 二级切片维度统一为 `signal_name × event_type × direction`，不再用 pattern_type 作为唯一二级键。
- `pattern_type` 字段保留（兼容 HTML 报告已有徽章逻辑），但 stats 分析用 `event_type`。

### 质询 2 接受——第一期 long-only，bearish/unknown 进诊断桶

明确第一期合同：

- **主排名表只含 direction=long 事件**。
- direction=short / unknown 事件单独存档，仅在诊断视图展示（"这些空头形态样本量 N，未做收益模拟"）。
- 短头 EV 模拟（short-adjusted return）+ 借券成本 + 空头执行滑点 → **第二期**。
- 报告披露要求新增一行："第一期只统计 long 方向；bearish/unknown 形态样本量单独展示但无 EV 计算"。

### 质询 3 接受——pit_date 用 adapter，不破现有签名

- 新增 `PITAwareProtocol`（Python Protocol）：`def evaluate_pit(ticker, df, pit_date) -> SignalResult`
- engine 通过 `getattr(signal, 'evaluate_pit', None)` 探测：
  - 实现了 `evaluate_pit` → 调用它，传 pit_date
  - 未实现 → 回退原 `evaluate(ticker, df)`，并 log.warning 一次
- 第一期只把外部数据依赖型 signal（CANSLIM / RSRating / MarketDirection 等）改造为实现 `evaluate_pit`；其他 signal 保持现状。
- **必测项**：新增 test_stats_pit_compliance.py，断言：
  1. 每个 signal 的 evaluate_pit 不会读 pit_date 之后的数据（mock loader 验证）
  2. 未实现 evaluate_pit 的 signal 走回退路径不抛 TypeError
  3. 同 signal 在不同 pit_date 下对同一段 df 产生不同 SignalResult（如果该 signal 依赖外部数据）

### 修改后的共识草稿要点

下面只列改动，未改动部分（engine 集成方式 / checkpoint / t+1 open / holdout / Wilson / 低样本分层）维持草稿不变。

OK @ 2026-07-18 21:55 Pilot R7

## Round 8 - Copilot（最终审查）

我会逐项核验 R7 是否把事件分类、long-only 口径和 PIT 兼容机制落成可实施的契约，并只保留会阻断定稿的真实问题。
三处 R6 修订已落实：`event_type` 成为统一二级键；long-only 主排名与 short/unknown 诊断桶边界明确；`evaluate_pit` adapter 也避免了直接改动所有既有 `evaluate` 签名。仍有一项会影响统计语义的未解决问题，建议修正后再定稿。

`confirmed_date=t 日` 与"engine 调仓日回调"在默认周频下不一致。现有 engine 只在 `rebalance_set` 内评估信号，`rebalance_freq="W"` 时是每周第一个交易日；形态若在周三确认，collector 不会在周三看到它，只会在下周一重新评估，甚至可能已消失。因此该记录不能称为"confirmation 日"，t+1 open 也不是形态可交易的下一交易日，事件样本会随策略 rebalance 频率而改变。

需在草稿补一个明确选择：

- 推荐：stats 采用独立的 `stats_eval_freq="D"`，每日 PIT 评估与采集；策略仍可周频调仓，二者分别披露。
- 若为控制成本而复用回测调仓日：字段应改为 `observed_date`，报告必须声明"按调仓频率观察到的事件统计"，不得称形态确认日，且 `rebalance_freq` 必须是统计元数据和切片维度。

此外，oscillator 的 extractor 需要定义同日 RSI 与 MACD 同时命中时是生成两个 event，还是按既有优先级仅生成一个；当前"返回一个 `(event_type, direction, confirmed)`"的描述尚未决定该归因规则。若目标包含分别验证 RSI/MACD 两类，应该允许 extractor 返回 `list[ExtractedEvent]`，否则其中一类会被静默丢弃。

OK @ 2026-07-18 22:00 Copilot R8

## Round 9 - Pilot（最终修订）

两点全部接受。修订如下：

### 1. stats 独立日频采集，不复用 engine 调仓日

采用 R8 推荐方案：

- 新增 `stats_eval_freq="D"`（默认日频），stats collector 独立按日 PIT 评估，不复用 engine 周频调仓循环
- engine 闭环、stats collector 各跑一遍，二者分别披露
- 代价：stats 模块多评估 N 倍（日频 vs 周频），但能保证"confirmation 日 = t 日"语义干净
- collector.on_eval_date 改名为 collector.on_daily_eval（明确日频语义）
- 字段 `confirmed_date` 保留，含义明确为"形态确认当日"
- 报告必须披露："stats 按日频采集，与 engine 周频调仓策略独立运行"

### 2. extractor 返回 list，允许同日多事件

修改第 3 条 EventExtractor 签名：

```python
def extract(signal_result: SignalResult) -> list[ExtractedEvent]
```

oscillator_timing 同日若 RSI 经典背离 + MACD 经典背离同时命中，生成两条独立 event（event_type 分别为 RSI_CLASSIC_DIVERGENCE / MACD_CLASSIC_DIVERGENCE）。

去重规则不变：同 ticker × signal × event_type × direction × 20 个交易日冷却。同日同方向但不同 event_type 视为两个独立事件。

### 修改后定稿

将上述两点融入草稿，标记为 "R9 最终版"。这两处是细节修订，不改变第一期范围或核心架构。如无异议请确认定稿。

OK @ 2026-07-18 22:05 Pilot R9

## Round 10 - Copilot（最终确认）

我会做最后一次针对性核验：确认日频采集是否已与策略调仓解耦，以及多事件 extractor 是否保留 RSI/MACD 同日触发的完整归因。
无异议，可定稿。

R8 的两项问题已真正闭合：

- stats 已改为独立日频 PIT 评估，不再依赖 engine 的周频调仓日；`confirmed_date=t` 与 t+1 open 的前瞻标签语义一致，且与策略闭环明确分开披露。
- extractor 已改为返回 `list[ExtractedEvent]`，并明确 RSI 与 MACD 同日命中生成两条事件；去重键包含 `event_type`，不会互相吞掉归因。

我同时复核了 `event_type`/`pattern_type` 分工、long-only 主排名与 short/unknown 诊断边界、holdout 跨界 censoring、PIT adapter 回退兼容及对应测试要求。未发现其他阻断定稿的工程性或语义性缺口。

OK @ 2026-07-18 22:10 Copilot R10

---

## 讨论时间线

| Round | 方向 | 主题 | 结果 |
|-------|------|------|------|
| R1 | Pilot（盲测） | 现状描述 + 4 个开放问题（不抛倾向） | 盲测启动，等 Copilot 独立分析 |
| R2 | Copilot | 信号有效性 vs 策略归因二分；固定统计指标；PIT 隔离 | 提出核心二分架构，Pilot 接受 |
| R3 | Pilot（亮倾向） | 第一期只做 SignalEvent；事件型采集；EV 主排序；低样本分层 | 范围收窄，提出 2 个开放点 |
| R4 | Copilot | engine 集成非旁路；混合采集；t+1 open 时点；holdout 默认 | 三大工程性纠偏，Pilot 全部接受 |
| R5 | Pilot | 接受 R4 全部 + 进入共识草稿 | 写草稿 |
| R6 | Copilot（质询） | event_type 必要性；long-only 边界；pit_date adapter | 三处质询全部成立 |
| R7 | Pilot（响应） | 接受 R6 三处修订 | 草稿更新为 R7 修订版 |
| R8 | Copilot（二次质询） | confirmed_date 与周频冲突；extractor 多事件 | 两处新问题成立 |
| R9 | Pilot（最终修订） | stats 独立日频采集；extractor 返回 list | 草稿第 1/3 条更新 |
| R10 | Copilot（最终确认） | 复核 R8 闭合 + 全面审查 | 无异议，定稿 |

## 共识摘要（实施用）

**第一期目标**：建立 SignalEvent 采集 + 前瞻标签 + 基础统计系统，回答"哪个形态历史上准"。

**核心架构决定**（不可动摇）：
1. SignalEvent 独立于 TradeRecord，回答信号有效性而非策略归因
2. stats 按日频独立采集，不复用 engine 周频调仓日
3. engine 通过 PIT adapter 显式传 pit_date，旧 signal 走回退
4. 前瞻标签从 t+1 open 起算（保守时点），与 engine t close 闭环分开披露
5. event_type 替代 pattern_type 作为统一二级键（兼容 oscillator_timing）
6. extractor 返回 list 允许同日多事件（RSI + MACD 同日并存）
7. 第一期 long-only，bearish/unknown 进诊断桶不进主排名
8. holdout 默认 12 个月，事件后切分，跨界事件 censored

**实施顺序**：
1. stats/pit_adapter.py + 改造外部数据型 signal
2. stats/events.py（SignalEvent + Collector + EventExtractor 映射）
3. backtest/engine.py 增加 collector 接入点（或独立 runner）
4. stats/analyzer.py（前瞻标签 + Wilson + 分层 + holdout）
5. stats/reporter.py（HTML 表 + CLI 子命令）
6. tests（events / analyzer / reporter / pit_compliance 四套）
7. 跑 NVDA/AAPL 实盘验证

---

## 实施 Review（Codex 主驾，CC 副驾仅记录）

### Review 结论：不通过，需修复 5 个 High 问题

| 决定 | 落实情况 |
|------|---------|
| 1. SignalEvent 独立 | ✅ |
| 2. 日频采集 | ✅ |
| 3. PIT adapter | ⚠️ 部分（stats 用了，engine 没接入） |
| 4. t+1 open | ✅ |
| 5. event_type | ✅ |
| 6. extractor list | ✅ |
| 7. long-only | ❌（--include-short 用 long 公式违背） |
| 8. holdout | ❌（用 confirmed_date 而非 entry_date；holdout 不展示） |

### High 问题（必修）

1. **holdout 计算了 labels 但报告只渲染 train**——用户会把训练 EV 误读为已验证
2. **holdout 按 confirmed_date 切分**——共识规定按 entry_date
3. **持续型 signal 无 transition 检测**——每隔 20 日重复采样，破坏事件语义
4. **market_direction 拿个股 df 当市场指数**——严重数据语义错误
5. **--include-short 用 long forward return 公式**——与 long-only 合同冲突

### Medium 问题

1. PIT adapter 没接入 backtest engine（共识第 1 条：一期不改 engine 闭环，可接受；但 _composite_score 仍不传 pit_date）
2. --min-sample 无效参数
3. holding_context 永远空
4. 报告未披露"stats 独立日频采集、与 engine 独立运行"
5. runner 没 warmup 窗口

OK @ 2026-07-18 22:30 Copilot Review

---

## 修复 Re-Review（Codex 二次确认）

5 个 High + Medium 1/2/4 + Low 1 全部修复：
- High 1 holdout 并列展示 ✅
- High 2 按 entry_date 切分 ✅
- High 3 持续型 signal transition 检测 ✅
- High 4 market_direction 单独加载 ^GSPC，缺数据时 continue 跳过 ✅
- High 5 --include-short 禁用，short/unknown 永远走诊断桶 ✅
- Medium 1 holdout_validated 加 ok 标签检查 ✅
- Medium 2 --min-sample 真实生效 ✅
- Medium 4 报告披露加入"stats 按独立日频采集" ✅
- Low 1 文档与实现对齐（3x 而非 2.5x） ✅

**最终结论：同意进入"能用"阶段，无剩余 High。**
278 测试通过（原 203 + stats 模块 75 个）。

### 非阻断后续建议（第二期处理）
- 报告顶部状态也接收 HoldoutTable.is_validated
- engine 接入 PIT adapter（_composite_score 传 pit_date）
- runner 预热窗口（避免长 lookback signal 在统计区间前段缺失）

OK @ 2026-07-18 22:55 Copilot Final
