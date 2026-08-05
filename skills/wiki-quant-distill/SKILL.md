---
name: wiki-quant-distill
description: 美股量化策略蒸馏器。把一本量化/技术分析书籍蒸馏成可运行的 signal.py + tests，挂载到 wiki-engine/tools/quant-scanner。cangjie-skill 的工程化接力——前者产方法论（人说的话），本 skill 产代码（机器跑的策略）。当用户说"蒸馏《XXX》到 quant"、"把这本书的策略挂上来"、"回炉重新实现 VCP"时触发。
---

# wiki-quant-distill — 量化书籍 → 可运行策略

> **定位**：补 `wiki-engine/tools/quant-scanner/` 信号库的"源头活水"。用户不想自己想策略，本 skill 把经典量化书籍的方法论自动化转成代码，让信号库持续扩展。
>
> **接力关系**：上游 `cangjie-skill`（book2skill）蒸馏书 → 产出 SKILL.md（方法论）；本 skill 接力 → 把方法论转成 `signals/{name}.py` + `tests/test_{name}.py`（可执行代码）。
>
> **核心差异（vs cangjie-skill）**：
> | 维度 | cangjie-skill | wiki-quant-distill（本 skill） |
> |------|---------------|-----------------------------|
> | 输入 | PDF + 书名 | PDF + 书名（同上） |
> | 输出 | 方法论 SKILL.md（R/I/A1/A2/E/B/L 七段） | signal.py + test_*.py（继承 BaseSignal） |
> | 目标读者 | 人 | quant-scanner 引擎 |
> | 跑测试 | 不跑 | pytest 必须通过 |

## 触发条件

**显式触发词**：
- "蒸馏《XXX》到 quant / 蒸馏《XXX》挂到扫描器"
- "把这本书的策略实现成 signal"
- "回炉重新实现 VCP / 重新蒸馏现有信号"
- "批量蒸馏这 3 本量化书"

**隐式触发**：
- 用户丢 PDF + "做策略"
- wiki-quant 报告"现有信号不足覆盖此形态"时，用户问"怎么补"

## 不适用场景

- ❌ 纯方法论书（无策略）：用 cangjie-skill 即可
- ❌ 非美股/非技术分析：如期权定价、债券策略（超出 quant-scanner 范围）
- ❌ 用户只想理解书的内容：用 wiki-research 走读书流程

## 工作流（5 阶段）

### 阶段 0：确认范围（必做断点）

启动前必须从用户处确认：

1. **书名 + PDF 路径**：必填，PDF 在 `mind/ai-wiki/data/` 下
2. **蒸馏范围**：
   - 全书（默认）
   - 指定章节（如"只蒸馏第 5-7 章缺口理论"）
   - 指定策略（如"只蒸馏威廉指标 + OBV"）
3. **挂载方式**：
   - 新增 signal（默认）— 在 `signals/` 下新建文件
   - 修订现有 signal — 找到对应文件，对照原文重写算法
   - 仅产出方法论 — 不写代码，只跑 cangjie-skill
4. **预期产出信号数**：用户预期几个 signal？（防止蒸馏过度碎片化）

**红线**：PDF 路径找不到、或书明显不在量化领域（如《聪明的投资者》价值投资），先停下来问用户。

### 阶段 1：调 cangjie-skill 蒸馏方法论（上游）

调用 `cangjie-skill`（book2skill）跑完整流水线：

```
阶段 0: Adler 整书理解 → BOOK_OVERVIEW.md
阶段 1: 5 agent 并行提取 → 候选方法论池
阶段 1.5: 三重验证 → 通过的单元
阶段 2: RIA++ → SKILL.md
阶段 3: Zettelkasten 链接
阶段 4: 压力测试 → test-prompts.json
```

**产出落点**：`mind/ai-wiki/wiki/analysis/{书名}/skills/{skill名}/SKILL.md`

**完成后必须检查**：
- SKILL.md 的 `E (Execution)` 段是否有可代码化的步骤？
- `B (Boundary)` 段是否定义了清晰的"不适用条件"（→ 转成 signal 的过滤逻辑）？
- `L (Lens)` 段是否给出了量化指标（→ 转成 signal 的 score 计算）？

如果 SKILL.md 偏方法论抽象、缺量化锚点，**不要进入阶段 2**，先回炉补量化定义。

### 阶段 2：方法论 → signal.py 设计

对每个通过的 skill 候选，设计代码方案：

#### 2.1 命名（强制规范）

| 维度 | 规则 | 示例 |
|------|------|------|
| signal slug | `snake_case`，与 SKILL.md slug 一致 | `vcp` / `cup_handle` / `major_reversal` |
| 类名 | `PascalCase + Signal` 后缀 | `VCPSignal` / `CupHandleSignal` |
| signal.name | 与 slug 同 | `"vcp"` / `"cup_handle"` |
| 文件路径 | `signals/{slug}.py` | `signals/vcp.py` |

**禁止**：使用书名做 slug（如 `minervini_vcp`），用策略本质名（`vcp`）。

#### 2.2 设计 SignalResult 结构

所有信号必须返回 `SignalResult`，详见 `signals/base.py`：

```python
SignalResult(
    ticker=ticker,
    signal_name="xxx",
    value=0.8,           # [-1, +1]，1=强做多，-1=强做空
    passed=True,         # value > threshold 时 True
    details={            # 原始数据 + 解释字段
        "pattern_type": "double_bottom",
        "score_breakdown": {...},
        "target_price": 123.45,
    },
    reasons=["...", "..."]  # 人类可读的触发理由列表
)
```

**value 归一化规则**（借鉴 ai-hedge-fund/v2）：
- 强做多：+0.8 ~ +1.0
- 弱做多：+0.5 ~ +0.8
- 中性：-0.5 ~ +0.5
- 弱做空：-0.8 ~ -0.5
- 强做空：-1.0 ~ -0.8

#### 2.3 识别算法源

每个策略要明确数据源：
- **纯价格型**（OHLCV）：trend_template / vcp / cup_handle / major_reversal / continuation
- **需要基本面**：can_slim（EPS/ROE）、new_high_supply（流通股/内部人）
- **需要全市场对比**：rs_rating（SP500 宇宙）、market_direction（^GSPC）

**关键决策**：能否纯价格实现？能 → `--price-only` 可用；不能 → 标注外部数据依赖，在 SKILL.md 中说明。

#### 2.4 写 signals/{slug}.py

**强制要求**：
- 继承 `BaseSignal`
- 实现唯一方法 `evaluate(ticker, df) -> SignalResult`
- 模块 docstring 引用来源（书名 + 章节 + skill slug）
- 用 `logging.getLogger(__name__)`，不要 `print`
- 防御性编程：数据列缺失/NaN 时返回中性 `SignalResult(value=0.0, passed=False)`

**参考模板**：`templates/signal.py.template`

#### 2.5 写 tests/test_{slug}.py

**强制覆盖**（参考 `templates/test_signal.py.template`）：
- 强信号触发（合成 HH/HL 数据 → value ≥ 0.7）
- 弱信号触发（边界数据 → value 在阈值附近）
- 中性场景（无信号 → value ≈ 0, passed=False）
- 数据缺失/NaN 不崩
- value 在 [-1, +1] 范围内（clamp 测试）

**禁止**：写占位测试（`def test_dummy(): assert True`），所有测试必须可运行且覆盖核心路径。

### 阶段 3：注册 + 验证

#### 3.1 注册到扫描引擎

- `signals/__init__.py`：导出新类
- 如果扫描引擎有 `default_signals` 列表（见 `scanner/engine.py`），加入新 signal
- 如果 CLI 有 `--signal` 选项（见 `scanner/cli.py`），更新帮助文档

#### 3.2 跑测试

```bash
cd wiki-engine/tools/quant-scanner
.venv/bin/python -m pytest tests/test_{slug}.py -v --tb=short
# 然后
.venv/bin/python -m pytest tests/ -q --tb=short  # 全量回归，确保没破坏现有
```

**红线**：新测试必须通过 + 全量回归 0 失败。失败必须修复，不能"先 merge 再说"。

#### 3.3 实盘冒烟

跑一次真实标的扫描：

```bash
bash wrappers/run-scan.sh NVDA AAPL 2>&1 | grep "{slug}"
```

- 输出非 None、非 NaN
- value 在 [-1, +1]
- details 字段填充合理

### 阶段 4：文档同步

**必更新文件**：

1. `wiki-engine/tools/quant-scanner/HANDOFF.md`
   - 信号文件表加一行
   - "项目位置 → 代码" 描述如新增模块，更新
   - 信号总数 +1
2. `memory/project_quant_scanner.md`
   - "已实现 → 信号系统"表加一行
   - 测试数更新（原 278 + 新增 N）
3. `ai-wiki/wiki/analysis/{书名}/skills/INDEX.md`
   - 标注该 skill 已挂载到代码（slug + 文件路径）
4. 如果是书的第一批信号，更新 `ai-wiki/wiki/analysis/quant-methods-index.md` 总览
5. 全局软链：`~/.claude/skills/books/{书名}-{skill名}.md`（由 cangjie-skill 创建，本 skill 不重复）

### 阶段 5：用户验证

把产出汇总给用户：
- 新增 N 个 signal + 文件路径
- 测试通过数 / 总数
- 实盘冒烟结果（NVDA/AAPL 上的 sample value）
- 下一步建议（如"建议再加 X 信号互补" / "现有算法在 Y 场景需注意"）

## 信号命名速查（避免与现有冲突）

现有 16 个 signal（2026-07-18 状态）：

| 持续型 | 反转型 | 择时/选股型 | 综合型 |
|-------|-------|-----------|-------|
| trend_template | cup_handle | oscillator_timing | can_slim |
| vcp | major_reversal | rs_rating | new_high_supply |
| pivot_point | support_resistance | | |
| continuation | | market_direction | |
| trend_regime | | dow_phases | |
| | | sell_signals | |

**新增时检查**：
- 是否与现有 signal 本质重叠？（如"波动率收缩"和 VCP 是同一个）
- 是否能合并？（如新增"岛形反转"应进 `major_reversal` 而非新建）
- 是否真的需要独立 signal？（如果只是参数变体，作为现有 signal 的子型即可）

## 多本批量蒸馏

用户一次丢 3 本书时：

1. **顺序处理**（不并行）：每本完整跑 5 阶段再进下一本（避免信号库污染 + 测试失败定位难）
2. **跨书去重**：每跑完一本，检查与已有 signal 是否本质重叠
3. **优先级排序**：按用户指定顺序；无指定时按"经典程度 + 数据可得性"

**禁止**：一次开 3 本并行跑——出问题时难定位。

## 回炉（修订现有 signal）

当用户说"VCP 算法误报多，回炉对照原文重写"：

1. **读取现有 signal.py**：理解当前实现
2. **重跑 cangjie-skill 阶段 2**：对照原书重新蒸馏方法论
3. **diff**：列出原算法 vs 书中定义的偏差
4. **重写**：保持 API 不变（SignalResult 结构 + signal.name），只改算法
5. **回归测试**：先跑现有测试看哪些通过/失败，失败的更新（因为算法变了）
6. **冒烟对比**：跑同一批 ticker，对比修改前后的 value 差异

**红线**：禁止"顺手优化"——只改用户点名的 signal，不动周边代码。

## 与其他 skill 的关系

| skill | 关系 |
|-------|------|
| `cangjie-skill`（book2skill） | 上游 — 蒸馏方法论，本 skill 接力代码化 |
| `wiki-quant` | 下游 — 用本 skill 产出的 signal 跑扫描 |
| `wiki-research` | 间接 — 通过 wiki-quant 间接受益于本 skill |
| `cross-ai-debate` | 可选 — 复杂策略设计时跨 AI 辩论 |

## 输出落盘规则

| 产出 | 路径 | 用途 |
|------|------|------|
| 方法论 SKILL.md | `wiki/analysis/{书名}/skills/{skill名}/SKILL.md` | cangjie-skill 产出 |
| signal.py | `wiki-engine/tools/quant-scanner/src/quant_scanner/signals/{slug}.py` | 引擎挂载 |
| test_*.py | `wiki-engine/tools/quant-scanner/tests/test_{slug}.py` | 验证 |
| HANDOFF.md | 同上 tools/quant-scanner/ | 跨会话续做 |
| memory | `~/.claude/projects/.../memory/project_quant_scanner.md` | 持久记忆 |

## 默认参数（不传时）

- 测试样本数据周期：2 年（`period=2y`）
- 冒烟扫描标的：`NVDA AAPL TSLA`（3 只代表性股票）
- 蒸馏范围：全书
- 挂载方式：新增 signal

## 质量门槛（违反则阻止输出）

1. **测试必须通过**：新 signal 测试 + 全量回归 0 失败
2. **冒烟必须成功**：NVDA/AAPL 上 value 非 None 非 NaN
3. **命名必须规范**：与现有 16 个 signal 风格一致
4. **必须解释术语**：details 字段的人类可读字段必须中文化
5. **必须更新文档**：HANDOFF.md + memory + INDEX.md
6. **不得自作主张**：用户点名蒸馏 X，不要顺手把 Y/Z 也蒸馏了

## 故障兜底

- **cangjie-skill 失败**：明确告知"方法论蒸馏失败，停止流程"，不要硬写代码
- **PDF 解析失败**：让用户转成 txt 后再丢
- **测试失败**：先修测试，不要修改测试断言迁就代码（除非断言本身错）
- **数据源不可用**：signal 加 fallback 逻辑（如 EDGAR 失败 → yfinance）

## 完成标志

向用户输出：

```
✅ 蒸馏完成：《XXX》

📚 方法论产出：N 个 skill（详见 wiki/analysis/{书名}/skills/）
🔧 代码挂载：M 个 signal（详见 wiki-engine/tools/quant-scanner/signals/）
🧪 测试：原 278 + 新增 K = 总 {278+K}，全过
🔍 冒烟：NVDA value=X.XX, AAPL value=Y.YY
📝 文档：HANDOFF.md + memory + INDEX.md 已更新

下一步：
- 跑 `bash wrappers/run-scan.sh NVDA` 看新 signal 在实盘的触发情况
- 跑 `bash wrappers/run-stats.sh NVDA AAPL TSLA --period 2y` 看历史胜率
```
