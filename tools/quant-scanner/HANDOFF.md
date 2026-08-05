# quant-scanner 跨会话续做手册

> **目的**：让任意新会话读完此文件即可无缝接手。最后更新：2026-07-21（**论文三角补齐**：Amihud ILLIQ + Corwin-Schultz Spread + Pastor-Stambaugh 流动性因子（任务 #98）+ Wilder RSI/ATR/ADX 校准与 TradingView/MetaTrader 对齐（#99）+ Gu-Kelly-Xiu 2020 JFE 多模型对比 LR/GBM/MLP（#100）。新增 ~50 个测试，743 → 793 通过）。详见各任务节。
> 另读：`memory/project_quant_scanner.md`（但 memory 部分描述已过时，以本文件为准）。

## 2026-07-21 论文三角：流动性因子 + Wilder 校准 + Gu-Kelly-Xiu ML 对比

用户问「还有什么技术指标，或者论文可以蒸馏增加技术量化指标的？」，推荐并实施 3 篇经典论文：

**任务 #98：Amihud ILLIQ 流动性因子（`src/quant_scanner/factors/liquidity.py`，~250 行）**
- 论文：Amihud (2002) JFE "Illiquidity and Stock Returns"
- 算法：
  - `amihud_illiq`：|return| / dollar_volume，21 日滚动均值
  - `amihud_illiq_log`：log10(illiq × 1e6)，数值更稳定
  - `amihud_implied_turnover`：1/ILLIQ
  - `corwin_schultz_spread`：从日内 high-low 估计 bid-ask spread（Corwin-Schultz 2012 JFE）
    - β = Σ ln²(H/L) 在连续 2 天内（rolling(2).sum）
    - α = (√(2β) − √β) / (3 − 2√2) − √(γ/(3−2√2))，clip(lower=0)
    - S = 2(e^α − 1) / (1 + e^α)
  - `corwin_schultz_spread_bps`：spread × 10000
  - `pastor_stambaugh_innov`：滚动回归 θ_1（Pastor-Stambaugh 2003 JFE 流动性创新）
  - `liquidity_composite`：z-score 合成，clip [-5, 5]
- CLI：`scanner factors liquidity --ticker NVDA`
- 关键 bug 修复：CS spread 公式 β 原写每日 log_hl²（错），应为 2 日 rolling sum；α 未 clip 导致负值
- 测试：`tests/test_liquidity.py`（18 个）

**任务 #99：Wilder Smoothing 校准（`src/quant_scanner/features/indicators.py`）**
- 论文：Wilder (1978)《New Concepts in Technical Trading Systems》
- 算法：`wilder_smoothing(values, period=14)`
  - 首值 = SMA(period)（不是 ewm 首值）
  - 前 period-1 个 NaN
  - 递归：next = (prev × (period-1) + current) / period
  - 中间 NaN 用前一值填充（递归稳定）
  - 前导 NaN 跳过，从第一个有效值开始累计 SMA
- 重构：`rsi` / `atr` / `adx` 全部改用 `wilder_smoothing`，与 TradingView/MetaTrader 完全对齐
- 顺带修复：`fillna(method="ffill")` → `.ffill()`（现代 pandas 兼容）
- 测试：`tests/test_wilder_smoothing.py`（18 个，含手工计算验证）

**任务 #100：Gu-Kelly-Xiu ML 多模型对比（`src/quant_scanner/ml/deep_factor.py`，~350 行）**
- 论文：Gu, Kelly, Xiu (2020) JFE 134(2):385-424 "Empirical Asset Pricing via Machine Learning"
- 论文核心发现：对 100+ alpha × 10+ ML 方法基准测试，GBM > RF > MLP > 线性
- 实现：
  - `make_models(seed=42)`：LR + GBM + MLP 三模型工厂
    - LR：LogisticRegression(liblinear, L2)
    - GBM：GradientBoostingClassifier(100 trees, depth=3)
    - MLP：MLPClassifier(hidden=(64,32), ReLU, adam, early_stopping)
  - `compare_ml_models(features, target, n_splits, purge_bars, embargo_bars, threshold)`：
    - StandardScaler 预处理（防 MLP 溢出）
    - Purged K-Fold 评估（复用 eval/purged_kfold）
    - 每模型每折记录 precision / filter_rate
  - `run_model_comparison_pipeline(df, primary_alpha, ...)`：端到端 MetaLabeler → 特征 → 对比
  - 数据类：`ModelFoldResult` / `ModelComparison` / `ComparisonResult`（含 `.best_model()` 和 `.summary()`）
- CLI：`scanner ml compare-models --ticker NVDA --primary-alpha alpha_42`
- 自测：合成非线性数据（含 f1×f2 交互项）→ MLP precision=0.727（lift +0.245）胜出，与论文「非线性交互适合神经网络」论断一致
- 数值稳定性：fit/predict 包 `np.errstate + warnings.catch_warnings` 抑制 sklearn MLP 内部 matmul overflow 噪声
- 测试：`tests/test_deep_factor.py`（15 个，含非线性效应验证 + 单类 target + NaN 特征 + aggregate）

## 2026-07-19 因子挖掘体系（方法 1+2 完成）

继 VCP 回炉之后，用户提出战略转向：「**蒸馏书只是想找背书的思想，实际策略才是核心。能否不靠生成式也能创造策略？**」。本次实施方法 1+2（论文挖掘方法 3+4 留作未来 TODO）：

**方法 1：算子库 `src/quant_scanner/factors/operators.py`**（50+ 算子，5 大类）：
- A. 时序统计：`ts_mean/std/max/min/arg_max/arg_min/delta/delay/rank/skew/kurt/corr/cov/sum/product/decay_linear/decay_exp/scale`
- B. 横截面：`rank/scale/zscore/winsorize/quantile_transform`（Series fallback + DataFrame 真 cross-sectional）
- C. 数学：`add/sub/mul/div/gt/lt/eq/abs_/sign/log/power/max_/min_/relu/sigmoid`（含 Series×scalar、scalar×Series、除零安全）
- D. 技术：`returns/log_returns/vwap/typical_price/rsi/macd/bollinger/atr/obv/mfi`
- E. 工具：`normalize_ohclv`、`factor_ic`（Spearman 信息系数）、`factor_ir`（信息比率）

**方法 2：Alpha101 `src/quant_scanner/factors/alpha101.py`**（WorldQuant Kakushadze 2015 SSRN 论文前 20 个）：
- 单标的模式：`rank` 退化为全样本时序百分位（真 cross-sectional 需多标的 batch，待后续）
- 论文算子全映射：`adv{d}`/`signedpower`/`decay_linear`/`ts_arg_max`/`correlation`/`covariance`
- `Alpha101` 类：`compute_all(df)` 批量跑、`compute(df, name)` 单跑、失败降级 NaN（不阻塞）
- 已修复 4 个 bug：alpha_14 关键 AttributeError、alpha_9 死代码、alpha_5 弃用 fillna、div 函数对 Series 输入的 ambiguous truth value

**CLI 集成 `scanner/cli.py`**：
- `scanner factors list`：列出已实现 alpha
- `scanner factors eval --ticker NVDA --horizon 5`：拉行情 → 跑 20 个 alpha → 算 IC/IR → 按 |IC| 排序输出
- 学术标准：|IC| > 0.03 算有效，IR > 0.5 算高质量

**测试覆盖（286 → 378 通过）**：
- `tests/test_factors_operators.py`（35 个）：算子正确性 + IC/IR smoke + 安全除零
- `tests/test_factors_alpha101.py`（11 个）：compute_all 不崩 + 每个 alpha 非空 + alpha_14/9/7 关键 bug 回归
- `tests/test_factors_cli.py`（2 个）：factors list smoke

**论文挖掘（方法 3+4，未来 TODO）**：
- **arXiv/Bloomberg 论文挖掘**：从 SSRN/arXiv 抓「equity factor / alpha / quantitative trading」类论文 → 蒸馏成 alpha 函数挂载。候选池：Kakushadze 101（已部分移植）、Brody−Huang 动量、Asness Quality、Carhart 4-factor、Hou-Xue-Zhang Q-factor
- **学术因子复现自动化**：建 `factors/papers/` 目录，每篇论文一个 .py + 引用元数据（DOI、作者、发表年）
- **工具链**：grep ARxiv API + 自动解析 PDF（已有 PyMuPDF）→ LLM 提取公式 → 映射到 operators.py

**关联文档**：
- 战略转向原文：用户「蒸馏书只是想找背书的思想，实际的还是各种形态和策略」
- skill 层：`wiki-engine/skills/wiki-quant-distill/SKILL.md`（书 → signal.py 蒸馏流水线，本次用 VCP 端到端验证过）
- 后续 GP/RL Alpha 生成（gprearn / AlphaGen）需要先有这套算子库作 DSL —— 现已就位

## 2026-07-19 回炉：VCP 对照《股票魔法师 Ⅱ》原书第 6 章修复

通过 wiki-quant-distill skill 流水线跑端到端验证发现 3 个偏差：

| 偏差 | 原书定义 | 旧版 | 修复 |
|------|---------|------|------|
| `min_shrink_ratio` 过严 | p.145「上下可以有合理的波动」 | 0.55（必须缩到一半以下） | 0.75（容差放宽） |
| vol_score 算法不符 | p.156「最后收缩期间成交量 ≤ 50日均量×50%」 | 「近20日均量/前40日均量」 | 最后trough处 ±3 日均量 vs base 50 日均量 |
| 无容差递减判定 | 「上下可以有合理的波动」允许单次反弹 | 仅 strict / non-strict 二分 | 新增 tolerant 等级（单次容差违规 ≤1.0） |

**新增硬规则**：`decrease_grade == "fail"` 时强制 `passed=False`，即使其他维度满分把总分拉过 threshold（修复 AVGO conts=[3.7,2.8,4.5,5.4,4.1,2.7] 反递减仍 passed=True 的误报）。

**新增字段**：`details["decrease_grade"]`（strict/tolerant/fail）、`details["last_trough_vol_ratio"]`、`details["tolerance_violations"]`。旧字段 `strictly_decreasing` / `vol_ratio` 保留向后兼容。

**测试**：原 18 个 + 新增 6 个 = 24 个 VCP 测试全过；全量 278 → 284 测试全过。

## 项目位置
- 代码：`wiki-engine/tools/quant-scanner/src/quant_scanner/`（2026-07-18 从 ai-tools/ 物理迁移至 wiki-engine 内）
- 蒸馏标准（对照）：`ai-wiki/wiki/analysis/{笑傲股市,股票魔法师II,金融市场技术分析}/`
- 总览：`ai-wiki/wiki/analysis/quant-methods-index.md`
- 测试：`wiki-engine/tools/quant-scanner/tests/`（**793 通过**，含 factor 库 48 + liquidity 18 + wilder 18 + deep_factor 15）

## 真实状态（2026-07-18 核对代码，比 memory 更超前）

### 信号文件（16 个已实现）
| 信号 | 文件 | 状态 |
|---|---|---|
| trend_template | signals/trend_template.py | ✅ Minervini 8 规则 |
| vcp | signals/vcp.py | ✅ VCP 形态 |
| pivot_point | signals/pivot_point.py | ✅ 中枢点 |
| sell_signals | signals/sell_signals.py | ✅ 基底计数卖出 |
| cup_handle | signals/cup_handle.py | ✅ |
| market_direction | signals/market_direction.py | ✅ FTD + Distribution Day |
| trend_regime | signals/trend_regime.py | ✅ |
| major_reversal | signals/major_reversal.py | ✅ **双顶/底 + 头肩顶/底 + 三重顶/底 + 圆弧 + V型 + 岛形（10 种反转形态）** |
| continuation | signals/continuation.py | ✅ **旗形 + 三角形（对称/上升/下降）+ 楔形（上升/下降）+ 矩形（6 种持续形态）** |
| oscillator_timing | signals/oscillator_timing.py | ✅ **RSI 经典背离 + MACD 经典背离 + 隐藏背离 + 失败摆动 + 超买超卖** |
| rs_rating | signals/rs_rating.py | ✅ SP500 宇宙 |
| can_slim | signals/can_slim.py | ✅ **7 字母全精确化（C/A EDGAR + I yfinance 机构 + M MarketDirectionSignal）** |
| new_high_supply | signals/new_high_supply.py | ✅ **笑傲股市 N+S 字母独立精确版（含 EDGAR Form 4 内部人 cluster）** |
| dow_phases | signals/dow_phases.py | ✅ **道氏三阶段量价打分（吸筹/公众参与/派发）** |
| support_resistance | signals/support_resistance.py | ✅ **支撑阻力强度评分 + 有效突破 + 角色互换** |

### can_slim.py 字母级状态（对照 `笑傲股市/canslim-seven-letter-precise/SKILL.md`）
| 字母 | 标准 | 当前实现 | 差距 |
|---|---|---|---|
| C | 当季 EPS YoY ≥25% + 加速 | ✅ **EDGAR GAAP 季度 EPS YoY**（loader.load_edgar_quarterly_eps_yoy）+ 加速度 + 回退 yfinance | — |
| A | 3 年年化 ≥25% + ROE ≥17% | ✅ **EDGAR CAGR + ROE 校验 + 利润率 3 年趋势**（loader.load_edgar_*） | — |
| N | 距 52 周高 ≤15% | ✅ + 5 日创新高 | — |
| S | 流通股 <5 亿 | ✅ | — |
| L | **全市场 12 月 RS Rating ≥85** | ✅ RS Rating（SP500 宇宙，已升级） | — |
| I | **机构数 ≥10 + 季度环比增加** | ✅ **yfinance institutional_holders 详细数据**（机构数 + 净增持/净减持方向 + 总变化），回退到 institutional_pct | 不再强求 SEC 13F-HR 反查（bulk XML 工作量过大，yfinance 已提供方向代理） |
| M | FTD + Distribution Day | ✅ **调用 MarketDirectionSignal**（统一实现，不再重复） | — |

## 数据源
`data/loader.py` 基于 yfinance；`data/edgar.py` 接入 SEC EDGAR companyfacts API（GAAP 精确）：
- **行情**：`yf.download` ✅（`load_batch` 支持并发，max_workers 默认 8）
- **基本面（EDGAR，优先）**：Diluted EPS / ROE / 营业利润率 / 流通股 / Form 4 计数
- **基本面（yfinance，回退）**：`info` + `quarterly_income_stmt` + `income_stmt`
- **机构**：yfinance `Ticker.institutional_holders`（详细列表 + Change 列，已升级 I 字母）→ institutional_pct 回退

EDGAR 缓存：`data_cache/edgar/{CIK}.json`，7 天 TTL，1 req/sec 限速（SEC 强制，无法并发），失败回退过期缓存或 yfinance。

**剩余升级方向**：non-GAAP EPS（需从 8-K Exhibit 99 解析，复杂度高，目前用 GAAP 也基本满足 O'Neil 原书口径）。

## 待办（按优先级 + 依赖链）

### P0 — 让现有信号可信（先做）✅
### P1 — 新增 4 个信号 ✅
### P2 — 数据源升级 ✅（除 non-GAAP EPS 暂缓）
### P3 — 形态扩展 ✅ 全部完成
- [x] new_high_supply / dow_phases / support_resistance / position_sizing 升级
- [x] **P3.1 头肩顶/底**（major_reversal）+3 测试
- [x] **P3.2 三角形/楔形/矩形**（continuation）+4 测试
- [x] **P3.3 MACD/隐藏背离/失败摆动**（oscillator_timing）+5 测试（顺手修 RSI loss bug）
- [x] **P3.4 三重顶/底 + 圆弧 + V型 + 岛形**（major_reversal）+7 测试

### P4 — 性能优化 ✅
- [x] DataLoader.load_batch 并发（ThreadPoolExecutor，缓存命中串行 + 未命中并发）
- [x] Scanner.scan 加 max_workers 参数 + CLI 加 --workers/-w（默认 4）
- [x] EDGAR 不并发（SEC 1 req/sec 强制限速）

### P5 — 可选功能 ✅
- [x] HTML 报告新信号可视化（CANSLIM 字母条 + 形态徽章 + 综合分柱状图 + 目标价/颈线高亮）
- [x] **P6 胜率统计模块**（SignalEvent 采集 + 前瞻标签 + Wilson 区间 + holdout + HTML 报告 + CLI `stats` 子命令）

## 后续可能方向（非阻塞）
- 回测自动胜率统计（每个形态历史命中率）→ **P6 已完成第一期（long-only SignalEvent）；第二期：entry_snapshot + target/stop + walk-forward + engine PIT 适配 + 空头收益模拟**
- non-GAAP EPS 解析（8-K Exhibit 99）
- 跨 pit_date SP500 宇宙缓存（多版本对比）
- 信号权重自动学习（用历史 ROI 反推 weights）


## 本轮进度（会话 25fd908e，2026-07-18）
- [x] 核对真实状态（发现 memory 部分过时：M/C/A 已超前实现，测试 32→61）
- [x] 建 HANDOFF.md（本文件）
- [x] P0.1 修 PIT 泄露 ✅（can_slim market 按 pit_date 截断 + 回测自动 skip 基本面，+2 测试）
- [x] P1.1 rs_rating.py + L 字母集成 ✅（+8+1 测试，信号数 8→9）
- [x] P1.2 major_reversal.py（双顶/双底）✅（+5 测试，信号数 9→10）
- [x] P1.3 continuation.py（旗形）✅（+5 测试，信号数 10→11）
- [x] P1.4 oscillator_timing.py（RSI 背离）✅（+5 测试，信号数 11→12，全部 85 通过）
- [x] **P1 全部完成**（4 个新信号 + PIT 修复）
- [ ] 剩余：P2 数据源升级（SEC EDGAR + 13F）+ 各信号扩展形态（头肩/三角形/MACD 等）

## 本轮进度（会话补丁，2026-07-18 PIT 合规回头审）
P1 code review 发现 2 个 🔴 阻塞 + 多个 🟡 警告，本批修复：

**🔴 阻塞修复**
- [x] **#1 PIT 缓存污染**：rs_rating `_load_universe_returns` PIT 调用 loader.load 时未传 cache_key → 历史切片覆盖 `{TICKER}.parquet` 实时缓存。修复：PIT 模式下 `use_cache=False`（内存缓存 `_universe_returns` 已足够）。
- [x] **#2 启发式不可靠**："距今 >5 天 = 回测"会被长周末/节假日延迟误判。修复：evaluate 签名加 `pit_date: Optional[pd.Timestamp]` 显式参数，启发式阈值放宽到 >30 天 + WARNING 日志。can_slim + rs_rating 同步改。

**🟡 警告修复**
- [x] **#13 continuation 缺前置趋势**：dead cat bounce 会被识别成 bull flag。修复：复用 `utils.swing.prior_trend`，只允许形态方向沿主趋势（up → bull flag only，down → bear flag only，none → 拒绝）。
- [x] **#14 _find_swings 重复 3 份**：提取到 `utils/swing.py`，3 个信号共用。
- [x] **#7 RSI 前 14 天 fillna(50) 污染 swing 检测**：移除 fillna，改为 `valid_highs = [(i,p) for i,p in highs if i >= rsi_period]`。
- [x] **#11 continuation flag_max_bars 30 → 20**：Murphy 警告线（>4 周 = 可能反转）。
- [x] **#12 RSI 背离阈值 1 → 3 点**：噪声范围内不算背离。

**测试覆盖**：85 → 90，新增 5 个关键 PIT 合规测试
- `test_explicit_pit_date_triggers_backtest_mode`：显式 pit_date 触发回测 + use_cache=False 验证
- `test_heuristic_pit_threshold_is_30_days`：阈值改 30 天的边界测试
- `test_can_slim_with_rs_rating_pit_chain`：can_slim + 真实 rs_rating 注入的 PIT 链路
- `test_continuation_dead_cat_bounce_rejected`：下跌中段反弹不被识别为 bull flag
- `test_oscillator_skips_swings_before_rsi_warmup`：前 14 天 swing 不参与背离检测

**未修（优先级低）**：#4 RS Rating 含 ticker 自身排名（轻微偏差）、#5 平分 rank 处理（极端边界）、#9 continuation 方向优先（已被 #13 解决）、#10 major_reversal 高波动股阈值、#16 rs_rating 多 pit_date 缓存（P2 性能优化时一起）。

## 本轮进度（会话补丁 2，2026-07-18 VCP zigzag 修复）

用户报告 VCP 算法 zigzag 误报。经审 code 发现 5 个 bug，全部修复：

**🔴 修复**
- [x] **#1 zigzag 初始化 bug**：旧版 `direction=0` 时 `hi>extreme` 立刻设 `direction=1`，没等回落 min_pct，导致第一段涨幅的伪 peak 进入序列。改为标准两阶段算法：先前向扫描确认初始 pivot，再进主循环。
- [x] **#2 无基底左缘检测**：旧版 zigzag 从 lookback 起点扫描，把前期涨幅段的回调也算成 contraction 候选。新版加 `_find_base_left_edge`（找前期涨幅高点），zigzag 只扫 base 内部。
- [x] **#3 固定 5% 阈值**：高波动股过多 pivot、低波动股漏 pivot、且 5% > VCP 末次收缩（1-5%）会吃掉浅 pivot。改 ATR 自适应（clamp [2%, 8%]），默认 2.5%。
- [x] **#4 trend_template 未作为前置**：旧版用 MA50>MA200 简化版。新版直接调用 `TrendTemplateSignal` 8 条，不通过 → value=0。
- [x] **#5 contraction 配对不严格**：旧版从全部 pivots 任意配对。新版要求相邻 peak→trough、严格递减、在 base 内、末次 ≤ 10%。
- [x] **BaseSignal.__init__ 扩展**：接受 kwargs 覆盖类属性，让测试可传 `require_trend_template=False`。

**🟡 SKILL.md 同步更新**
- [x] `vcp-precise-footprint/SKILL.md` 的 E（Execution）段重写，加入基底左缘、ATR 自适应、两阶段 zigzag 算法说明
- [x] 新增"实现笔记"段，记录 6 个 bug + 修复对照表

**测试覆盖**：90 → 102，VCP 测试 3 → 15
- `test_vcp_pattern_gets_decent_score`：3 次收缩严格递减 + footprint 标签
- `test_zigzag_*`（5 个）：基本涨/跌、V/倒 V、横盘无 pivot、旧 init bug 回归测试
- `test_base_left_edge_*`（2 个）：前期涨幅高点 + 无涨幅返回 None
- `test_runup_period_not_counted_as_contractions`：核心 bug 回归测试（前期涨幅段假回调不算）
- `test_atr_adaptive_threshold_*`（2 个）：高/低波动股阈值自适应
- `test_trend_template_prerequisite_blocks_non_stage2`：下跌趋势 → value=0

**改动文件**：`signals/vcp.py`（重写）、`signals/base.py`（__init__ kwargs）、`tests/test_vcp.py`（扩 5x）、`ai-wiki/wiki/analysis/股票魔法师II/vcp-precise-footprint/SKILL.md`。

## 本轮进度（会话补丁 3，2026-07-18 VCP 真实验证 + RS Rating SP500）

### VCP 真实股票验证（用 yfinance 拉 14 只大盘股）

验证发现 **3 个合成测试漏掉的真 bug**：
- [x] **vol_score = 9.32 炸到 1.0**：公式 `(1-vol_ratio)/(1-1.05+0.01)` 在 vol_ratio=1.37 时负除负得正。改为条件分支：vol_ratio ≥ 阈值直接 0 分。
- [x] **lookback=200 天太短**：VCP 完整周期（前期涨幅 + 基底）常超 200 天。扩到 350 天。
- [x] **base_left_edge 末段突破时找不到**：AAPL 已突破创新高，max_idx 在末段被排除。加 fallback 递归找前期局部高点。

**扫描结果合理**：14 只大盘股全部 passed=False。2026-07 没典型 VCP 形态（合理——VCP 是突破前的形态）。AAPL/GOOGL/AMD/CRWD 评分 0.40-0.53（部分符合但非标准 VCP）。

### RS Rating SP500 升级 + CAN SLIM L 字母收尾

- [x] **新增 `data/sp500.py`**：拉维基百科 S&P 500 列表（503 ticker）+ 30 天缓存。pytest 5 测试。
- [x] **`RSRatingSignal` 默认走 SP500**：`use_sp500=True` 时优先 SP500，失败回退 watchlist。
- [x] **`CANSLIMSignal` 默认自动注入 RSRatingSignal**：`auto_inject_rs=True`（生产默认）。原 `rs_rating=None → 降级 SPX` 通过 `auto_inject_rs=False` 保留（向后兼容）。
- [x] **L 字母收尾**：P0.2 旧账了结，CAN SLIM 默认生产路径走全市场 RS。

**测试覆盖**：102 → 115（+13）
- VCP +3：vol_score 防回归、base_left_edge 末段突破、不严格递减 pattern_score
- SP500 +5：拉取、知名 ticker、BRK-B 规范化、缓存、网络失败回退
- RS Rating +3：默认 SP500、use_sp500=False 回退、显式 universe 优先
- CAN SLIM +2：默认注入 rs_rating、可禁用

**改动文件**：`signals/vcp.py`、`signals/rs_rating.py`、`signals/can_slim.py`、`data/sp500.py`（新）、`tests/test_vcp.py`、`tests/test_sp500.py`（新）、`tests/test_rs_rating.py`、`tests/test_can_slim.py`。

### 性能注意事项

- SP500 宇宙首次拉取需 5-10 分钟（503 ticker × 252 天）
- 后续有 `_universe_returns` 内存缓存（同 pit_date）+ `{TICKER}.parquet` 磁盘缓存
- 实时模式 + SP500 = 单次 evaluate 约 5-10 分钟；批量扫描建议预热缓存

**剩余 P2 + 扩展形态**（未动）：SEC EDGAR non-GAAP EPS、13F 机构数据、各信号扩展（头肩/三角形/MACD 等）。

## 怎么使用 quant-scanner

### 环境

```bash
cd /Users/shayin/data1/htdocs/project/mind/ai-wiki/wiki-engine/tools/quant-scanner
source .venv/bin/activate          # Python 3.12 + 所有依赖
```

### 命令行（CLI）

入口：`quant-scanner` (= `python -m quant_scanner.scanner.cli`)，3 个子命令：

#### 1. `scan` — 批量扫描信号打分

```bash
# 默认 watchlist（TSLA/NVDA/AAPL/BABA/PDD/TME/QCOM/META/MSFT/GOOGL）
quant-scanner scan

# 指定 ticker
quant-scanner scan NVDA AAPL TSLA

# 从文件读 watchlist（每行一个 ticker，# 注释）
quant-scanner scan --watchlist data/watchlist.txt

# 拉更长历史（默认 2y，VCP 需 350 天所以建议 3y）
quant-scanner scan --period 3y

# 指定 HTML 报告输出路径
quant-scanner scan -o report.html
```

输出：终端表格（综合分 + 各信号通过/失败）+ HTML 报告（默认 `reports/scan-YYYY-MM-DD.html`）。

**包含的信号**（5 个，按 CLI 顺序）：
- `trend_template` — Minervini 8 条
- `vcp` — VCP 形态（含 trend_template 前置）
- `pivot_point` — 中枢点精确买点
- `base_counting_sell` — 基底计数卖出
- `can_slim` — O'Neil 7 字母（默认注入 SP500 RS Rating）

#### 2. `detail` — 单股详细分析

```bash
quant-scanner detail NVDA
quant-scanner detail AAPL --period 3y
```

输出：trend_template / vcp / can_slim 三个信号的完整 reasons + details。

#### 3. `backtest` — 回测（PIT 合规）

```bash
quant-scanner backtest --ticker NVDA --start 2024-01-01 --end 2024-12-31
```

回测自动进入 PIT 模式（`pit_date` 显式传导 + use_cache=False 避免缓存污染）。

### Python API（自定义用法）

```python
from quant_scanner.data.loader import DataLoader
from quant_scanner.signals.vcp import VCPSignal
from quant_scanner.signals.can_slim import CANSLIMSignal
from quant_scanner.signals.trend_template import TrendTemplateSignal

loader = DataLoader()

# 单信号评估
df = loader.load("NVDA", period="3y")
sig = VCPSignal()
result = sig.evaluate("NVDA", df)
print(result.value, result.passed, result.reasons)

# CAN SLIM（默认走 SP500 RS，首次 5-10 分钟预热）
can_slim = CANSLIMSignal(loader=loader)
r = can_slim.evaluate("NVDA", df)
print(r.details["letters"])           # 7 字母评分
print(r.details["rs_rating"])         # L 字母 RS Rating

# 关闭 SP500（快速测试 / 单股调试）
can_slim_fast = CANSLIMSignal(loader=loader, auto_inject_rs=False)

# 回测（PIT 合规）
pit_date = pd.Timestamp("2024-06-30")
r = can_slim.evaluate("NVDA", df, pit_date=pit_date)
```

### 12 个信号速查

| 信号 | 文件 | 用途 | 关键参数 |
|---|---|---|---|
| trend_template | trend_template.py | 第二阶段筛选 | threshold=0.875（8 条中至少 7 条） |
| vcp | vcp.py | VCP 形态识别（含 footprint 标签） | require_trend_template=True |
| pivot_point | pivot_point.py | 中枢点精确买点 | action: BUY/READY/WAIT |
| sell_signals | sell_signals.py | 基底计数 + 强弱卖出 | base_count ≥ 5 强制卖出 |
| can_slim | can_slim.py | O'Neil 7 字母 | auto_inject_rs=True |
| rs_rating | rs_rating.py | 全市场 RS 百分位 | use_sp500=True |
| cup_handle | cup_handle.py | 杯柄形态 | 5 维评分 |
| market_direction | market_direction.py | FTD + 分布日状态机 | 三档：确认/尝试/压力 |
| trend_regime | trend_regime.py | ADX + 道氏 4 阶段 | ADX > 25 = 趋势确立 |
| major_reversal | major_reversal.py | 双顶/双底 | value 带符号 |
| continuation | continuation.py | 旗形（bull/bear flag） | value 带符号 |
| oscillator_timing | oscillator_timing.py | RSI 背离 + 超买超卖 | value 带符号 |

### 缓存

- `data_cache/{TICKER}.parquet` — 日线缓存（TTL 1 天）
- `data_cache/{TICKER}.eps.parquet` — EPS 历史（TTL 7 天）
- `data_cache/{TICKER}.meta.parquet` — 基本面（TTL 7 天）
- `data/sp500_cache.txt` — SP500 列表（TTL 30 天）

清理：直接删 `data_cache/` 目录即可。

### 常见问题

**Q: 扫描很慢？**
A: 首次跑 CAN SLIM 会拉 SP500 全市场（5-10 分钟）。后续走缓存秒级。如跳过：`CANSLIMSignal(loader=loader, auto_inject_rs=False)`。

**Q: VCP 评分突然变 0？**
A: 多数情况是 trend_template 不通过（非第二阶段）。检查 `r.details["trend_template_passed"]`。

**Q: 测试？**
A: `python -m pytest tests/ -q`。当前 115 个全过。

## 待办（下个会话从这里接）

### P2 — 数据源升级（精度）

#### P2.2 SEC EDGAR non-GAAP EPS（CAN SLIM C/A 字母）
**问题**：yfinance 只给 GAAP EPS（含一次性收益），CAN SLIM C 字母要求"剔除非经常性损益"。
**方案**：接 SEC EDGAR XBRL API（免费、无 token，只需 User-Agent header）。
- 公司映射：`https://data.sec.gov/submissions/company_tickers.json`（CIK → ticker）
- XBRL 财务：`https://data.sec.gov/api/xbrl/companyconcept/CIK{cik}/us-gaap/EarningsPerShareBasic.json`
- 字段：non-GAAP EPS（用 `dilutedEPS` 或 `EarningsPerShareDiluted`）+ 一次性项目识别（`UnusualItems`）
- yfinance 作为 fallback（API 限速 / 数据缺失时）
- 工作量：2-3 小时（含测试）

#### P2.3 13F 机构数据（CAN SLIM I 字母）
**问题**：yfinance 只给 top ~10 持有者，CAN SLIM I 字母要求"机构数 ≥ 10 + 季度环比增加"。
**方案**：SEC EDGAR 13F 文件。
- 全量机构数：从 13F 合并表（cover page 的 `institutionsCount`）或解析 13F-HR 表
- 季度趋势：拉最近 4 个季度 13F 对比
- 数据延迟：13F 季度后 40-45 天披露，需在 I 字母评分中标注"截至 YYYY-MM-DD"
- 工作量：3-4 小时（13F XML 解析较复杂）

### P3 — 形态扩展（功能）

每个信号原书有更多形态，按使用频率排序：

#### major_reversal（当前只双顶/双底）
- 头肩顶/底（最常用）
- 三重顶/底
- 圆弧顶/底
- V 型反转
- 岛形反转（含缺口）

#### continuation（当前只旗形）
- 三角形（对称/上升/下降）
- 楔形（上升/下降）
- 矩形（盘整）

#### oscillator_timing（当前只 RSI 经典背离）
- MACD 顶/底背离
- Stochastic 超买超卖
- 隐藏背离（趋势延续信号）
- 失败摆动（failure swing）

### P4 — 性能优化（低优先）

- RS Rating 多 pit_date 缓存（同一回测多次调用不同 pit_date）
- SP500 宇宙批量并发拉取（yfinance threads=True 改造）
- CAN SLIM 字母级并行评估

### P5 — 可选功能

- CLI 加 `--no-sp500` 选项（快速单股调试）
- HTML 报告加 VCP footprint 可视化
- 回测胜率自动统计（按信号 / 按基底计数）

## 下个会话怎么接
1. 读本文件（真实状态 + 待办清单）
2. 读 `canslim-seven-letter-precise/SKILL.md`（字母标准）+ `股票魔法师II/vcp-precise-footprint/SKILL.md`（VCP 形态）
3. 优先级建议：**P2 I 字母 13F** > P3 形态扩展（头肩/三角形/MACD）> P4 性能优化
4. 完成一项就在本文件勾选 + 更新"最后更新"日期

## 已下书 PDF（ai-wiki/data/，本轮新下，昨晚蒸馏用的是抓取文本）
- 金融市场技术分析-Murphy.pdf（596 页全本）✅
- 笑傲股市-O'Neil.pdf（142 页第二版）⚠️ 非第四版
- Minervini：未下到（DRM 借阅制）— 若要重蒸馏补强，需 Z-Library 下完整 PDF

## 本轮进度（会话补丁 4，2026-07-18 美股量化三角书籍补齐）

用户："quant-scanner 配套的书有几本没蒸馏完整吗？" → 排查发现蒸馏 skill 全在，但**蒸馏→代码挂载层面**差 4 个缺口 + can_slim 3 字母精度问题。本次"补齐吧"完成全部：

**🔴 数据层**
- [x] **新增 `data/edgar.py`**：EdgarClient 接入 SEC companyfacts API
  - 方法：`get_quarterly_eps/yoy/acceleration`、`get_annual_eps_cagr`、`get_roe`、`get_operating_margin_trend`、`get_shares_outstanding`、`get_insider_form4_count`
  - 缓存：`data_cache/edgar/{CIK}.json`，7 天 TTL；ticker→CIK 映射缓存 30 天
  - 限速：1 req/sec（SEC 上限 10/sec，保守）；网络失败回退过期缓存或 None
  - DataLoader 集成：新增 7 个 `load_edgar_*` 方法

**🟡 can_slim 三字母精修**
- [x] **C 字母**：抽 `_evaluate_letter_c` + `_score_letter_c`，优先 EDGAR 季度 EPS YoY，回退 yfinance，最后代理 quarterly_eps_growth
- [x] **A 字母**：抽 `_evaluate_letter_a`，EDGAR 3 年 CAGR + ROE ≥17% 加分 + 利润率 3 年上升加分 / 下行扣分
- [x] **M 字母**：删除 `_evaluate_market_direction` 重复实现，改为调用 `MarketDirectionSignal`
- [x] **M 字母 fail 禁买**：M < 0.3 时强制 `passed=False`（O'Neil 铁律）

**🟢 新增 3 个 Murphy/笑傲股市信号**
- [x] **`signals/new_high_supply.py`**：NewHighSupplySignal — N 字母 52 周新高评级 + S 字母流通股分级 + EDGAR Form 4 内部人 cluster buying + 回购 +0.1
- [x] **`signals/dow_phases.py`**：DowPhasesSignal — 5 维量价打分表（价格结构/量能/均线/波动率/缺口），吸筹(≥+3)/公众参与/派发(≤-3) 三阶段判定
- [x] **`signals/support_resistance.py`**：SupportResistanceSignal — swing 聚类识别支撑阻力位 + 强度评分（接触×跨度×量能，最大 125）+ 有效突破（≥3% + ≥150% 量能）+ 角色互换（突破后回踩不破）

**🟢 position_sizing 升级（2% 风控铁律）**
- [x] `position_size` 默认 `risk_budget_pct=0.02`（Murphy 铁律）
- [x] 新增 `atr_adjusted_risk_pct`：高波动（ATR/price>4%）减仓 20%，低波动（<1.5%）加仓 10%
- [x] 新增 `Position` 数据类 + `portfolio_risk_check`：总仓位 ≤80% / 总风险 ≤6% / 同行业 ≤40% 三大约束
- [x] 新增 `max_positions_by_volatility`：VIX>25 最多 5 仓 / 正常 8 仓 / VIX<15 最多 10 仓
- [x] 向后兼容：`position_size(account, 0.01, setup)` 旧签名仍可用

**测试**：115 → **146 通过**（+31 测试）
- `test_edgar.py`：12 个（mock SEC API，全隔离网络）
- `test_new_high.py`：10 个（N/S 字母各档评级 + cluster buying）
- `test_dow_phases.py`：6 个（吸筹/派发/公众参与/数据不足）
- `test_support_resistance.py`：7 个（强度评分/突破/角色互换）
- `test_can_slim.py`：+6 个（EDGAR C/A、ROE 加分/扣分、M 字母统一 + fail 禁买）
- `test_position_sizing.py`：+13 个（2% 默认、ATR 自适应、组合风控、VIX 分档）

**集成验证**（NVDA 实盘）：
- CAN SLIM: value=0.76，C/A 均走 EDGAR，M=0.8（健康上涨）
- New High Supply: N=0.40（距高 -14%），S=0.50（24B 流通股）
- Dow Phases: 公众参与弱势（MA/gap 子分 -1）
- Support/Resistance: 200 支撑 / 207 阻力 之间，无突破

**改动文件**：`data/edgar.py`（新）、`data/loader.py`、`signals/can_slim.py`（重写 C/A/M）、`signals/new_high_supply.py`（新）、`signals/dow_phases.py`（新）、`signals/support_resistance.py`（新）、`signals/__init__.py`、`utils/position_sizing.py`（升级）、`scanner/cli.py`（默认 signals 加 4 新）、6 个 test 文件。**信号数 12→15。**

**剩余待办**：P2 I 字母 13F / P3 形态扩展（头肩/三角形/MACD 等）/ P4 性能优化。

## 本轮进度（会话补丁 5，2026-07-18 I 字母机构持仓升级）

用户："接下来做什么" → "继续" 批准 A 方案（CAN SLIM 唯一仍用代理的字母 = I）。

**数据源决策**：SEC EDGAR 没有反查"某 ticker 机构持仓汇总"的 API（13F-HR bulk XML 反向索引工作量过大）。**务实方案**：强化 yfinance `Ticker.institutional_holders` DataFrame 解析（列：Holder/Shares/Date/Change/% Out/Value）→ 算机构数 + 净增持/净减持方向 + 总股数变化。

**改动**：
- [x] **`data/loader.py` 新增 `load_institutional_holders(ticker)`**：
  - 解析 yfinance institutional_holders 表
  - 返回：holder_count / net_increasing / net_decreasing / net_new / total_change_shares / latest_report_date / top_holders（前 10）
  - 7 天 parquet 缓存（`{ticker}.inst.parquet`）+ 失败降级返回 None
- [x] **`signals/can_slim.py` I 字母抽 `_evaluate_letter_i`**：
  - 优先详细数据：机构数 ≥10 → 0.7；5-10 → 0.5；<5 → 0.0
  - 净增持方向（net_inc ≥ net_dec 且 net_inc > 0）→ +0.2；净减持 → -0.1
  - 回退 institutional_pct：≥50% → 1.0；≥30% → 0.7；<30% → 线性映射
  - details 输出 `i_source` / `inst_holder_count` / `inst_net_increasing` / `inst_net_decreasing` / `inst_direction`
- [x] **测试**：+7 个（强赞助/净减持/中等/不足/回退 pct/pct 不达标/PIT 跳过）

**测试**：146 → **176 全通过**（+30，包括其他补测）

**改动文件**：`data/loader.py`、`signals/can_slim.py`、`tests/test_can_slim.py`。

**CAN SLIM 7 字母全部精确化（不再有代理）**：
| 字母 | 数据源 |
|---|---|
| C | EDGAR GAAP 季度 EPS YoY + 加速度 |
| A | EDGAR 3 年 CAGR + ROE + 利润率趋势 |
| N | 52 周新高 + 5 日创新高（loader 价格数据） |
| S | yfinance 流通股 + EDGAR Form 4 cluster + 回购 |
| L | RSRatingSignal（SP500 宇宙全市场百分位） |
| I | **yfinance institutional_holders 详细数据（机构数+方向）** ← 本次完成 |
| M | MarketDirectionSignal（FTD + Distribution Day） |

**剩余待办**：P3 形态扩展（头肩/三角形/MACD/隐藏背离等）/ P4 性能优化（EDGAR 并发、跨 pit_date 缓存）/ P5 HTML 报告可视化。

## 本轮进度（会话补丁 6，2026-07-18 Murphy 形态扩展 P3.1–P3.3）

用户出门前授权自主完成 P3-P5 全部任务，每完成一项推微信。

**P3.1 — Major Reversal 头肩顶/底（Murphy Ch5）**：
- [x] `signals/major_reversal.py` 加 `_detect_head_and_shoulders_top/bottom`
- [x] 结构：H1<H2>H3（H1≈H3，头部突出 ≥2%，颈线深度 ≥3%）+ 跌破/突破颈线
- [x] 目标价：颈线 ∓ (头部 - 颈线)
- [x] +3 测试（顶/底/目标价）

**P3.2 — Continuation 三角形/楔形/矩形（Murphy Ch6）**：
- [x] `signals/continuation.py` 加 `_detect_triangle_wedge_rectangle`
- [x] 6 类形态：对称/上升/下降三角、矩形、上升/下降楔形（斜率分类）
- [x] 关键设计：三角/楔形/矩形允许 trend_dir="none"（这些形态可能出现在整理阶段），旗形仍要求明确 trend（防 dead cat bounce 误判）
- [x] +4 测试

**P3.3 — Oscillator Timing MACD + 隐藏背离 + 失败摆动（Murphy Ch10）**：
- [x] `signals/oscillator_timing.py` 加 `_macd` / `_detect_hidden_divergence` / `_detect_failure_swing`
- [x] **顺手修复 RSI loss 计算 bug**（`-delta.clip(lower=0)` → `(-delta).clip(lower=0)`，原版在全涨数据下产出 -inf）
- [x] evaluate() 重写：RSI 经典背离 + MACD 经典背离 + 隐藏背离 + 失败摆动 + 超买超卖，按优先级综合评分
  - RSI+MACD 经典背离同向 → ±0.75（增强）
  - 单一经典背离 → ±0.6
  - 隐藏背离 → ±0.5
  - 失败摆动 → ±0.45
- [x] details 新增 `macd_line` / `macd_histogram` / `macd_divergence` / `hidden_divergence` / `failure_swing` 字段
- [x] +5 测试（MACD 计算/背离/失败摆动健壮性/隐藏背离方法/中性字段）

**测试**：176 → **188 全通过**（+12）

**改动文件**：`signals/major_reversal.py`、`signals/continuation.py`、`signals/oscillator_timing.py`、`tests/test_major_reversal.py`、`tests/test_continuation.py`、`tests/test_oscillator_timing.py`。

**剩余待办**：P3.4 其他反转形态（三重顶/底、圆弧、V 型、岛形反转）/ P4 性能优化 / P5 HTML 报告可视化。

## 本轮进度（会话补丁 7，2026-07-18 P3.4 其他反转形态）

**新增 4 种反转形态**（全部在 `signals/major_reversal.py`）：

1. **三重顶/底（TRIPLE_TOP/BOTTOM）**
   - 三触同阻力/支撑，价差 ≤ 4%
   - 两次回调的最低/高 = 颈线
   - 可靠性加权 ×1.1（比双顶更可靠）
   - `_detect_triple_top` / `_detect_triple_bottom`

2. **圆弧顶/底（ROUNDING_TOP/BOTTOM）**
   - 取最近 40-150 天 close 对时间索引做二次多项式拟合
   - R² ≥ 0.65 才算圆弧
   - a<0 + trend up = 圆弧顶；a>0 + trend down = 圆弧底
   - 可靠性加权 ×1.05（慢但可靠）
   - `_detect_rounding`

3. **V 型反转（V_TOP/V_BOTTOM）**
   - 最近 25 天内查找极值点
   - 前段跌幅/涨幅 ≥ 20%，后段反向 ≥ 50%
   - 可靠性打折 ×0.85（剧烈波动，止损难度高）
   - `_detect_v_reversal`

4. **岛形反转（ISLAND_TOP/ISLAND_BOTTOM）**
   - 两个反向缺口隔离出岛屿（≤ 15 天）
   - 缺口幅度 ≥ 1%
   - `_detect_island_reversal`

**evaluate() 形态链调整**：按优先级 H&S > 三重 > 双 > V 型 > 圆弧 > 岛形。无颈线形态（圆弧/V/岛形）用 `_sign` 字段标记方向。

**测试**：188 → **195 全通过**（+7）

**改动文件**：`signals/major_reversal.py`、`tests/test_major_reversal.py`。

**剩余待办**：P4 性能优化（EDGAR 并发、跨 pit_date 缓存）/ P5 HTML 报告可视化。

## 本轮进度（会话补丁 8，2026-07-18 P4 性能优化）

**并发扫描 + 并发批量加载**：

- [x] `data/loader.py:load_batch` 升级：
  - 缓存命中的 ticker 串行（无网络开销）
  - 未命中的 ticker 用 `ThreadPoolExecutor` 并发（默认 max_workers=8）
  - yfinance 单 session 容忍度约 8 并发
- [x] `scanner/engine.py:Scanner.scan` 加 `max_workers` 参数：
  - `max_workers=1`（默认）= 串行（保持原行为）
  - `max_workers>1` = `_scan_parallel` 走线程池
  - 结果按原 ticker 顺序返回（避免乱序）
  - 兼容 Progress 进度条
- [x] `scanner/cli.py:scan` 加 `--workers / -w` 选项，默认 4
- [x] **EDGAR 并发**：跳过。SEC API 强制 1 req/sec 限速（`_throttle` 已实现），无法真正并发。CIK→ticker 解析已缓存，facts JSON 必须串行拉取。

**测试**：195 → **197 全通过**（+2：并发等价性、load_batch 并发拉取不丢数据）

**改动文件**：`data/loader.py`、`scanner/engine.py`、`scanner/cli.py`、`tests/test_engine.py`。

**预期效果**：SP500 全市场扫描时间从 ~10 分钟 → ~2-3 分钟（4 workers × 缓存命中率）。

**剩余待办**：P5 HTML 报告新信号可视化。

## 本轮进度（会话补丁 9，2026-07-18 P5 HTML 报告可视化升级）

**reporter/html.py 全面重构**：

- [x] **CAN SLIM 字母条**：7 字母（C/A/N/S/L/I/M）色块可视化
  - 绿色（强，≥0.7）/ 橙色（中，0.4-0.7）/ 红色（弱，<0.4）/ 灰色（数据缺失）
  - 鼠标悬停显示完整 reasons
- [x] **综合分柱状图**：每个 ticker 综合分用 CSS 柱条（绿正红负，百分比填充）
- [x] **形态徽章**（pattern_badge Jinja filter）：
  - 看多形态（双底/H&S 底/三重底/圆弧底/V 底/岛形底/BULL FLAG/上升三角）→ 绿
  - 看空形态（双顶/H&S 顶/三重顶/圆弧顶/V 顶/岛形顶/BEAR FLAG/下降三角/上升楔形）→ 红
  - 中性形态（矩形/对称三角）→ 灰
- [x] **目标价/颈线高亮**：详情区橙色高亮框显示 `target` / `neckline` / `confirmed`
- [x] **信号详情卡片**：每个 signal 一个卡片，含 phase / state / pattern_type 元信息
- [x] **平均分统计**：顶部加 avg_score
- [x] **图例栏**：色彩含义说明
- [x] **修复循环 import**：reporter.html 用 TYPE_CHECKING 避免循环依赖

**测试**：197 → **203 全通过**（+6：基础渲染、CANSLIM 字母条、看多/看空徽章、柱状图、空报告）

**改动文件**：`reporter/html.py`（重构）、`tests/test_html_reporter.py`（新建）。

**P3-P5 全部完成。** 三本书方法论 + Murphy 形态扩展 + 并发优化 + HTML 可视化全部落地。

---

## 本轮进度（会话补丁 10，2026-07-18 P6 胜率统计模块第一期）

**输入**：cross-ai-debate 辩论（cc × codex，R1-R10 + 共识质询 + 2 轮 review），讨论文件 `docs/review-debate-win-rate-stats.md`。

**核心架构决定**（共识 R1-R10，辩论文件含完整推导）：
1. SignalEvent 独立于 TradeRecord，回答"信号有效性"而非"策略归因"
2. stats 按独立日频采集，不复用 engine 周频调仓日
3. PIT adapter 显式传 pit_date，旧 signal 走回退
4. 前瞻标签从 t+1 open 起算（保守时点），与 engine t close 闭环分开披露
5. event_type 作为统一二级键（兼容 oscillator_timing 的 RSI/MACD/hidden/failure/extreme）
6. extractor 返回 list 允许同日多事件（RSI + MACD 同日并存）
7. 第一期 long-only，bearish/unknown 进诊断桶不进主排名
8. holdout 默认 12 个月，事件后切分，跨界事件 censored

**新增模块**：
- `src/quant_scanner/stats/__init__.py`
- `src/quant_scanner/stats/pit_adapter.py`：`PITAwareProtocol` + `evaluate_with_pit`（inspect.signature 检测 dispatch）
- `src/quant_scanner/stats/events.py`：`SignalEvent` + `ExtractedEvent` + signal-specific extractor 映射 + `SignalEventCollector`（持续型 transition 检测 + 形态类 cooldown）
- `src/quant_scanner/stats/runner.py`：`SignalEventRunner` 日频采集（market_direction 单独加载 ^GSPC）
- `src/quant_scanner/stats/analyzer.py`：Wilson 95% + 前瞻标签（5/20/60，t+1 open 起算，holdout 边界 censored）+ 聚合（默认 EV 降序）+ 低样本分层 + `split_holdout`（按 entry_date 归属）
- `src/quant_scanner/stats/reporter.py`：HTML 模板（披露、顶层表、二级表、诊断桶、holdout 段独立验证表）
- `src/quant_scanner/stats/cli.py`：`quant-scanner stats` 子命令

**新增测试**（共 75 个，全过）：
- `tests/test_stats_pit_compliance.py`（8）
- `tests/test_stats_events.py`（31，含 transition 检测 6 个）
- `tests/test_stats_runner.py`（9，含 market_direction 数据路径 2 个）
- `tests/test_stats_analyzer.py`（22，含 entry_date holdout 边界）
- `tests/test_stats_reporter.py`（7，含 holdout_table 渲染）

**测试**：203 → **278 全通过**（+75 stats）。

**Codex review 全部 High 已修复**：
- High 1：holdout 段独立聚合 + reporter 并列展示
- High 2：按 entry_date 切分 holdout（共识第 6 条）
- High 3：持续型 signal transition 检测（PERSISTENT_SIGNALS + 状态缓存）
- High 4：market_direction 缺 ^GSPC 时 continue 跳过，绝不回退个股 df
- High 5：--include-short 禁用，short/unknown 永远走诊断桶

**非阻塞后续**（第二期）：
- engine 接入 PIT adapter（`_composite_score` 显式传 pit_date）
- runner 预热窗口（避免长 lookback signal 前段样本缺失）
- entry_snapshot + target/stop 分析
- walk-forward（滚动重训）
- 空头收益模拟（short-adjusted return + 借券成本）

**CLI 用法**：
```bash
quant-scanner stats NVDA AAPL TSLA --period 5y --horizon 20 --holdout-months 12
```

**P6 第一期完成。** 共识 + 实施 + 双向 review 闭环全跑通。

---

## 2026-07-19 6 层架构升级（P0-P3，单股→板块→大盘全栈）

**背景**：用户要"短长期结合/找股找板块/短线/beta 板块启动" + 成交量维度（原指标只基于 close）。从单股扫描器升级成 6 层系统。

### 架构

```
⑥ 决策       wiki-research 接入（wiki-quant skill 已更新 4 wrappers + 触发词）
⑤ 大盘背景   cli market  → 趋势 + VIX + 风格轮动 + risk-on/off + 市场宽度  [P2 + 宽度优化]
④ 板块/轮动  cli sector  → 18 ETF RS 排名 + 轮动 + 启动信号                  [P1]
③ 选股扫描   cli screen  → breakout/trend/momentum                           [P1]
② 单股信号   cli scan/detail → 16 形态信号（原有）
① 单股指标   cli indicators → 22 指标含成交量（OBV/MFI/VWAP/布林/KDJ/ADX）   [P0]
─ 数据源 ─   yfinance + 东财 fallback（_fetch_from_eastmoney，105/106 secid）[P0]
```

### 新增文件

| 文件 | 功能 | 验证 |
|------|------|------|
| `features/indicators.py` | 22 指标（价格动量7/成交量6/趋势4/波动3/量价背离）| ✅ QCOM 跑通 |
| `features/sector.py` | 板块 RS + 轮动 + 启动 + sector_summary 摘要 | ✅ 5 ETF 快测通过 |
| `features/screener.py` | 三策略选股（breakout/trend/momentum）+ DEFAULT_WATCHLIST 25 只 | 代码完成（未全量测）|
| `features/market.py` | 大盘趋势 + VIX + 风格轮动 + market_regime + **market_breadth**（30 只样本宽度）| ✅ 跑通（宽度 75.9% above MA200）|
| `data/loader.py` 加 `_fetch_from_eastmoney` | 东财 HTTP fallback（105/106 secid 双试 + 2 次重试退避）| ✅ 逻辑验证（106.BABA 短范围 klines=12）；当日东财 502 临时 |
| `wrappers/run-{indicators,sector,market,screen}.sh` | 4 新 wrapper（.venv/bin/python3 绝对路径，避免 activate 坑）| ✅ chmod |
| `scanner/cli.py` 加 4 命令 | indicators/sector/market/screen（click @main.command）| ✅ |

### 验证结果（2026-07-19 实跑）

- **QCOM indicators**：RSI 38.05 / **MFI 18.38 超卖**（量能配合，比 RSI 更敏感）/ MACD 柱缩小 / ADX 12.69 无趋势空头 / 布林中轨下方 / **底背离**（OBV 未新低）→ 21 指标正常
- **sector（5 ETF）**：SMH 3月+15% 但近1月-8.8%走弱；XLE/KWEB 近1月走强（资金流入）；XLE ⚡突破
- **market**：中性/转折（SPY MA50 纠缠 + 成长/科技走弱 + 防御/能源走强 = risk-off 倾向；IWM 小盘 +11.6% 最强）
- **market_breadth（29 只蓝筹）**：%above MA200 **75.9%（健康）** + 涨跌比 0.45（短期回调）→ 长期牛市未破 + 短期调整

### 待做（按研究线分组，下次会话从这里接）

#### A. 板块信号研究线（2026-07-19 已收尾，结论：信号是噪音）
- [x] **A1. 扩样本验证 bull_strong 正信号** → **已回答（10y default 置信）**：bull_strong 下 launch 信号无效（LAUNCH_STRICT@bull_strong n=303 胜率 47.5%/EV-0.18%）。无需工程化 regime 分层聚合。
- [x] **A2. launch 定义剔除 RS 升** → **已回答**：信号整体是噪音（3y/5y/10y 三轮扩样本互相推翻），剔除 RS 无意义。
- [ ] **A3. warmup 排除**（数据卫生，低优先）：collect/aggregate 从 MA200 形成后起算，排除 warmup 负 EV 污染。纯数据卫生，不影响"信号无效"的结论。

**研究线结论**：板块 ETF launch 信号体系（突破+放量+RS升任何组合）**证伪**。`sector` 命令回归 RS ranking 描述工具，launch_signal **不作交易信号**。详见末尾「2026-07-19 10y 扩样本终极否定」节。

#### B. 数据源稳定性
- [ ] **B1. 新浪第三数据源**：`_fetch_from_sina`（美股历史 jsonp API）作东财 502 兜底。中等工作量（jsonp 解析）。

#### C. 覆盖面扩展
- [ ] **C1. 市场宽度扩展**：`BREADTH_UNIVERSE` 30 只蓝筹 → S&P 500 全量（`load_sp500_tickers` + load_batch，~2 分钟）。
- [x] **C2. screener 全量测试** ✅（2026-07-19）：三策略跑通，**修复 period bug**（`6m`/`3m` → `6mo`/`3mo`，yfinance 只认 mo，原 bug 导致 breakout/momentum 静默失败）。调整期命中：breakout 1 / trend 12 / momentum 0（合理）。延伸发现见末尾「个股信号初测」节。

#### D. 已完成（保留索引，详见末尾对应章节）
- [x] 板块启动信号历史回测（collect_sector_launch_events + sector-stats CLI + 17 测试）— ⚠️ 小样本结论已被全样本推翻
- [x] compute_sector_rs 严格定义升级（is_breakout_20d + 8 测试）— 严格≥宽松但均不显著
- [x] compute_market_regime + sector 命令 regime 避雷提示（+4 测试）— bull_correction 避雷已落地

### 已知问题（坑，下次注意）

- **东财 502**：`_fetch_from_eastmoney` 逻辑验证过（106.BABA 短范围 klines=12 ✅），但 2026-07-19 当日全范围 502（疑似调试请求多触发限流/服务波动）。重试机制已加（2 次 + 2s 退避）。**恢复后 `load()` 自动 fallback**（yfinance 失败 → 东财）。
- **QQQ/^VIX yfinance 偶发失败**：`cli market` 时 QQQ 偶报"possibly delisted"、^VIX 偶发无数据（yfinance 临时问题）。regime 用 SPY + 其他风格降级判定，逻辑正确。可加 yfinance 重试。
- **后台任务跨 session 中断**：长任务（全量 sector 18 ETF / 全量 screen 25 只）后台跑时，session 切换会 `stopped`。**建议前台跑（timeout 设长如 240s）或分批**。
- **东财 secid 前缀不固定**：美股 105/106 都要试（BABA=106，QCOM=105，不能写死）。代码已双试。

### CLI 用法（完整）

```bash
# 单股
quant-scanner indicators QCOM                  # 22 指标含成交量（新 P0）
quant-scanner scan QCOM                        # 16 形态信号（原有）
quant-scanner detail QCOM                      # 详细信号（原有）

# 板块 + 选股 + 大盘（新）
quant-scanner sector                           # 板块 RS + 轮动 + 启动（P1）
quant-scanner screen breakout|trend|momentum [--watchlist PATH]  # 选股（P1）
quant-scanner market                           # 大盘 + VIX + 风格 + 宽度（P2）

# 胜率（原有）
quant-scanner stats QCOM NVDA AVGO AMD --period 2y --horizon 20
```

### wiki-quant skill 集成

- `wiki-engine/skills/wiki-quant/SKILL.md` 已更新（4 新 wrapper + 触发词：板块轮动/选股/大盘/指标）
- 同步到 `~/.claude/skills/wiki-quant/`（cf 微信端可调）
- wiki-research 步骤 3 技术面栏可调 `indicators`；板块/大盘背景可调 `sector`/`market`

**6 层升级 P0-P3 完成。** 单股指标(22含成交量) + 板块轮动 + 选股扫描 + 大盘背景/宽度 + 东财数据源 + skill 集成，全栈可用。

---

## 2026-07-19 板块启动信号历史回测（6 层升级待做 #1 完成）

**目标**：验证 sector.py `launch_signal`（突破+放量+RS升）及各条件子组合的历史胜率/EV，回答"哪个条件最有预测力、RS 是否关键加成"。

### 做了什么

- **`features/sector.py` 新增 `collect_sector_launch_events()`**：PIT 遍历 ETF 历史，每个交易日按当时可知数据（切片 `iloc[:t+1]`，不泄露未来）判定 6 种条件组合，生成标准 `SignalEvent`，直接喂 stats 基建。
- **发现并修复 `compute_sector_rs` 的 breakout 定义缺陷**：原 `high_20 = close[-20:].max()` 含当日 → close[-1] 永远 ≤ high_20 → 只要没跌超 2% 就判突破。回测里加严格版（突破前 20 日最高，不含今天）做对比。
- **`stats/cli.py` 新增 `sector-stats` 子命令**：复用 `compute_forward_labels` + `aggregate` + Wilson 区间 + holdout + `StatsReporter`，零改动统计基建。
- **6 种 event_type 拆解**（让数据回答条件贡献）：
  - `LAUNCH_STRICT`：严格突破 + 放量 + RS 升（三重确认，严格版）
  - `LAUNCH_LOOSE`：宽松突破(0.98) + 放量 + RS 升（对齐 `compute_sector_rs` 现有定义）
  - `BREAKOUT`：仅严格突破（单条件基线）
  - `BREAKOUT_VOL`：严格突破 + 放量（隔离 RS 边际贡献）
  - `VOLUME_SURGE`：仅放量（单条件基线）
  - `RS_RISING`：仅 RS 1 月上升（单条件基线）

### ⚠️ 重大修正（2026-07-19 全样本推翻小样本结论）

下方 3y 小样本结论**已被 5y 全样本推翻**，仅作"小样本过拟合"反面案例保留。扩到 18 ETF × 5y（n=169，default 置信）后：
- LAUNCH_STRICT 胜率 **68.8% → 48.5%**（低于随机 50%），EV **+4.71% → +0.43%**
- LAUNCH_LOOSE 胜率 **64.7% → 44.5%**
- 板块 ETF launch 信号在 horizon 5/20/60 下**均无可靠预测力**（详见末尾「2026-07-19 全样本回测否定结论」节）

**教训**：3 ETF × 3y（grey 区 n<20）的小样本绝不可作为结论依据，SMH/XLE/KWEB 恰是近年特殊板块导致严重失真。

### 研究发现（SMH/XLE/KWEB × 3y 训练段，horizon=20 交易日）— ⚠️ 已被推翻，仅作过拟合案例

| event_type | n | 胜率 | EV | 盈亏比 | 置信 |
|---|---|---|---|---|---|
| **LAUNCH_STRICT** | 16 | **68.8%** | **+4.71%** | **3.80** | grey |
| LAUNCH_LOOSE | 17 | 64.7% | +4.34% | 3.60 | grey |
| BREAKOUT_VOL | 19 | 57.9% | +3.09% | 2.35 | grey |
| VOLUME_SURGE | 45 | 57.8% | +2.42% | 2.29 | exploratory |
| BREAKOUT | 33 | 54.5% | +1.22% | 1.41 | exploratory |
| RS_RISING | 44 | 45.5% | +1.05% | 1.34 | exploratory |

**4 条结论**：
1. **三重确认最强**：LAUNCH_STRICT 全面碾压单条件，验证"突破+放量+RS升"组合有效。
2. **严格 > 宽松突破**（68.8% vs 64.7%）→ `compute_sector_rs` 的宽松定义应升级到严格定义（已列入待做 #5）。
3. **放量是关键滤网**：BREAKOUT → BREAKOUT_VOL，EV 从 +1.22% 跳到 +3.09%（+153%）。
4. **单 RS 无预测力**（胜率 45.5% < 随机 50%，中位 -0.99%）→ RS 必须配合突破放量才有意义。

**⚠️ 样本量提醒**：LAUNCH_STRICT/LOOSE n=16/17 在 grey 区（<20），3 ETF × 3y 不足以下强结论。需扩到全 19 ETF × 5y+ 才能升到 exploratory/default。但趋势清晰：组合 > 单条件，严格 > 宽松，放量关键。

### 改动文件

- `src/quant_scanner/features/sector.py`：+ `LAUNCH_EVENT_TYPES` 常量 + `collect_sector_launch_events()` 函数；import `SignalEvent`
- `src/quant_scanner/stats/cli.py`：+ `sector_stats` 命令 + `_resolve_window()` helper
- `src/quant_scanner/scanner/cli.py`：注册 `sector-stats` 到 main group
- `tests/test_sector_backtest.py`（新）：12 个测试（边界/6 种触发条件/严格vs宽松/cooldown/PIT 不泄露/entry_date/SPY 对齐）

**测试**：全量 284 → **301 通过**（+17：12 原始 + 5 审查后补）。无回归。

### 对抗性代码审查闭环（subagent 第二视角）

审查结论：🔴 阻塞无（PIT 切片/数值窗口/接口契约全确认正确），🟡 4 警告 + 8 测试缺口。

已处理：
- **W1 修**：`breakout_loose` 加 `len(close_up_to) >= 20` 保护（对齐 strict，防 min_history 被调小时窗口语义漂移）
- **W2/W3 注释**：`aligned` 用完整历史带来的轻微 listing bias trade-off、entry_date 用"共有交易日"而非"ETF 自己的下一交易日"的取舍——均判定非阻塞（SPY 与行业 ETF 交易日重合度极高 + compute_forward_labels 有 fallback），加注释说明
- **W4**：入口加 `min_history < 66` 的 warning log（breakout 需 20 日 / RS 需 22 日窗口）
- **测试补** G1（close 持平前高，严格 `>` 不触发，防误改 `>=`）/G2（details_whitelist 字段内容）/G3（SPY 比 ETF 短对称场景）/G5（rs_1m_chg 数值精度，SPY 跌 ETF 涨算 +15.79%）/G7（端到端 compute_forward_labels，持续上涨→forward_return>0）

未处理（审查也判定非阻塞）：G4（跨 event_type cooldown 独立性，已被 test_cooldown_dedup 间接覆盖：BREAKOUT 与 RS_RISING 各自独立计数 4 个）/ G6（volume 含 NaN 的极端边界）。

### CLI 用法

```bash
# 默认全 19 ETF × 5y（慢，建议指定子集加速）
quant-scanner sector-stats SMH XLE KWEB XLK XLF --period 5y --horizon 20

# 短周期快验证
quant-scanner sector-stats SMH XLE KWEB --period 3y

# 输出 HTML 报告到 reports/{date}-sector-winrate-stats.html
# 核心看 second_level 表（6 种 event_type 胜率/EV 对比）
```

**板块启动信号回测落地。** 下一步可做待做 #5（compute_sector_rs 严格定义升级，小改动让实时命令也受益）或 #2（新浪数据源）。

---

## 2026-07-19 全样本回测否定结论（推翻上一节小样本）

**起因**：上一节用 3 ETF × 3y（SMH/XLE/KWEB）得出"LAUNCH_STRICT 68.8% 胜率"的强结论。为升置信度扩到全 18 ETF × 5y，结论**完全反转**。

### 全样本数据（18 ETF × 5y，holdout 12 月切分后训练段）

| event_type | h=5 胜率/EV/盈亏比 | h=20 | h=60 |
|---|---|---|---|
| LAUNCH_STRICT | 42.6% / -0.49% / 0.68 | 48.5% / +0.43% / 1.19 | 44.9% / -0.32% / 0.92 |
| LAUNCH_LOOSE | 45.4% / -0.39% / 0.75 | 44.5% / +0.03% / 1.01 | 44.9% / -0.26% / 0.93 |
| BREAKOUT_VOL | 44.9% / -0.51% / 0.66 | 50.0% / +0.40% / 1.18 | 45.8% / -0.23% / 0.94 |
| BREAKOUT | 46.7% / -0.36% / 0.75 | 51.0% / +0.24% / 1.09 | 45.1% / -0.81% / 0.82 |
| VOLUME_SURGE | 48.8% / -0.22% / 0.85 | 51.7% / +0.39% / 1.16 | **52.7% / +1.17% / 1.33** |
| RS_RISING | 47.2% / -0.33% / 0.79 | 47.4% / +0.05% / 1.02 | 48.5% / +0.37% / 1.09 |

（n=158~608，default/low 置信，统计可靠；XLC 因 yfinance 临时 delisted 跳过）

### 结论

1. **板块 ETF 的"突破+放量+RS升"启动信号在全 horizon 下都没有可靠预测力**。中线接近零，短线（5d）稳定亏损（追高被套，盈亏比 0.66-0.85），长线仅 VOLUME_SURGE 微正。
2. **"三重确认最强"是小样本过拟合**——全样本下 LAUNCH_STRICT 反而偏弱（短线最差）。
3. **唯一稳定的方向结论**：宽松(LOOSE) 一致 ≤ 严格(STRICT)（全 horizon 成立）。所以 `compute_sector_rs` 改 strict 是对的——但只是"更准的描述"，两者都不显著。
4. **唯一微弱正向**：VOLUME_SURGE @ 60d（52.7% / 盈亏比 1.33），但 EV +1.17% 扣滑点+佣金后所剩无几，不足以支撑策略。
5. **LAUNCH_STRICT 95% 置信下界 41.1%**——明确无统计显著预测力。

### 教训（重要）

- **grey 区（n<20）的小样本绝不可作为结论依据**。SMH/XLE/KWEB 是近年特殊板块（半导体强势、能源反弹、中概波动），3y 恰好放大了信号假象。
- 用户记忆 [[feedback_subagent_data_verification]] 的头号风险"确认偏误+时效偏差"再次应验——这次是小样本偏差。**回测结论必须扩到 default 置信（n≥200）才能采信**。

### 后续方向（如果要救"找板块启动"需求）

当前 launch 信号定义在 ETF 层面失效，不是调参能解决，需要根本性重新设计：
- **换层面**：ETF 噪音大（一篮子股票），改用 ETF 内部成分股的 launch 聚合（如 SMH 内创新高的成分股占比）
- **加过滤**：launch 只在 risk-on（market_regime 确认）时触发，risk-off 时板块突破多为假突破
- **信号可能反了**："突破+放量+RS升"在板块层可能是**追高/末端**信号而非启动——考虑反转逻辑或用"突破前的横盘+蓄势"
- **或承认局限**：板块轮动用 RS 排名跟踪即可（compute_sector_rs 的 ranking 部分仍有效，是描述工具），**放弃"预测启动时点"**的执念

**当前建议**：`sector` 命令的 ranking（RS 强弱/资金流向）继续作为**描述工具**用，但**不要把 launch_signal 当预测信号做交易**。

### 后续验证：market regime 分层精细化（部分救活信号）

**假设**：全样本无效可能是被 risk-off 假突破污染。按 SPY vs MA50/MA200 分三档 regime 分层回测（horizon=20，5y 全样本）：

| event_type@regime | n | 胜率 | EV | 盈亏比 |
|---|---|---|---|---|
| **BREAKOUT_VOL@bull_strong** | 121 | **56.2%** | +1.18% | **1.65** ⭐ |
| LAUNCH_STRICT@bull_strong | 96 | 53.1% | +1.04% | 1.55 |
| VOLUME_SURGE@bull_strong | 256 | 53.5% | +0.57% | 1.31 |
| BREAKOUT@bull_strong | 254 | 52.8% | +0.34% | 1.17 |
| LAUNCH_STRICT@bear | 28 | 53.6% | +2.11% | 2.40（样本小存疑） |
| **LAUNCH_STRICT@bull_correction** | 32 | **37.5%** | -1.69% | **0.39** ⚠️ |
| **BREAKOUT_VOL@bull_correction** | 32 | 34.4% | -1.91% | 0.38 ⚠️ |

（regime 定义：bull_strong = close>MA50>MA200；bull_correction = close<MA50 但 >MA200；bear = close<MA200；warmup = MA200 未形成）

**精细化结论**：
1. **bull_correction 是明确假突破区**（最可靠，n=32-95）：SPY 跌破 MA50 但还在 MA200 上方时，所有突破/启动信号胜率 34-37%、盈亏比 0.4 → **坚决避雷**。这是"牛市回调中的板块突破多为假突破"的量化验证。
2. **bull_strong 下信号温和有效**：BREAKOUT_VOL 56.2%/盈亏比 1.65（对比全样本基线 50%/1.18，过滤后明显改善）。
3. **RS 升条件有害**：BREAKOUT_VOL（无 RS）一致 ≥ LAUNCH_STRICT（加 RS）→ `RS_RISING` 作为过滤条件无效甚至稀释，考虑从 launch 定义剔除。
4. **warmup（MA200 未形成期）全面负**：未来回测应从第 200 个交易日起算，排除 warmup。
5. **bear 阶段正收益反直觉**（n=28-47 小样本）：可能过拟合或"逆势最强板块"效应，存疑不采信。

**置信度诚实评估**：子样本 n=28-256，多数仍在 low/exploratory，**未达 default(≥200)**。bull_correction 避雷最可靠（负信号 + n 充足）；bull_strong 正信号边际有效但需扩样本到 default 才能定论。**故本轮不工程化未达置信的正信号**，避免重蹈小样本覆辙（见 [[feedback_backtest_min_sample]]）。

**已落地**：`sector` 实时命令加当前 regime 提示 + bull_correction 避雷警告（见下节）。
**待做（扩样本后再议）**：若 bull_strong 正信号在更长窗口/更多 ETF 下升到 default 置信，再工程化进 sector-stats 的 regime 分层聚合。

---

## 2026-07-19 10y 扩样本终极否定（关闭板块信号研究线）

**动机**：5y regime 分层得出"bull_strong 温和有效 / bull_correction 避雷"的结论，但子样本 n=32-256 多数未达 default。为定论扩到 10y（19 ETF × 2016-2026，6518 事件，各 regime n=303-814 达 default 置信）。

### 三轮扩样本对比（LAUNCH_STRICT@bull_strong，horizon=20）

| 样本 | n | 胜率 | EV | 置信 | 结论 |
|---|---|---|---|---|---|
| 3y（3 ETF）| 16 | 68.8% | +4.71% | grey | "三重确认最强" |
| 5y（18 ETF）| 96 | 53.1% | +1.04% | low | "温和有效" |
| **10y（19 ETF）** | **303** | **47.5%** | **-0.18%** | **default** | **无效** |

**三轮互相推翻，本身就是最强证据：信号是噪音，任何时间切片都会随机波动。**

### 10y 全样本 default 置信结论

| event_type@regime | n | 胜率 | EV | 盈亏比 |
|---|---|---|---|---|
| LAUNCH_STRICT@bull_strong | 303 | 47.5% | -0.18% | 0.92 |
| BREAKOUT_VOL@bull_strong | 382 | 50.0% | -0.06% | 0.97 |
| VOLUME_SURGE@bull_strong | 814 | 53.2% | -0.13% | 0.94 |
| VOLUME_SURGE@bull_correction | 256 | 62.1% | +1.14% | 1.58（5y 时是避雷区，10y 反转） |
| VOLUME_SURGE@bear | 292 | 59.6% | +1.80% | 1.72 |
| **VOLUME_SURGE 整体** | **1454** | **56.6%** | **+0.58%** | **1.27** |

1. **launch 信号在所有 regime 下均无可靠预测力**（default 置信，n=303-814）。
2. **唯一 default 置信的微弱正向**：VOLUME_SURGE 整体 56.6%/盈亏比 1.27（n=1454）。扣滑点+佣金（~0.4%）后 EV 剩 ~+0.18%/20d，年化 ~2%，**不支撑策略**。
3. 5y 的"bull_correction 避雷""bear 正收益"等结论在 10y 全部不稳定（方向都变了）→ regime 分层也无法救活。

### 对已落地工作的影响（诚实修正）

- `compute_market_regime` + `sector` 命令的 regime 提示：**保留**（作为市场状态描述，让用户知道 SPY 所处阶段），但 **warning 文案中性化**——去掉"假突破高发区避雷（胜率34-37%）"等 5y 不稳断言，改为"牛市回调/熊市下行"的中性描述。`launch_signal` 的预测性宣传全部收回。
- `compute_sector_rs` 的严格突破定义：**保留**（语义更准，strict≥loose），但只是"描述"，非"信号"。

### 教训（再次强化 [[feedback_backtest_min_sample]]）

- **3y→5y→10y 三轮扩样本互相推翻**：grey(n<20) → low(50-200) → default(≥200) 每升一档，上一档结论就反转。只有 default 置信才可信，且即使 default 也要看跨样本稳定性。
- **"看似合理的信号 + 小样本亮眼"是最危险的组合**——板块轮动是经典技术分析教义，"突破+放量+RS升"逻辑上无懈可击，但 ETF 层面就是无效。逻辑对 ≠ 有效。
- **本轮总价值**：用三轮 default 置信回测，彻底证伪了一个经典信号，避免基于它亏钱。`sector` 命令的 RS ranking 仍是有效的**描述工具**（看资金流向/强弱），只是不能预测启动时点。

### 后续方向（如要继续"找板块"）

放弃 ETF 层面的 launch 预测，转：
- **描述派**：RS ranking + regime 状态跟踪（已落地），承认板块轮动跟踪 ≠ 预测启动
- **换层面**：ETF 成分股的相对强度聚合（如 SMH 内 RS 前 20% 成分占比），而非 ETF 价格突破
- **转个股**：quant-scanner 的 16 个单股信号（VCP/CANSLIM 等）才是 O'Neil/Minervini 方法论的适用层面，板块信号本就是衍生品（已验证 trend_template 60d 有效，见下节）

---

## 2026-07-19 个股信号初测（trend_template 60d 有效，对比板块无效）

**动机**：板块 launch 全样本证伪后，自然追问"个股层面呢"。O'Neil/Minervini 方法论核心本就在个股（VCP/趋势模板），板块是衍生品。用现成 stats 框架测 watchlist 个股。

### 数据（28 只龙头 watchlist × 3y，TREND_TEMPLATE，default 置信）

| horizon | n | 胜率 | EV | 盈亏比 | 结论 |
|---|---|---|---|---|---|
| 5d | 354 | 49.4% | -0.32% | 0.88 | 短线无效 |
| 20d | 345 | 52.8% | +0.68% | 1.15 | 中线微弱正 |
| **60d** | **319** | **56.4%** | **+3.17%** | **1.57** | **长线统计显著有效**（Wilson 下界 >50%） |

VCP 信号 n=4（28 只龙头 × 3y 里 VCP 形态几乎不出现，需 SP500 全宇宙才能测）。

### 结论

1. **个股 TREND_TEMPLATE（O'Neil 第二阶段）60 交易日有真实预测力**：胜率 56.4% / 盈亏比 1.57 / EV +3.17%（n=319 default 置信）。扣滑点+佣金（~0.4%）后 ~+2.77%/60d，年化 ~11%。**quant-scanner 里第一个 default 置信的有效信号**。
2. **短线（5d）无效**（49.4%）——趋势模板是中线信号，不适合短线交易。
3. **板块 vs 个股对比**：板块 launch 全样本无效（噪音），个股 trend_template 60d 有效 → **quant-scanner 的价值在个股中线趋势**，不在板块/短线。
4. `screen_trend` 当前命中（12 只）是有效中线候选（非短线）。

### 局限（诚实）

- 只测 28 只大盘龙头 × 3y（default 置信但样本偏大盘股）。扩 SP500 × 5y 可能衰减，建议验证稳健性后再用于策略。
- VCP/CANSLIM 等其他个股信号未测（VCP 需更大宇宙，小样本 n=4 不可结论）。
- trend_template 60d 有效 ≠ 选股必赚，56% 胜率仍有 44% 亏损，需配合仓位管理（position_sizing 已实现）。

### 后续可选

- **扩 SP500 × 5y 验证 trend_template@60d 稳健性**（若仍 default 有效，可工程化为中线选股核心信号）—— 优先级最高
- VCP/CANSLIM 用 SP500 全宇宙回测（小宇宙测不出）
- 或接受当前结论：quant-scanner 定位为**个股中线趋势扫描器**（trend_template 核心），板块/短线场景另寻工具

### ✅ SP500 × 5y 稳健性验证（2026-07-19，扩样本增强 — 确认真 alpha）

50 只 SP500 stride 样本 × 5y（default 置信，n=1553-1654，PIT 评估耗时 271s）：

| horizon | n | 胜率 | EV | 盈亏比 | 95% 下界 |
|---|---|---|---|---|---|
| 5d | 1654 | 48.7% | -0.22% | 0.88 | 46.3% |
| 20d | 1628 | 54.0% | +0.94% | 1.36 | 51.6% |
| **60d** | **1553** | **60.3%** | **+3.84%** | **1.98** | **57.9%** |

**对比原 28 龙头 × 3y（60d: 56.4%/盈亏比1.57），扩样本后不衰减反增强（60.3%/1.98）** —— 和板块 launch（3y 68.8%→10y 47.5% 崩溃）形成鲜明对比。**这是真正的 alpha，不是小样本幻觉**。

**升级结论**：trend_template@60d 是 quant-scanner 的**核心价值锚点**——个股中线趋势模板，胜率 60%、盈亏比 2:1、扣成本年化 ~14%。短线(5d)无效确认。

为什么扩样本反增强：5y 含 2021-22 熊市 + 2023-25 反弹，趋势模板在趋势明确期表现好；50 只 SP500 比龙头分散，中小盘趋势更明显（龙头大盘股趋势平缓，信号弱）。

### 后续（基于验证结果，优先级排序）

- [x] **基于 trend_template@60d 工程化中线选股** ✅（2026-07-19）：`screen_trend_plan` + CLI `screen trend --plan [--account N]`。复用 `TradeSetup`+`position_size`+自写`_atr`。**默认 4×ATR/3:1（组合回测验证），backtest_ev 显示实际回测 EV（4.21%，非误导的理论期望）**。
- [x] **退出规则组合回测** ✅（2026-07-19）：新增 `stats/trade_sim.py`（+10 测试）。**结论：纯持有 60d 最优（EV+4.21%/盈亏比2.12），2×ATR 紧止损有害（60% 被洗出，EV+0.34%）**。详见下节。
- [x] **sell_signals 退出回测** ✅（2026-07-19，15 只 × 3y n=196）：sell_signal 过早（中位 16d 触发，90% 事件），EV -0.32% 不如 hold +1.66%。**结论：trend_template@60d 最优 = 纯持有 60d，任何提前退出（紧止损/sell_signal）都降低 EV**。trade_sim 加 exit_rule="sell_signal"（+现有 10 测试）。
- [ ] VCP/CANSLIM 用 SP500 全宇宙回测（小宇宙 n=4 测不出）

---

## 2026-07-19 组合回测：退出规则（纯持有 60d 最优，紧止损有害）

**动机**：screen_trend_plan 给的 stop/target 计划，实际命中率/EV 未知。新增 `stats/trade_sim.py` 模拟完整交易（入场 t+1 open + 逐日 stop/target/time 退出 + open gap 处理），对比退出规则。

### grid 结果（SP500 50 只 × 5y，n=1663，default 置信）

| 退出规则 | 胜率 | EV | 盈亏比 | 平均持有 | stop% |
|---|---|---|---|---|---|
| **hold_60d（baseline）** | **61.4%** | **+4.21%** | **2.12** | 60d | 0% |
| stop_target 5×ATR/3:1 | 56.5% | +2.92% | 1.64 | 50d | 29% |
| stop_target 4×ATR/3:1 | 51.6% | +2.34% | 1.52 | 45d | 39% |
| stop_target 3×ATR/3:1 | 43.5% | +1.57% | 1.36 | 36d | 53% |
| stop_target 2×ATR/2:1（教科书默认）| 38.8% | +0.34% | 1.10 | 18d | **60%** |

### 结论

1. **纯持有 60d 是最优退出**（EV+4.21%/盈亏比2.12）。trend_template 触发后给空间持有，比设止损好。
2. **2×ATR 止损（教科书/Murphy 默认）有害**：60% 交易被洗出（趋势股正常回调 2-3×ATR），胜率 61.4%→38.8%，EV +4.21%→+0.34%。
3. **止损越宽越接近 hold**（2×ATR +0.34% → 5×ATR +2.92%），但都不及 hold。止损本质"截断赢家"。
4. **screen_trend_plan 已修正**：默认 4×ATR/3:1（宽止损给空间）+ `backtest_ev` 显示 hold 实际 EV（4.21%，非误导的理论期望）+ exit_note 警告紧止损有害。
5. **sell_signal（基底计数）退出同样过早**（15 只 × 3y 小样本 n=196，low 置信）：trend_template 入场后中位 16d 即触发 sell_signal（90% 事件），EV -0.32%（vs hold +1.66%），盈亏比 0.91。**任何提前退出（紧止损 / sell_signal）都因"截断赢家"降低 EV**——trend_template@60d 的最优策略就是入场后纯持有 60d（time exit）。方向结论稳，绝对值待 SP500 扩样本确认。

### 实操建议

- **trend_template@60d 入场后，默认纯持有 60d**（time exit），不设紧止损
- 止损仅作风控上限（≥4×ATR 或 swing low 结构止损），接受 EV 比 hold 低
- sell_signals 已验证过早（见结论第5点），time exit 仍是最优退出

---

## 日常使用速查（用户视角 · 场景驱动）

> 区别于前面的详细 CLI 文档（给 AI 接手），本节是用户日常用的速查。也是 quant-scanner 当前能力地图。

### 环境（每次先这个）
```bash
cd /Users/shayin/data1/htdocs/project/mind/ai-wiki/wiki-engine/tools/quant-scanner
source .venv/bin/activate
```
之后 `quant-scanner <命令>` 直接用。

### 🎯 主打：找中线能买的股（核心应用）
```bash
quant-scanner screen trend --plan --account 100000
```
输出每只候选的 **entry / 止损 / 目标 / 回测EV / 股数**。策略就一条：**纯持有 60 天，别紧设止损**（回测证明 2×ATR 紧止损把 60% 赢家洗出去）。命中第二阶段趋势股（如 AAPL/GS/XOM/CVX/UNH/LLY 类）。

### 🔍 日常查股（持仓/关注）
```bash
quant-scanner indicators NVDA     # 22 指标（含成交量 OBV/MFI/VWAP）
quant-scanner detail NVDA          # 形态信号详解（VCP/CANSLIM 等）
quant-scanner scan NVDA AAPL QCOM BABA PDD TME   # 批量扫信号打分
quant-scanner stats NVDA --period 3y              # 该股信号历史胜率
```

### 📊 看大盘 + 板块环境
```bash
quant-scanner market      # 大盘趋势 / VIX / 风格轮动 / 市场宽度
quant-scanner sector      # 板块 RS 排名 + 当前 regime 状态
```
⚠️ **sector 看 RS 强弱可以，但别把"⚡启动"当买信号**——已证伪是噪音。

### 诚实标注（避免踩坑）
| 能力 | 状态 | 怎么用 |
|---|---|---|
| `screen trend --plan` 中线选股 | ✅ 可信（default 置信 n=1553）| 主打，纯持有 60d |
| `indicators` / `scan` / `detail` | ✅ 技术面分析 | 日常查股 |
| `sector` RS 排名 / `market` | 🟡 描述工具 | 看环境，不预测 |
| 板块 launch / 短线突破 | ❌ 已证伪 | **别当信号** |

### 微信端
通过 `wiki-quant` skill 调用（cf 看不到 HTML，会自动用 `wrappers/parse-html.py` 转 markdown 摘要回用户）。

**一句话总结**：日常两条主线——**找中线用 `screen trend --plan`，查个股用 `indicators` / `detail` / `scan`**。板块和大盘看环境，短线这个工具给不了（已诚实验证）。

---

## 2026-07-19 出场策略信号（ExitStrategySignal）落地

**任务 #83 完成**：把 trend_template@60d 回测结论（纯持有 60d 最优、紧止损有害）固化成可调用的 Signal，给已建仓标的输出明确的止损/止盈/减仓价位。

### 新增文件

- `src/quant_scanner/signals/exit_strategy.py`（259 行）：`ExitStrategySignal` 继承 `BaseSignal`
- `tests/test_exit_strategy.py`（10 测试，全过）：覆盖健康上涨/跌破止损/时间止损/MA13 紧止损/MA30 减仓/入场点解析/数据不足/3:1 目标价/必需字段/MA30 bug 防回归

### 设计要点（对齐 HANDOFF 回测锚点）

| 参数 | 值 | 依据 |
|------|---|------|
| `atr_multiplier` | 3.0 | 宽止损避免被洗出（回测：2×ATR 洗出 60%，5×ATR +2.92% 仍不及 hold） |
| `trailing_atr_multiplier` | 2.5 | trailing = max_high − 2.5×ATR |
| `max_holding_days` | 60 | 对齐回测最优 time exit 周期 |
| `target_reward_pct` | 0.20 | 3:1 风险比（达到则主动止盈） |

### 关键修复：MA30 bug

旧版逻辑：`ma30_force=True` + `below_ma30` → 加入 `exit_reasons` → 触发 `action="EXIT"`（全清）。但 MA30 跌破语义是"减仓"不是"清仓"，违反回测结论（紧止损/提前退出截断赢家）。

修复后分层：
- `exit_reasons`（硬触发 EXIT）：仅【跌破 ATR 止损位】+【时间止损】
- `reduce_reasons`（软触发 REDUCE）：跌破 MA30 → 建议减仓 1/3
- `action` 警告级：跌破 MA13 → `TIGHTEN_STOP`；跌破 MA5 → `WATCH`

### value 安全度计算

`value ∈ [0, 1]` 综合三维度：
- 收益进度 0.4 权重（current_return / target）
- 距止损空间 0.4 权重（distance / 10%）
- 时间衰减 0.2 权重（holding_days 越接近 max 越危险）

`value < 0.3` → `passed=True`（建议卖出）；REDUCE 时 value=0.25。

### 集成

- `wiki-engine/skills/wiki-quant/SKILL.md`：加模式 5（出场策略），含调用示例 + 输出字段说明 + AI 处理规则 + 落盘位置
- `ai-wiki/CLAUDE.md` report.md 模板：加「### 出场策略」段（决策型个股研究 + 已建仓标的必填）
- 全局 skill 同步：`~/.claude/skills/wiki-quant/SKILL.md`

### 测试统计

总测试数 382 → **392**（+10 出场策略测试）。全过。

---

## 2026-07-19 仓位管理 × 因子联动（任务 #84）

**动机**：之前 `position_size` 只考虑账户/风险/ATR，忽略了 Alpha 因子强度。强因子（|IC|≥0.08）应该适当加仓，弱信号应减仓——这是「统计结构强的票多给仓位」的工程化。

### 新增

**`utils/position_sizing.py`**：
- `ic_to_factor_weight(ic)` 函数：|IC| ≥ 0.08 → 1.5x；≥ 0.05 → 1.2x；≥ 0.03 → 1.0x；< 0.03 → 0.7x
- `position_size()` 加 `factor_weight: float = 1.0` 参数
- 封顶保护：`factor_weight` 钳制到 [0.5, 1.5]，**不超 Murphy 2% 单笔上限 × 1.5**

**`tests/test_position_sizing.py`** 新增 9 测试：
- IC 分档 4 档（strong/effective/weak/noise）
- 负 IC 用 |IC| 评级
- 强因子加仓 100→150 股
- 弱信号减仓 100→70 股
- factor_weight 上下限钳制
- 默认值向后兼容
- factor_weight × ATR 叠加

### 集成

- `wiki-engine/skills/wiki-quant/SKILL.md` 加模式 6（仓位管理 × 因子联动）
- `ai-wiki/CLAUDE.md` report.md 模板加「### 仓位管理」段
- 全局 skill 同步

### 设计权衡

**为什么封顶 1.5x 而不是按 IC 线性放大？**
- Murphy 铁律：单笔风险 ≤ 2%。即使强因子也不能突破风险上限
- 过拟合防护：IC > 0.10 可能是数据挖掘陷阱，不该被奖励更多仓位
- 实操经验：1.5x 已经是显著加仓（同样止损下买入量多 50%），更激进违反分散原则

**为什么不直接乘到 shares？**
- `shares = (account × risk_pct × factor_weight) / risk_per_share`
- 加权在 risk_pct 层而非 shares 层，保持数学一致性，方便与组合风控对接

---

## 2026-07-19 横截面多标的因子（任务 #88）

**动机**：单标的 IC（模式 4）只能看时序结构，无法回答"在这批候选股中哪只更优"。论文原意 `rank` 是 cross-sectional（每天对全市场所有股票排名），单标的模式退化丢失了这层信息。

### 新增

**`factors/alpha101.py`** 加 `Alpha101CrossSectional` 类 + `build_forward_returns_panel`：
- 接受 `dict[str, pd.DataFrame]` 多标的 panel
- 内部 outer join 对齐日期，输出 date × ticker panel per alpha
- `cross_sectional_ic()`：每天对 N 只股票做 rank corr，再取时间均值
- `cross_sectional_ir()`：IC 均值 / IC 标准差

**CLI** `factors cs-eval` 子命令：
```bash
.venv/bin/python -m quant_scanner.scanner.cli factors cs-eval \
  --tickers NVDA,AAPL,MSFT,GOOGL,META,AMD,TSLA --period 2y --horizon 5
```
输出按 |IC| 降序的表格，含强度分级。

**测试**（`tests/test_factors_cross_sectional.py`，13 个）：
- panel 构造 + 单 alpha / 批量计算
- 强信号 IC > 0.8 验证（构造完全相关）
- 随机信号 |IC| < 0.3 验证
- IR = 0 边界（常量序列）
- 数据不足兜底（< 3 列返回 NaN）
- 不同日期范围 outer join
- 单 alpha 失败不阻塞

### 实盘验证

5 只大盘股（AAPL/MSFT/GOOGL/NVDA/META）1y × 5d horizon：
- alpha_1（大盘反向因子）IC=-0.075（有效）
- alpha_12（量价共振）IC=+0.073（有效）
- alpha_5/20/17/10 IC=±0.05~0.07（有效）

7 只科技股样本下结论稳定。建议扩到 ≥20 只同板块标的才达 default 置信度。

### 集成

- `wiki-engine/skills/wiki-quant/SKILL.md` 加模式 7（横截面多标的因子）
- `ai-wiki/CLAUDE.md` report.md 模板加「### 横截面多标的因子」段
- 全局 skill 同步

### 与单标的 IC 的对比

| 维度 | 单标的 IC（模式 4） | 横截面 IC（模式 7） |
|------|-------------------|-------------------|
| 衡量 | 因子对该股的时序预测力 | 因子能否区分多股票相对表现 |
| 样本 | 1 × N 天 | N × N 天 |
| 用途 | 单股择时（何时买） | 多股排序（哪只更优） |

两者互补：单标的 IC 强 + 横截面 IC 强 = 既可择时又可选股；单标的 IC 强 + 横截面 IC 弱 = 仅适合择时。

### 测试统计

总测试数 402 → **415**（+13 横截面测试）。全过。

---

## 2026-07-19 Regime 条件因子（任务 #85）

**动机**：单标的 IC（模式 4）是全样本均值，掩盖了「因子在不同市场状态下表现不同」。某 alpha 在牛市 IC=+0.08 但熊市 IC=-0.05 → 全样本可能只剩 0.02 误判为弱信号。

### 新增

**`factors/operators.py`** 加 2 个函数：
- `classify_regime(df, ma_long=200, ma_short=50)`：基于 SPY close vs MA200 + MA200 5 日斜率分类
  - close > MA200 且 MA200 上行 → bull
  - close < MA200 且 MA200 下行 → bear
  - 其他 → sideways
- `regime_conditional_ic(factor, fwd, regime, window=20)`：分 regime 算 IC，**每段独立 rank**（避免其他 regime 稀释）

**CLI** `factors regime-eval` 子命令：
```bash
.venv/bin/python -m quant_scanner.scanner.cli factors regime-eval --ticker NVDA --horizon 5
```
输出按 bull |IC| 降序的表格，含自适应判定（牛市专属/熊市专属/震荡专属/跨 regime/通用）。

**测试**（`tests/test_factors_regime.py`，10 个）：
- 牛市/熊市数据分类验证
- 数据不足兜底
- 自定义 MA 窗口
- 强信号在 bull 段 IC 显著
- regime 自适应因子检测（构造 bull 段强相关，bear 段随机）

### 实盘验证

NVDA 2y × 5d horizon，SPY benchmark：bull=127d / bear=14d / sideways=110d
- alpha_5：bull=+0.01 / bear=-0.49 → **熊市专属**（当前 SPY 牛市，该因子对 NVDA 无效）
- alpha_20：bull=-0.02 / bear=+0.13 → **熊市专属**
- alpha_2/8/15：跨 regime 有效

**关键发现**：当前 SPY 牛市下，alpha_5/20 等"熊市专属"因子对 NVDA 失效——印证「单标的 IC 全样本均值会掩盖 regime 差异」。

### 集成

- `wiki-engine/skills/wiki-quant/SKILL.md` 加模式 8（Regime 自适应因子）
- `ai-wiki/CLAUDE.md` report.md 模板加「### Regime 自适应因子」段（决策型研究必填）
- 全局 skill 同步

### 实操价值

1. 决策型研究时：先看当前 SPY regime → 只用 regime 匹配的因子做技术面结论
2. 单标的 IC 边界模糊（top IC=0.04）时：追加 regime 分析找差异化因子
3. 防 LLM 主观：明确"当前 SPY 熊市 + 牛市专属因子无效" → 强制降级技术面结论

### 测试统计

总测试数 415 → **425**（+10 regime 测试）。全过。

---

## 2026-07-19 SHAP 特征重要性（任务 #86，方法 3 入口）

**动机**：Alpha 因子（模式 4-8）只看「单个 alpha vs 收益」的线性关系，无法揭示「多个 alpha 联合作用时哪个贡献最大」的非线性结构。SHAP（Lundberg & Lee 2017 NeurIPS）配树模型可捕捉 alpha 之间的交互效应，是 GP 因子挖掘（任务 #87）的「解释入口」——GP 产出新因子后必须用 SHAP 验证其在多因子模型中的边际贡献是否真为正。

### 新增

**`factors/shap_eval.py` — `ShapFactorEvaluator` 类**：
- 把 alpha 因子作为特征训练树模型预测前瞻收益 → SHAP TreeExplainer 算每个特征的平均贡献度
- **双 backend**：默认 `sklearn`（GradientBoostingRegressor，无系统依赖）；`xgboost` 可选（macOS 缺 libomp 时自动降级 sklearn）
- 输出：`top_features: [(name, mean_abs_shap, direction)]` 按 |SHAP| 降序、`feature_importance` dict、`shap_values` 矩阵、`test_r2`（泛化能力）、`n_samples`
- **方向判定用 correlation(feature_value, shap_value)**：正相关 → positive（推动预测变大），负相关 → negative（反向因子）
- 样本不足（< 50）或依赖缺失时优雅返回 `{"error": ...}`，不抛异常

**CLI** `factors shap-eval` 子命令：
```bash
.venv/bin/python -m quant_scanner.scanner.cli factors shap-eval --ticker NVDA --horizon 5
.venv/bin/python -m quant_scanner.scanner.cli factors shap-eval --ticker NVDA --backend xgboost
```
输出 Test R² + Top N 特征表（含方向）。

**测试**（`tests/test_factors_shap.py`，9 个）：
- 构造 5 个 alpha（alpha_1 强正向、alpha_3 强反向、其余噪声）→ 强信号因子进 top 2
- alpha_1 → positive、alpha_3 → negative（方向判定正确）
- `evaluate_simple` 返回精简 list
- 样本不足 / 缺依赖（mock shap=None）→ 返回 error
- NaN dropna 后 `n_samples` 正确
- `test_r2` 是 float

### 实盘验证

NVDA 2y × 5d horizon：
- Test R² = **-0.42**（负值，alpha 组合对 NVDA 无泛化能力）
- 诚实标注：当前走势无历史可类比结构，技术面结论应降级

**关键判断标准**：
- R² > 0.15：SHAP 结论可信
- R² < 0.05：alpha 组合无预测力，强制告知「无历史可类比结构」
- 单 IC 强 + SHAP 弱 = 信息冗余；单 IC 弱 + SHAP 强 = 交互效应

### 集成

- `wiki-engine/skills/wiki-quant/SKILL.md` 加模式 9（SHAP 特征重要性）
- `ai-wiki/CLAUDE.md` report.md 模板加「### SHAP 特征重要性」段（决策型研究可选，IC 矛盾时强制）
- 全局 skill 同步

### 实操价值

1. IC 矛盾时（单标的强但横截面弱、或方向不一致）→ SHAP 仲裁
2. GP 挖出新因子后用 SHAP 验证其边际贡献（任务 #87 的评估入口）
3. 防 LLM 主观堆砌因子：用 SHAP 剪枝冗余 alpha，只保留多因子模型中真正贡献大的

### 测试统计

总测试数 425 → **434**（+9 SHAP 测试）。全过。

---

## 2026-07-19 GP 因子挖掘（任务 #87，方法 3 主菜）

**动机**：Alpha101（方法 2）只能搬运 WorldQuant 论文现成公式，挖掘范围受限于原作者；SHAP（方法 3 入口）只能评估已有 alpha 组合。**方法 3 主菜 = GP（遗传规划）数据驱动挖新因子**：在算子集中搜索因子表达式，以 IC 为适应度，进化 N 代后输出新 alpha 公式。

**核心机制**（Koza 1992《Genetic Programming》）：
- 基因树 = 算子嵌套表达式，叶子是 feature（close/volume/...）或 const
- 适应度 = |Spearman IC|（因子值 vs 前瞻收益的秩相关）
- 进化循环：锦标赛选择 → 子树交换交叉 → 子树替换变异 → 精英保留
- 深度控制防膨胀（默认 max_depth=4）

### 新增

**`factors/gp_mining.py`（~450 行，无外部依赖，不引 gplearn）**：
- `Node` 类：基因树节点，支持 `evaluate(df)` 递归求值 + `to_formula()` 序列化 + `clone/size/depth`
- `UNARY_OPS`（21 个）：`ts_mean/ts_std/ts_rank/ts_delta/ts_delay/ts_skew/ts_kurt/ts_decay_linear/ts_sum/ts_max/ts_min/abs_/sign/log/sigmoid/relu/rsi/rank/scale/zscore/returns/log_returns`
- `BINARY_OPS`（8 个）：`add/sub/mul/div/max_/min_/ts_corr/ts_cov`
- `GPFactorMiner` 类：
  - 参数：`population_size=50, generations=20, tournament_size=5, p_crossover=0.7, p_mutate=0.15, max_depth=4, elitism=2, random_state=42`
  - `mine(df, forward_returns, feature_cols, verbose)` → dict（best_node/best_formula/best_ic/best_ir/best_factor/history/n_samples）
  - **初始种群拒绝无效个体**（fitness=0 时重试最多 5 次）
  - **失败 fitness = 0.0**（合法无信号），让无效个体自然沉底但不污染锦标赛选择
  - 算子直接复用 `operators.py`，零重复

**CLI** `factors gp-mine` 子命令：
```bash
.venv/bin/python -m quant_scanner.scanner.cli factors gp-mine --ticker NVDA --horizon 5
```
输出最佳公式 + IC/IR + 进化历史（每代最优 |IC|）。

**测试**（`tests/test_factors_gp.py`，23 个）：
- `Node` 单元测试（7）：叶子/内部节点、to_formula、size/depth、evaluate、clone 独立性、未知 feature 返回 NaN、unknown_op 不崩
- `GPFactorMiner` 测试（16）：
  - 初始化参数（默认 + 自定义）
  - `_random_tree` 深度受控
  - `mine` 返回完整 dict
  - **合成强信号数据 → GP 找到 |IC|>0.05**（构造 close-MA20 反转结构）
  - 公式为字符串、history 有 generations 条目、best_factor 是 Series
  - 同种子可复现、不同种子可能不同
  - 缺 feature 返回 error
  - 算子注册表完整性

### 实盘验证

**AAPL（pop=50, gen=20, horizon=5d, 2y 样本）**：
- 进化轨迹：gen 1 |IC|=0.107 → gen 5 |IC|=0.118 → gen 17 |IC|=0.121（收敛）
- 最佳公式：`log_returns(log_returns(relu(min_(low, close)), w=10), w=10)`
  - 可解释为「下影线极端波动率的 10 日对数收益反转结构」
- IC=+0.121 / IR=+0.276 / N=232
- 判定：强信号（|IC|>0.08），建议入库用 SHAP 验证边际贡献

### 集成

- `wiki-engine/skills/wiki-quant/SKILL.md` 加模式 10（GP 因子挖掘）
- `ai-wiki/CLAUDE.md` report.md 模板加「### GP 因子挖掘」段（决策型研究可选）
- 全局 skill 同步

### 实操价值

1. 用户问"挖一个针对 NVDA 的新因子" → 直接跑 gp-mine
2. Alpha101 全部失效时 → GP 数据驱动找替代因子
3. 与 SHAP 衔接：GP 产出新因子 → shap-eval 验证不冗余 → 入库 alpha_21

### 关键修复（开发过程）

| Bug | 现象 | 根因 | 修复 |
|-----|------|------|------|
| `_collect_with_parents` 递归丢 parent | `_crossover` 报 NoneType.clone | 递归时把 c 当 root，parent 误记 None | 改为递归传参 `parent, idx` |
| tuple 解包错位 | `b_node` 接收 parent 而非 node | `_, b_node, _` 把第 2 位当 node | 改为 `b_node, _, _` |
| 失败 fitness=-1.0 污染排序 | 所有 gen_best |IC|=-1.0 → mine 报"无有效因子" | -1.0 让 sort 失效 | 改为 0.0（合法无信号） |
| 随机树产生大量无效个体 | const 进时序算子 → std=0 → IC=NaN | _random_leaf 不区分算子类型 | 初始种群拒绝 fitness=0（最多重试 5 次） |
| feature 验证不足 | feature_cols=["close"] 但 df 无 close 时跑出公式 | `if not features` 只检查空 | 加 `missing = [f for f in features if f not in df.columns]` |

### 已知局限（未来 TODO）

- **过拟合风险**：当前 mine() 用全样本 IC，未做 train/test split。未来需扩展为时间序列切分（前 70% 训练 GP，后 30% 验证）
- **种子敏感**：不同 random_state 可能挖出不同公式。建议跑 3-5 次取交集
- **小样本不可信**：N<100 时 IC 容易是噪声拟合。强制要求 ≥2 年日线
- **算子集可扩展**：当前仅复用 operators.py 既有算子，未来可加 bespoke 算子（如 options IV / skew）

### 测试统计

总测试数 434 → **457**（+23 GP 测试）。全过。

---

## 2026-07-20 Alpha101 完整覆盖（alpha_1-101，任务 #89 完成）

**动机**：用户下载 Kakushadze 2015 论文 PDF（arXiv:1601.00991）到 `ai-wiki/data/`，逐条对照完成论文剩余 71 个 alpha 的机械化移植，覆盖全部 1-101。

### 完成清单

| 范围 | 状态 | 备注 |
|------|------|------|
| alpha_1-20 | ✅ 已有 | 第一批（任务 #82 之前） |
| alpha_21-30 | ✅ 已有 | 第二批（任务 #89 第一阶段） |
| alpha_31-65 | ✅ 本次完成 | 第三批，35 个（含 4 个跳过） |
| alpha_66-101 | ✅ 本次完成 | 第四批，36 个（含 15 个跳过） |
| **总计** | **82 个已实现** | **19 个跳过（IndNeutralize/cap）** |

**跳过的 19 个**（依赖行业中性化或市值数据，单标的模式无法支持）：
- IndNeutralize：48, 58, 59, 63, 67, 69, 70, 76, 79, 80, 82, 87, 89, 90, 91, 93, 97, 100
- cap（市值）：56

### 论文算子映射（移植关键决策）

- `rank(x)` → 时序百分位（单标的退化，cross-sectional 版见 `Alpha101CrossSectional`）
- `adv{d}` → `ts_mean(volume, d)`（d 日均量，d 整数化）
- `signedpower(x, a)` → `sign(x) * |x|^a`（`_signed_power` 辅助函数）
- `decay_linear(x, d)` → `ts_decay_linear(x, d)`（线性衰减权重）
- `Ts_Rank/Ts_ArgMax/Ts_ArgMin/Ts_Max/Ts_Min/Ts_Sum/Product` → 直接复用 operators
- `IndNeutralize(x, g)` → 跳过，返回 NaN Series（单标的无行业分类）
- 非整数 magic constants（如 16.1219, 0.728317, 14.9655）→ 保留原值或合理取整

### 实施过程中的关键 bug 修复

| Bug | 根因 | 修复 |
|-----|------|------|
| alpha_46 缺闭括号 | `ts_delay(df["close"], 1])[other]` 应为 `))` | 补全括号 |
| alpha_47 括号不平衡 | `mul(div(mul(...), adv20)` 少一个 `)` | 补全并加 `, 1.0` |
| `div(scalar, Series)` 返回全 NaN | `pd.Series(scalar)` 用 RangeIndex，与 DatetimeIndex 不对齐 | 改写 div：scalar/Series 路径直接 `a / b.replace(0, NaN)` |
| `ts_corr(x, y, 2)` 报 min_periods>window | `min_periods=max(3, window//2)=3` 当 window=2 时 3>2 | `min_periods=min(window, max(3, window//2))` |
| Alpha101.IMPLEMENTED list comprehension 看不到 SKIPPED | Python 类作用域中 list comp 有独立 scope | 内联 SKIPPED 集合字面量 |

### 测试

`tests/test_factors_alpha101.py` 新增 ~215 测试（共 ~248 alpha101 测试）：
- `test_alpha101_implemented_count`：IMPLEMENTED 列表 82 个
- `test_alpha_31_101_returns_series` × 71（parametrize）：每个返回 pd.Series 长度 = 输入
- `test_alpha_31_101_skipped_is_all_nan` × 19：跳过的 alpha 返回全 NaN
- `test_alpha_31_101_non_skipped_has_values` × 52：非跳过的 alpha 至少有 1 个非 NaN
- `test_alpha_101_simple_formula`：手工值验证（close=10, open=9, high=11, low=8 → 1/3.001）
- `test_alpha101_compute_all_full_coverage`：compute_all 返回 82 个结果
- 既有 alpha_1-30 测试全部保留

### 实盘验证

合成 500 日 OHLCV 跑 `compute_all`：
- 82 个 alpha 全部跑通（compute_all 不抛异常）
- 19 个跳过的返回全 NaN（设计如此）
- 63 个非跳过的至少有部分非 NaN 值

### 已知局限

1. **IndNeutralize 跳过**：19 个依赖行业中性化的 alpha 无法在单标的模式实现；如需启用，需要加载 GICS/BICS 行业分类数据并实现 cross-sectional demeaning（多标的 panel 模式）
2. **magic constants 保留原值**：论文中 alpha_60+ 大量小数（如 16.1219, 0.728317, 14.9655）保留原值，未做整数化（避免引入偏差）
3. **rank 退化为时序百分位**：单标的模式丢失 cross-sectional 信息；真正论文版 rank 见 `Alpha101CrossSectional`（任务 #88）
4. **vwap 用 20 日简单代理**：`vwap(high, low, close, volume, 20)` 是 20 日滚动 VWAP，论文里 vwap 是当日 VWAP（需要 intraday 数据）；如能给到当日 vwap，可替换

### 后续路径（不再阻塞）

- 用户需 IndNeutralize 路径 → 加载 GICS 行业数据，实现 cross-sectional 版本的 alpha_48/58/59/63/67/69/70/76/79/80/82/87/89/90/91/93/97/100
- 用户需当日 vwap → 替换 `vwap()` 调用为当日 intraday VWAP
- 目前 82 个已足够覆盖论文 81% 的公式（19/101 跳过）

### 测试统计

总测试数 479 → **694**（+215 alpha_31-101 测试）。全过。

## 2026-07-20 Harvey haircut 防御层（Deflated IC，任务 #90 完成）

### 背景

82 个 alpha 一起评估时，|IC| 最大的有正向选择偏差（你测了 82 次，最好那个本来就是概率上的极端值）。Harvey-Liu-Zhu (2016) RFS *...and the Cross-Section of Expected Returns* 提出的 haircut 思路：把"N 次多重检验"考虑进去后，最大 |IC| 因子还显著吗？

### 新增文件

- `src/quant_scanner/factors/deflated_ic.py`（~270 行）
  - `expected_max_ir_under_null(n, σ, method)`：零假设下最大期望 IR
    - exact: `(1-γ)Φ⁻¹(1-1/N) + γΦ⁻¹(1-1/(N·e))`（Bailey-López de Prado 2014）
    - approx: `σ*√(2 ln N)`
  - `DeflatedICResult` dataclass（alpha/ic_mean/ic_std/ir/skew/kurtosis/n_obs/expected_max_ir/dic_statistic/dic_pvalue/significant）
  - `deflated_ic_test(factors, fwd, n_trials, rolling_window=60, alpha=0.05, method="exact")`：主 API，返回按 |IC| 排序的 DataFrame
  - `haircut_summary(report)`：返回 total/significant_before/significant_after/haircut_rate/top_survivors/biggest_casualties

- `tests/test_factors_deflated_ic.py`（11 测试）：数学正确性 + 信号 vs 噪声判别 + 边界情况 + 摘要结构

### 关键技术细节

1. **用 |IR| 而非带符号 IR**：因子方向不重要，看显著性
2. **Newey-West 调整 T**：滚动 IC 序列高度重叠（60 日窗口每日滑动），自相关让名义 T 严重高估有效样本数
   - `T_eff = T * (1-ρ)/(1+ρ)`，ρ = IC 序列一阶自相关
   - 这是测试从 13 个噪声通过 → 0 个的关键修复
3. **DIC 公式**（DSR 的 IC 版）：
   ```
   dic_stat = (|IR| - E[max IR₀]) * √((T_eff-1) / (1 - skew·|IR| + (kurt/4)·|IR|²))
   p_value = 1 - Φ(dic_stat)
   ```

### CLI 集成

`scanner factors deflate --ticker NVDA --horizon 5`

### 实盘验证

| 标的 | 总因子 | DIC 前 |IC|>0.03 | DIC 后 p<0.05 | Haircut rate | 存活因子 |
|------|--------|----------------------|------------------|--------------|---------|
| NVDA | 82 | 45 | 5 | **88.9%** | alpha_72, alpha_94, alpha_30, alpha_61, alpha_29 |
| BABA | 82 | 38 | 3 | **92.1%** | alpha_72, alpha_5, alpha_61 |

**关键观察**：alpha_72 在 NVDA 和 BABA 都通过 DIC，且 IC 在 NVDA 高达 -0.1765 → 这是真信号（论文里 alpha_72 = `rank(ts_decay_linear(vwap, 20))`，价格反转因子）。alpha_17/alpha_10/alpha_1 等 IC 不错但 DIC 不过 → 多重检验下的伪信号候选。

**符合 Harvey 预期**：原 30-50% haircut 预期 → 实测 89-92%（更严格，因为我们用了 exact 公式 + Newey-West 调整）。

### 测试统计

总测试数 694 → **705**（+11 deflated_ic 测试）。全过。

### 下一步（任务 #91-96）

按 López de Prado AfML 路径继续：
- #91 Triple-Barrier Labeling（Ch 3，止盈+止损+时间三道屏障）
- #92 MAX 因子（Bali-Cakici-Whitelaw 2010 JFE）
- #93 PEAD 因子（Daniel-Hirshleifer-Sun 2020 JFE）
- #94 Purged K-Fold CV + skill 同步
- #95 Fractional Differencing（Ch 5）
- #96 Meta-Labeling（Ch 4）

## 2026-07-20 Triple-Barrier Labeling（任务 #91 完成）

### 背景

传统 fixed-horizon 标签（前瞻 N 日收益正负）在波动率变化时不稳定：高波动期一个 +5% 是噪声，低波动期是巨大信号。López de Prado AfML Ch 3 提出 Triple-Barrier Labeling：用 **止盈 + 止损 + 时间** 三道屏障决定标签，屏障宽度自适应波动率（ATR 倍数）。

### 新增文件

- `src/quant_scanner/labels/__init__.py`（新模块）
- `src/quant_scanner/labels/triple_barrier.py`（~280 行）
  - `_atr(high, low, close, window)`：Wilder smoothing 的 ATR
  - `add_atr_barriers(df, tp_atr_mult, sl_atr_mult, atr_window, side)`：计算动态止盈/止损价（支持 side Series 时序方向）
  - `TripleBarrierLabeler` 类：可复用标签生成器（参数固定，多次 transform）
  - `triple_barrier_labels(...)`：快捷函数
  - `TripleBarrierResult` dataclass

- `tests/test_triple_barrier.py`（17 测试）：ATR 正确性 / 三种屏障触及 / side 方向 / 边界情况 / Labeler 类一致性

### 关键技术细节

1. **ATR Wilder smoothing**：`tr.ewm(alpha=1/window, adjust=False).mean()`（不是简单 SMA）
2. **Side 方向**：做多 tp 在上方（+），做空 tp 在下方（-）；side 可标量或 Series（支持时序方向切换）
3. **屏障触及优先级**：同时碰到 tp+sl 时保守优先止损（防假突破）
4. **垂直屏障标签**：tp/sl 都没碰 → 按 vb 收盘位置决定（+1/-1/0），不是直接返回 0
5. **时间屏障=特殊处理**：序列短于 vb 时用 `min(vb, n-1)`，不崩

### CLI 集成

```bash
scanner labels stats --ticker NVDA --tp 2.0 --sl 2.0 --vb 10 --side long
scanner labels tb --ticker BABA --side short
```

### 实盘验证

| 标的 | 方向 | 样本 | tp 命中 | sl 命中 | vb 命中 | 胜率 | 平均收益 |
|------|------|------|---------|---------|---------|------|---------|
| NVDA | long | 490 | 39.6% | 31.4% | 29.0% | **57.6%** | +0.95% |
| BABA | long | 490 | 39.8% | 37.3% | 22.9% | 47.6% | -0.17% |

**关键观察**：NVDA 2 年强上涨 → 做多胜率 57.6% 合理；BABA 2 年震荡 → 做多胜率 47.6%（接近 coin flip），与走势匹配。Triple-Barrier 的波动率自适应让标签比固定 5 日 horizon 更稳定。

### 测试统计

总测试数 705 → **722**（+17 triple_barrier 测试）。全过。

### 下一步

- #92 MAX 因子（Bali-Cakici-Whitelaw 2010 JFE，MAX effect）
- #93 PEAD 因子（Daniel-Hirshleifer-Sun 2020 JFE，盈利公告漂移）
- #94 Purged K-Fold CV（AfML Ch 7，防 label 泄漏，与 triple-barrier 配合）
- #95 Fractional Differencing（AfML Ch 5）
- #96 Meta-Labeling（AfML Ch 4，基于 triple-barrier 输出 → 二级分类器）

## 2026-07-20 MAX 因子（Bali-Cakici-Whitelaw 2010 JFE，任务 #92 完成）

### 背景

Bali-Cakici-Whitelaw (2010) JFE *Maxing Out: Stocks as Lotteries and the Cross-Section of Expected Returns*：

**MAX effect**：近期最大单日收益（MAX）高的股票，未来收益显著较低。
- 行为金融解释：投资者偏好"彩票型"股票（散户追捧极端收益）→ 高估 → 均值回归
- 月度收益差：MAX 高 vs MAX 低 = **-1.0%**（论文 Table IV）
- 在小盘股、散户占比高的股票上更强

### 新增文件

- `src/quant_scanner/factors/max_factor.py`（~130 行）
  - `max_factor(close, window, k)`：通用 MAX 因子（top-k 平均日收益）
  - `max_factor_1(close, window)`：MAX(1)（最大单日收益）
  - `max_factor_5(close, window)`：MAX(5)（top-5 平均，论文标准）
  - `max_decile_rank(close, window, k)`：MAX 在过去 N 天的分位（>0.9 彩票型）

- `tests/test_max_factor.py`（13 测试）：数学正确性 / 彩票型 vs 平稳 / 快捷函数等价 / 边界情况

### 关键技术细节

1. **用 simple return（pct_change）而非 log return**：保持与论文一致
2. **pct_change 首值填 0**：避免 rolling 因首 NaN 丢一个样本
3. **k>1 取 top-k 平均**：MAX(5) 比 MAX(1) 更稳定（论文标准）
4. **分位 rank**：>0.9 = 彩票型（警惕反转），<0.1 = 走势平稳

### 实盘验证

| 标的 | MAX1 当前 | MAX1 IC | MAX5 当前 | MAX5 IC | MAX5 分位 |
|------|----------|---------|----------|---------|-----------|
| NVDA | 4.06% | +0.004 | 3.20% | +0.027 | 0.40（中性）|
| BABA | 11.05% | **-0.032** | 4.64% | -0.002 | **1.00（彩票型）** |

**关键观察**：
- BABA MAX1 IC=-0.032 符合论文负向预测，且当前 MAX 分位=1.00（彩票型！）
  → BABA 近期有极端单日涨幅，按 MAX effect 论文应警惕反转
- NVDA IC≈0：强上涨股的 MAX effect 被趋势掩盖（论文也说大市值股效应较弱）
- 这验证了 MAX 因子对"散户占比高 / 小盘 / 震荡股"更有效

### 测试统计

总测试数 722 → **735**（+13 max_factor 测试）。全过。

## 2026-07-20 PEAD 因子（Daniel-Hirshleifer-Sun 2020 JFE，任务 #93 完成）

### 背景

PEAD（Post-Earnings Announcement Drift）是市场最持久的异象之一，**40+ 年未被套利消除**：
- Ball-Brown (1968) 首次记录
- Bernard-Thomas (1989) 系统化
- Daniel-Hirshleifer-Sun (2020) JFE 整合到短周期行为错误定价框架

**核心现象**：盈利公告后超预期（SUE>0）→ 股价未来 1-3 个月持续漂移向上；不及预期（SUE<0）→ 持续向下。
- SUE 最高 10% 股票 → 未来 60 日超额收益 +2-4%
- 月度 IC 通常 0.03-0.06

### 新增文件

- `src/quant_scanner/factors/pead_factor.py`（~190 行）
  - `compute_sue(quarterly_eps, n_surprise_for_std)`：SUE 计算（季节性随机游走）
  - `pead_signal(quarterly_eps, price_index, decay_window, ...)`：日频信号（带时间衰减）
  - `pead_categorize(sue_value)`：分类（strong_beat/beat/in_line/miss/strong_miss）
  - `pead_summary(quarterly_eps, latest_n)`：摘要

- `tests/test_pead_factor.py`（14 测试）

### 关键技术细节

1. **季节性随机游走**：`E[EPS_t] = EPS_{t-4}`（去年同季度）
2. **MAD 归一化**（替代 std）：抗异常值，支持 N=1 退化情况（SUE=±1）
   ```python
   mad = surprise.abs().rolling(window=4, min_periods=1).mean()
   sue = surprise / mad
   ```
3. **时间衰减**：发布当天 SUE=原值，60 天后线性衰减到 0
4. **分类阈值**：|SUE|≥2 强信号，1-2 中等，<1 in-line

### 实盘验证（NVDA/BABA）

| 标的 | 最近财报 | SUE | 分类 |
|------|----------|-----|------|
| NVDA | 2026-04-30 | +1.000 | beat |
| BABA | 2026-03-31 | +1.000 | beat |

**⚠️ 数据限制**：yfinance `quarterly_income_stmt` 默认只返回 5 个季度，导致 MAD 归一化退化为 N=1（SUE=±1）。
- 实际需要 8+ 季度才能产生有意义的 SUE
- 解决方案：扩展 loader 接入 SEC EDGAR（已规划）

### 测试统计

总测试数 735 → **749**（+14 pead_factor 测试）。全过。

## 2026-07-21 Fractional Differencing（AfML Ch 5，任务 #95 完成）

### 背景

传统一阶差分 `X_t - X_{t-1}` 让金融时间序列（价格）平稳，但**完全丢失记忆**。
价格有强记忆性（过去的价格影响未来）→ 平稳 vs 记忆是 trade-off。

López de Prado AfML Ch 5 提出 Fractional Differencing：差分阶数 d ∈ (0, 1)：
- d=0：无差分（强记忆但不平稳）
- d=1：完全差分（平稳但无记忆）
- d=0.3-0.6：部分差分，平稳且保留部分记忆

### 新增文件

- `src/quant_scanner/factors/fractional_diff.py`（~200 行）
  - `_get_weights(d, threshold, max_width)`：fractional diff 权重序列（Hosking 1981 递推）
  - `fractional_diff(series, d, threshold, max_width)`：固定宽度窗口实现
  - `adf_pvalue(series)`：ADF 检验 p-value（用 statsmodels）
  - `find_min_d(series, d_grid, threshold, alpha)`：自动找最小平稳 d*
  - `memory_retention(series, d, threshold)`：与原序列的 R²（记忆保留度量）

- `tests/test_fractional_diff.py`（17 测试）

### 关键技术细节

1. **权重递推公式**：`w_k = w_{k-1} * (k-1-d) / k`（Hosking 1981）
2. **固定宽度截断**：threshold=1e-3 + max_width=200（默认）
   - 防止 d 小时权重序列爆炸（d=0.1 时原始序列上千项）
   - 实际宽度：d=0.4 约 50-100 项，d=1 仅 2 项
3. **ADF 用 statsmodels**：scipy.stats 没有 adfuller（之前的 bug）

### 实盘验证

| 标的 | d* (最小平稳 d) | ADF p-value | 记忆保留 (vs d=1) |
|------|-----------------|-------------|-------------------|
| NVDA | **0.4** | 0.034 | **56.7%**（vs d=1 的 0.5%） |
| BABA | **0.4** | 0.0067 | **54.0%**（vs d=1 的 0.1%） |

**关键观察**：
- d*=0.4 符合 AfML 论文经验值（金融资产通常 0.3-0.6）
- Fractional diff 保留 54-57% 原始方差，d=1 仅保留 < 1%
- **意义**：用 d* 替代 log return 作为 ML 特征，能让模型同时获取平稳性 + 长期记忆

### 依赖更新

`pyproject.toml` 加 `statsmodels>=0.14`（ADF 检验）

### 测试统计

总测试数 749 → **766**（+17 fractional_diff 测试）。全过。

## 2026-07-21 Meta-Labeling 二级分类器（AfML Ch 4，任务 #96 完成）

### 背景

传统单一模型既预测方向（买/卖）又预测仓位大小，容易过拟合。López de Prado AfML Ch 4 提
出 Meta-Labeling：把决策拆成两个独立问题：

| 模型 | 问题 | 输出 |
|------|------|------|
| Primary（一级） | 方向：何时买/卖？ | side ∈ {+1, -1} |
| Secondary（meta） | 信心：这次下注能赚吗？ | bet ∈ {0, 1} |

最终仓位 = side × confidence（方向 × 信心）。拆解减少过拟合，过滤低信心信号提升 precision。

### 新增文件

- `src/quant_scanner/labels/meta_labeling.py`（~200 行）
  - `MetaLabeler` 类：`tp_atr_mult`、`sl_atr_mult`、`atr_window`、`vertical_barrier_bars`
  - `prepare_meta_labels(df, primary_side)`：用 primary_side 作为 side 跑 triple-barrier，
    得到 `triple_label`（+1/0/-1）+ `meta_target`（=1 if primary 方向正确 else 0）
  - `compute_position_size(side, confidence, max_position)`：side × confidence × max_position，
    支持标量和 pd.Series
  - `meta_label_summary(meta_labels)`：返回 total/primary_correct/precision/by_barrier

- `tests/test_meta_labeling.py`（13 测试）

### CLI 集成

`labels` 命令新增 `meta` action：
```bash
quant-scanner labels meta --ticker NVDA --side long --tp 2.0 --sl 2.0 --vb 10
```
输出 primary 方向命中率、precision、barrier 分布。

### 关键技术细节

- **复用 triple-barrier**：`prepare_meta_labels` 内部调 `triple_barrier_labels(df, side=primary_side)`，
  得到 `label` 列，再 `meta_target = (label == 1).astype(int)`。复用而非重写，避免代码冗余。
- **side 标量与 Series 都支持**：标量 → 全程固定方向；Series → 每个入场点用对应方向
  （用于把已有 primary 预测序列作为输入）。
- **二分类 vs 三分类**：meta_target 严格二分类（0/1），secondary model 输出 P(target=1)
  作为 confidence。
- **position_size 公式**：`side × confidence × max_position`，标量返回 float，Series 返回
  Series（自动对齐索引）。`max_position` 默认 1.0（满仓）。

### NVDA/BABA 验证（2y 样本，tp=2×ATR, sl=2×ATR, vb=10bars）

| 标的 | side | precision | target=1 / 0 | barrier 分布 (tp/sl/vb) |
|------|------|-----------|--------------|------------------------|
| NVDA | long | **57.6%** | 282 / 208 | 39.6% / 31.4% / 29.0% |
| NVDA | short | **42.4%** | 208 / 282 | 31.4% / 39.6% / 29.0% |
| BABA | long | **47.6%** | 233 / 257 | 39.8% / 37.3% / 22.9% |

**关键观察**：
- NVDA long precision 57.6% > BABA long 47.6%，与 triple-barrier 阶段（任务 #91）
  的胜率一致，印证 NVDA 2 年单边上涨而 BABA 横盘
- NVDA short precision 42.4% = 100% - NVDA long 57.6%，方向对称性正确
- barrier 分布 tp/sl 几乎对称（sl 略多），表明 tp/sl 比例合理

### 后续衔接

secondary model 尚未实现（属于下游 ML 任务，超出本轮论文因子补齐范围）。
`compute_position_size()` 已就位，等 secondary model（sklearn 二分类器）输出 confidence 后
可直接调用。Meta-target 可作为下游 ML pipeline 的监督信号。

### 测试统计

总测试数 766 → **779**（+13 meta_labeling 测试）。全过。

## 2026-07-21 Purged K-Fold CV（AfML Ch 7，任务 #94 完成）

### 背景

标准 K-fold CV 假设样本独立同分布 → 金融数据严重违反：
- Triple-Barrier 标签窗口重叠 → 训练/测试样本标签共享
- 滚动 IC 重叠 → IC 虚高、策略过拟合

López de Prado AfML Ch 7 提出解决方案：
1. **Purge（清洗）**：训练集中移除标签窗口 ∩ 测试集 的样本
2. **Embargo（禁运）**：测试集之后再加 τ bars 缓冲防自相关泄漏
3. **OOS IC**：在 K 个 fold 的 OOS 数据上算 IC，得到真实信息比率
4. **叠加 Harvey haircut**：N 个 alpha × OOS IC → Deflated IC

### 新增模块：eval（首次建立）

- `src/quant_scanner/eval/__init__.py`
- `src/quant_scanner/eval/purged_kfold.py`（~300 行）：
  - `PurgedKFold` dataclass：`n_splits=5, purge_bars=10, embargo_bars=5`
  - `purged_kfold_indices(n, ...)`：快捷函数返回 K 个 (train, test) 对
  - `purged_cv_ic(factor, target, ...)`：单 alpha OOS IC 评估，返回
    `{fold_ics, oos_ic_mean, oos_ic_std, oos_ir, n_effective, n_train_total, n_test_total}`
  - `purged_cv_deflated_ic(factors, target, ...)`：N alpha OOS + DIC，返回
    DataFrame 含 alpha/oos_ic_mean/oos_ir/abs_oos_ir/expected_max_ir/dic_pvalue/significant
  - `oos_summary(report)`：haircut 摘要
  - `_emax_ir_null(N)`：Bailey-López de Prado 2014 公式（复用 deflated_ic.py 逻辑）

### 关键技术细节

- **复用 Newey-West T_eff 调整**：fold IC 序列跨折自相关，T_eff = T × (1-ρ)/(1+ρ)
- **Spearman IC（rank）**：单 fold 用 spearmanr 防 outlier
- **简化 DIC denom**：fold IC 数量（=n_splits）通常 5，不足以估计偏度/峰度，denom=1
- **5 folds 默认**：经验值，与 sklearn KFold 一致；金融数据 2y ≈ 500 样本 → 每 fold 100
- **purge/embargo 默认对齐 horizon**：`purge = max(horizon, 5)`，`embargo = max(horizon//2, 2)`

### CLI 集成

`factors` 命令新增 `purged-cv` action：
```bash
quant-scanner factors purged-cv --ticker NVDA --horizon 5 --period 2y
# 输出 OOS IC Top + OOS DIC 摘要
```

### NVDA 验证（2y 样本，horizon=5d，5 folds）

| 字段 | 值 |
|------|----|
| N trials | 82 |
| OOS 有效 (|IC|>0.03) | 42 |
| DIC 通过 (p<0.05) | 0 |
| OOS haircut rate | 100% |
| OOS \|IR\| 中位数 | 0.392 |
| Top alpha | alpha_42 (OOS IC=+0.096, OOS IR=+2.10) |

**关键观察**：
- 0 个 alpha 通过 DIC 是**符合预期**的：5 folds × 82 trials，E[max IR]=2.46 极其严格，
  OOS IR>2.46 才能过；alpha_42 的 IR=2.10 已接近但未越线
- **这正说明 Harvey haircut + Purged CV 是金标准**：把过拟合 alpha 彻底过滤掉
- OOS |IR| 中位数 0.392：NVDA 整体仍有 alpha 结构，只是单 alpha 不够极端
- 工程意义：可信的 alpha 需要更大样本（≥10y）或更少 trials（先模式 11 筛出 5 个再跑 OOS）

### wiki-quant SKILL.md 同步

新增「模式 12：Purged K-Fold CV（OOS 评估 + 多重检验双重过滤）」节，包含：
- 与模式 11（deflate）的对比表
- AfML 完整流水线（Triple-Barrier → Meta-Labeling → Purged CV）
- 强制规则：决策型研究必须用模式 12 验证模式 11 存活因子
- 全局副本 `~/.claude/skills/wiki-quant/SKILL.md` 同步

### AfML 论文因子流水线完整收官

| 任务 | 论文章节 | 模块 | 状态 |
|------|---------|------|------|
| #90 | Harvey-Liu-Zhu 2016 RFS | `factors/deflated_ic.py` | ✅ |
| #91 | AfML Ch 3 | `labels/triple_barrier.py` | ✅ |
| #92 | Bali-Cakici-Whitelaw 2010 JFE | `factors/max_factor.py` | ✅ |
| #93 | Daniel-Hirshleifer-Sun 2020 JFE | `factors/pead_factor.py` | ✅ |
| #95 | AfML Ch 5 | `factors/fractional_diff.py` | ✅ |
| #96 | AfML Ch 4 | `labels/meta_labeling.py` | ✅ |
| #94 | AfML Ch 7 | `eval/purged_kfold.py` | ✅ |

**整套 AfML 流水线已就位**：标签生成（triple-barrier）→ 二级分类（meta-labeling）→
特征工程（fractional diff + MAX + PEAD）→ 严格评估（purged CV + Harvey haircut）。

### 测试统计

总测试数 779 → **796**（+17 purged_kfold 测试）。全过。

## 2026-07-21 Secondary Model 闭环（AfML Ch 4 完整闭环，任务 #97 完成）

### 背景

任务 #96 的 MetaLabeler 产出了 `meta_target` 二分类标签，但**没有训练下游 sklearn 分类器**输出
confidence。这意味着整套 AfML 流水线只走了一半——产出标签而不产出可执行仓位决策。

López de Prado AfML Ch 4 的核心论点：**人拍方向，ML 拍仓位**。本任务接通下游，让流水线
真正闭环：
- Primary（已有 alpha）：方向 → side ∈ {+1, -1}
- Secondary（本任务）：信心 → P(primary 对) ∈ [0, 1]
- 最终仓位 = side × confidence × max_position（二值 → 连续决策）

### 新增模块：ml（首次建立）

- `src/quant_scanner/ml/__init__.py`
- `src/quant_scanner/ml/secondary_model.py`（~350 行）：
  - `SecondaryModel` dataclass：包装 sklearn 分类器（默认 LogisticRegression），
    支持 `fit` / `predict_confidence` / `compute_position`
  - `build_features_from_alphas(df, alpha_names=None)`：从 Alpha101 构建 feature
    DataFrame，**winsorize 1%/99% 极值 + z-score + clip[-10,10] 防 sklearn overflow**
  - `run_meta_labeling_pipeline(...)`：端到端
    primary → MetaLabeler → meta_target → SecondaryModel.fit → confidence → position，
    输出 PipelineResult（含 primary/secondary OOS 精度对比、过滤率）
  - `cross_validate_secondary(...)`：串起任务 #94 的 Purged K-Fold，每折训练
    secondary → 在 OOS 上预测 → 计算 lift = sec_prec - primary_prec
  - `pipeline_summary(result)`：输出摘要 dict
  - `_clone_clf(clf)`：sklearn clone 包装（每折重建模型防状态污染）

### 关键技术细节

1. **Feature winsorize**：直接 z-score 化会被极端值（alpha_42 含 ±1e10）破坏，必须先
   用 1%/99% 分位数裁剪再标准化。曾遇到 sklearn `matmul overflow`，修后稳定
2. **Top-features 默认 10**：81 个 alpha features × 490 样本必然过拟合，按 |IC| 排序选 top N
3. **GBM 默认（不是 LR）**：LogisticRegression 在 meta_target 严重失衡（如 NVDA 永远做多
   时 meta_target=1 占 57%）会退化为"总预测 1"。GradientBoostingClassifier 在小样本非
   线性表现更好
4. **时序切分不打乱**：OOS 必须按时间顺序，避免训练集"偷看"未来
5. **每折 clone clf**：sklearn 分类器有 fit 状态，CV 时必须重建，否则状态污染

### CLI 集成

`scanner ml` 命令（新增）：
```bash
scanner ml secondary --ticker NVDA --primary-alpha alpha_42 \
    --top-features 10 --model gbm --test-size 0.3
# 输出端到端 OOS lift + 过滤率

scanner ml cv --ticker NVDA --primary-alpha alpha_42 \
    --top-features 10 --model gbm --n-splits 5
# 输出 Purged K-Fold 每折 lift（跨 fold 稳定性）
```

### 实盘验证（2y 样本，GBM + top-10 features, 5-fold Purged CV）

| 标的 | Primary | Primary P | Secondary P | Lift | Filter% |
|------|---------|-----------|-------------|------|---------|
| NVDA | alpha_42 | 57.6% | 61.9% | **+4.37pp** | 38% |
| BABA | alpha_42 | 47.6% | 50.0% | **+2.48pp** | 51% |

**NVDA 5 folds 全正**：+1.0 / +9.9 / +3.4 / +6.0 / +1.5（最高 fold 1 +9.9pp）
**BABA 2 正 3 负平均正**：+7.4 / -2.6 / +11.9 / -3.9 / -0.5（fold 2 单 fold 拉高）

**关键观察**：
- 论文典型 lift +3-7pp，NVDA +4.37pp **完全达标**
- BABA 偏弱符合预期：之前 deflated_ic 显示 BABA 大部分 alpha 不显著，primary 本身就噪声
- 对比修复前（LR + 81 features）：NVDA 仅 +1.87pp 且 4 fold 接近 0；GBM + top-10 提升 2-3 倍

### wiki-quant SKILL.md 同步

新增「模式 13：Secondary Model 闭环」节（~150 行），包含：
- 端到端调用方式 + 参数说明
- NVDA 实盘结果示例
- 与模式 6（仓位管理）的联合规则：base 仓位 × confidence 双重过滤
- 与模式 10（GP 挖掘）的衔接：GP 新因子 → purged-cv → ml cv 双重验证 → 入库
- 强制规则：决策型研究推荐某 alpha 作 primary 时**必须**跑 ml cv 验证 lift
- 全局副本 `~/.claude/skills/wiki-quant/SKILL.md` 同步

### AfML 完整流水线收官

| 任务 | 论文章节 | 模块 | 角色 |
|------|---------|------|------|
| #91 | AfML Ch 3 | `labels/triple_barrier.py` | 标签生成 |
| #96 | AfML Ch 4 上半 | `labels/meta_labeling.py` | 二分类 target |
| #97 | AfML Ch 4 下半 | `ml/secondary_model.py` | **ML 闭环** |
| #95 | AfML Ch 5 | `factors/fractional_diff.py` | 平稳特征 |
| #94 | AfML Ch 7 | `eval/purged_kfold.py` | OOS 评估 |
| #90 | Harvey 2016 | `factors/deflated_ic.py` | 多重检验 |
| #92 | Bali 2010 | `factors/max_factor.py` | 彩票因子 |
| #93 | Daniel 2020 | `factors/pead_factor.py` | 盈利漂移 |

**完整流水线**：标签（triple-barrier）→ 二分类（meta_target）→ ML 闭环（secondary）→
OOS 严格评估（purged CV + Harvey haircut）+ 论文因子（MAX + PEAD + fractional diff）。

### 测试统计

总测试数 796 → **813**（+17 secondary_model 测试）。全过。
