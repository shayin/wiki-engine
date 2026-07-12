---
name: wiki-sweep
description: 扫描知识库健康度 + 全自动维护。两档分类执行（机械修复/判断更新）+ 变量 state_snapshot 对比 + C 档两阶段查证 + 微信推送变更 + 支持单标的模式。触发：用户说"扫一下"、"检查遗漏"、"整理知识库"、"sweep"、"/wiki-sweep {标的}"，或由 cron wrapper 自动触发
---

# Sweep V2 — 知识库全自动维护

> **分层**：② 维护类 · 知识库自检与更新
> **被调**：用户、cron 定时、wiki-mine（Phase 10 反向触发 C 档查证）
> **调用**：wiki-mine（维度 M 盲区体检）、wiki-lens（维度 L 透镜碰撞回顾）、wiki-push（微信推送）

## 核心理念

**全自动 + 全透明**：所有可自动处理的变更直接落地，但通过 changelog + 微信推送让用户随时知道发生了什么。

两档分类自动执行（A 档机械修复 + C 档判断更新）+ 变量失效基于 state_snapshot 对比（不是出现频率）+ tracker analysis 区只追加不覆盖 + 变量关停单独二次确认。

**为什么不分"数据刷新"档**：变量数据一变，往往就需要重新分析判断。不存在"只刷数据不动分析"的场景——数据变化本身就是触发 C 档研究的信号。

## 触发条件

- 用户说"扫一下"、"检查遗漏"、"整理知识库"、"sweep"
- cron wrapper 自动触发（每日 SWEEP_TIME）
- 手动调用 `/wiki-sweep`（全量扫描）
- 手动调用 `/wiki-sweep {标的}`（单标的模式，如 `/wiki-sweep 拼多多`）

## 前置检查

🔴 **CHECKPOINT：环境变量**
- `WIKI_ROOT` 必须设置且指向知识库根目录
- `$WIKI_ROOT/wiki/` 和 `$WIKI_ROOT/.cron/` 必须存在
- 失败则终止，不继续

## 执行模式

### 模式 A：交互模式（用户手动触发）
- 完整扫描所有维度
- 两档全部自动执行
- 关键变更实时推微信（如果用户在对话中）
- 末尾给用户当日变更摘要

### 模式 B：cron 模式（无人值守）
- 与模式 A 执行内容相同
- 区别：变更推送改为日报告模式（sweep 跑完一次性推）
- 关键事实翻转（P0）依然实时推

### 模式 C：单标的模式（手动触发 · `/wiki-sweep {标的}`）
- **只扫指定标的**，不扫全库（3-5 分钟 vs 全量 30-60 分钟）
- 执行范围：
  - **维度 M（盲区挖掘）**：调用 wiki-mine 对该标的做盲区体检，挖出的新变量直接进入下面的 C 档查证
  - A 档：只处理该标的相关的断链、registry 同步
  - C 档：对该标的的 macro-tracker 所有关键变量（含 mine 新挖出的）做轻量探查 + 必要的深度研究
  - 跨课题关联：只检查涉及该标的的关联
  - 生命周期：只处理该标的引用的变量
- 标的识别：支持中文名（"拼多多"）和 ticker（"PDD"），从 macro-tracker 文件名和 frontmatter 匹配
- 推送：同模式 A，关键变更实时推

> **单标的模式 = 盲区挖掘 + 变量查证 + 微信推送，一站式完成**。用户想"挖盲区"或"体检某个标的"时，直接用 `/wiki-sweep {标的}`。

🔴 **CHECKPOINT：模式判定**
- prompt 包含「cron 执行」或调用方为 wiki-cron.sh → 模式 B
- prompt 包含标的名称/ticker → 模式 C
- 否则 → 模式 A

---

## 完整执行流程

### 阶段 0：环境准备

1. 加载配置：
   - `.cron/config.sh`（推送配置、调度时间）
   - `.cron/snapshots/staging.json`（待二次确认的变量关停队列，不存在则空）
   - `context/finance.md`（持仓列表，识别 `priority: holding` 标的）

2. 初始化当日 changelog 段落（在 `wiki/changelog.md` 末尾追加）：
   ```
   ## YYYY-MM-DD HH:MM · wiki-sweep
   **汇总**：扫描中...
   ```

3. 创建**当日快照前置点**（如果今天还没有快照，调用 `snapshot-backup.sh`）

### 阶段 1：扫描收集

读取（按 priority 排序，holding 优先）：

- `wiki/index.md` — 计数对照
- `wiki/variables/registry.md` — 全局变量清单
- `wiki/variables/variables/*.md` — 关键变量档案（含 state_snapshot）
- `wiki/analysis/*/follow-ups/*.md` — macro-tracker（type: macro-tracker）
- `wiki/analysis/*/report.md` — 研究报告（仅 frontmatter + 结论段）
- `wiki/sources/*.md` — 知识卡片（仅 30 天内，最多 50 篇）
- `wiki/topics/*.md` — 主题页面
- `wiki/connections/index.md` — 跨课题关联索引
- `decisions/*.md` — 决策记录（status: open）
- `todos/active.md` — 待办

**优化**：大部分文件只读 frontmatter + 关键段落，不全读正文。只有以下情况读全文：
- macro-tracker（要刷变量值）
- 变量档案（要对比 state_snapshot）
- priority=holding 标的的 report

**模式 C（单标的）优化**：只读该标的的 tracker / report / 相关变量档案，跳过其他标的。A 档全局检查（如 index 计数）保留。

### 阶段 2：A 档处理（机械修复 · 直接执行）

**纯机械操作，不调 Claude（除例外）。直接改文件 + 写 changelog + 标 P2。**

#### A1. 索引计数同步
- 读 `registry.md` 实际变量数 vs `index.md` 中记录的"变量数"
- 不一致 → 直接更新 `index.md`
- changelog 写：`auto-fix index.md 变量数 X → Y`

#### A2. 断链修复（V1 维度 H）
- 扫描所有 `[[]]` 链接
- 检查目标文件是否存在
- 修复策略：
  - 文件改名了 → 通过模糊匹配找到新路径，更新链接
  - 文件不存在 → 移除链接，保留文本
  - 在 `raw/` 下的链接 → 跳过（不强制存在）
- changelog 写：`auto-fix {文件} 断链 → {目标}（已更新/已移除）`

#### A3. Topic 页面更新（V1 维度 E）
- 扫描 topics/ 下每个页面的 tags
- 找出 sources/ 下同标签但未关联的文章
- 直接追加到 topic 的 `## 关联文章`
- 超过 5 篇未关联才更新（避免频繁抖动）
- changelog 写：`auto-fix {topic} 关联 N 篇新文章`

#### A4. 孤立文章处理（V1 维度 G · 谨慎版）
- 扫描 sources/ 下无 tags 且无关联的文章
- 30 天内的孤立文章 → 自动补标签（基于内容 LLM 判定，单文章消耗极少 token）
- 30 天以前的 → 不动（避免大规模批量操作）
- changelog 写：`auto-fix {source} 自动补标签 [xxx, yyy]`

#### A5. registry 与 macro-tracker 同步
- macro-tracker 里提到但未在 registry 登记的变量 → 自动加入 registry（分配新 ID）
- changelog 写：`auto-fix registry.md 新增变量 V{XXX}（来自 {标的} tracker）`

### 阶段 3a：C 档轻量探查（每个变量档案 · 调 Claude）

**对所有变量档案（registry 中 status: active 的变量 + 关键 tracker），调 Claude 做轻量状态对比。**

#### 执行方式

对每个变量档案执行：

```
prompt 模板（给 Claude 的子任务）：

变量 ID: V006
变量名: Warsh Fed Chair
当前 state_snapshot: Warsh 是 Fed Chair 热门候选人（截至 2026-04-15）
影响标的: BABA, TSLA, QCOM（持仓标的）

任务：
1. 用 web search 查"Warsh Fed Chair 2026"最新进展
2. 对比 state_snapshot 与当前查到的状态
3. 输出 JSON 判定：
   {
     "verdict": "NO_CHANGE | STATE_CHANGED | EVIDENCE_CONFLICT | LIKELY_DEAD",
     "confidence": "high | medium | low",
     "old_snapshot": "采集时的状态描述",
     "new_snapshot": "现在的状态描述（如变化）",
     "evidence_urls": ["url1", "url2"],
     "needs_deep_research": true | false
   }
```

#### 判定后路由

| Verdict | Confidence | 路由 |
|---------|-----------|------|
| NO_CHANGE | 任意 | 跳过，不进 3b |
| STATE_CHANGED | high | 进 3b 队列（高优先级） |
| STATE_CHANGED | low/medium | 进 3b 队列（让 3b 验证） |
| EVIDENCE_CONFLICT | 任意 | 进 3b 队列 + 标 disputed |
| LIKELY_DEAD | 任意 | 进 staging 关停队列（不进 3b） |

#### P0 实时推送

STATE_CHANGED + confidence: high + 涉及 holding 标的 → **立即推微信**：

```
wechat-push.sh urgent "V006 Warsh 状态翻转" "旧：Warsh 热门候选人 → 新：Trump 转向 Powell 连任"
```

不积压到日报告，发生即推。

#### Token 预算

每个变量档案查证约 30-50K tokens。预算无硬上限，但单次 sweep 总 token 若超过 5M → 暂停，写入日志待续。

### 阶段 3b：C 档深度聚焦研究（仅 3a 标记的变量 · 调 Claude）

**对 3a 标记 STATE_CHANGED / EVIDENCE_CONFLICT 的变量，走聚焦研究。**

#### 执行方式

对每个待研究变量：

```
prompt 模板：

变量 ID: V006
变化类型: STATE_CHANGED
3a 输出:
  - old: Warsh 热门候选人
  - new: Trump 转向 Powell 连任
  - 证据: [url1, url2]

任务（聚焦模式 · 只研究变化部分）：
1. 验证变化是否真实（多源交叉）
2. 评估变化对持仓标的的影响（BABA/TSLA/QCOM）
3. 更新变量档案:
   - state_snapshot 字段 → 新值
   - analysis 区追加（永不覆盖）：
     [auto: YYYY-MM-DD] 状态从「{old}」→「{new}」
     证据：[来源1][来源2]
     对持仓影响：xxx
4. 更新 registry.md 该变量的"当前状态"列
5. 更新所有引用该变量的 macro-tracker 的「关键变量」表

输出：
- 更新了哪些文件
- 对持仓的核心影响判断
- 是否触发任何"确认规则"
```

#### 关键约束

- **analysis 区只追加**：用 `[auto: YYYY-MM-DD]` 标记，不覆盖用户手写内容
- **state_snapshot 可覆盖**：这是数据字段，错了就改
- **registry 同步更新**：变量的"当前状态"列必须同步
- **macro-tracker 引用同步**：所有引用该变量的 tracker 的当前值列同步

#### 输出

每条 3b 处理都进 changelog：
```
auto-update V006 Warsh Fed Chair
  → state: Warsh 候选 → Powell 连任
  → 影响: 利率路径预期稳定，BABA/TSLA 估值天花板抬升
  → 文件: variables/warsh-fed-chair.md, registry.md, 3 个 tracker
```

### 阶段 4：跨课题关联检查（V1 维度 K · 强化版）

**扫描未记录的跨课题关联。传导链 ≥2 跳 + 数据支撑才写入 connections/。**

1. 读取所有 macro-tracker 的「变量地图」段
2. 检查未在 `edges.md` 记录的潜在传导链
3. 对每条候选链：
   - 找 ≥2 个数据点支撑（否则丢弃）
   - 至少 2 跳（A→B→C，不能直接 A→B）
4. 通过校验的 → 创建 connection 卡片到 `wiki/connections/`
5. 加入 `edges.md` 的"跨板块传导边"或"跨标的联动边"
6. changelog 写：`auto-conn {标的A} ↔ {标的B} {联动机制}`

### 阶段 5：变量生命周期管理

#### 6.1 关停 staging 队列处理

读取 `.cron/snapshots/staging.json`：

```json
{
  "pending_close": [
    {
      "variable_id": "V302",
      "first_detected": "2026-07-01",
      "last_check": "2026-07-05",
      "consecutive_likely_dead": 2,
      "evidence": "..."
    }
  ]
}
```

规则：
- `consecutive_likely_dead >= 3`（连续 3 次 sweep 同样判断）→ 关停落地
  - registry 中状态改为 `closed`
  - 变量档案加标记 `status: closed (auto: YYYY-MM-DD)`
  - **不删除档案**（保留历史）
  - changelog 写：`auto-closed V302 {变量名}`
- 否则 → `consecutive_likely_dead++`，留在 staging

#### 6.2 新增疑似失效变量

阶段 3a 输出 LIKELY_DEAD 的变量 → 加入 staging：
- 已在 staging → 更新 `last_check` + `consecutive_likely_dead++`
- 不在 staging → 新建条目

changelog 写：`auto-close-pending V302 进入关停 staging（连续 N 次）`

### 阶段 6：跟进项处理（保留 V1 维度 A/C/D/J · 半自动）

**这些维度涉及用户判断优先级，半自动：自动写入 sweep-issues.md，不自动创建 follow-up。**

- 维度 A：研究报告遗漏跟进
- 维度 C：决策到期复盘
- 维度 D：长期待办缺失
- 维度 J：研究数据时效性（C 档已处理变量级别的数据时效）

写入 `.cron/sweep-issues.md`，标记 `- [ ]`。下次对话提醒用户。

**注**：维度 I（待办分类）、F（标签合并建议）、M5（盲区变量挖掘建议）依然输出建议但不自动执行——这些是策略层判断，不是机械操作。

### 阶段 7：输出与推送

#### 7.1 完成 changelog 段落

更新 `wiki/changelog.md` 当日段落的「汇总」行：

```
## 2026-07-05 23:15 · wiki-sweep

**汇总**：变更 12 条（P0 ×1，P1 ×3，P2 ×8）

#### P0 实时推送
- `auto-state V006 Warsh Fed Chair` → variables/warsh-fed-chair.md
  旧: Warsh 热门候选人 / 新: Trump 转向 Powell 连任
  影响: BABA/TSLA 估值
  [详细...]

#### P1 日报告
- `auto-update 拼多多 tracker` → ...
- `auto-conn PDD ↔ BABA` → ...
- `auto-close-pending V302` → ...

#### P2 仅 changelog
- `auto-fix index.md` 计数 63 → 67
- `auto-refresh V001 10Y` 4.20% → 4.35%
- `auto-fix registry.md` 新增 V016
- ...

#### 待二次确认
- V302（连续 2 次判定失效，待第 3 次确认）
```

#### 7.2 微信推送

**模式 A（交互）**：
- P0 实时推：3a 阶段已经推过
- 末尾给用户摘要（不另外推微信，因为用户在对话中）

**模式 B（cron）**：
- P0 实时推：3a/3b 阶段发生即推
- 日报告：sweep 跑完调用 `wechat-push.sh daily-report`
  - 有变更：逐条列出（不只数量）
  - 无变更：推一条「今日 N 条变更，全部自动处理完毕」

#### 7.3 更新 sweep-issues.md

- 阶段 6 的跟进项写入
- 上一轮未解决的不重复写

#### 7.4 更新 wiki/log.md

追加一行：`- YYYY-MM-DD HH:MM: sweep V2 完成（P0×N P1×N P2×N）`

#### 7.5 触发快照

调用 `snapshot-backup.sh`（创建当日快照，sweep 后状态）

---

## 反例与黑名单

1. **不要覆盖 tracker 的 analysis 段**：永远只追加 `[auto: YYYY-MM-DD]` 标记的新段落。
2. **不要在 A 档调 Claude 做研究**：A 档是纯机械操作，不调 LLM（A4 孤立文章补标签除外，那是单文件小操作）。
3. **不要把数据刷新和分析拆开**：数据变化本身就是 C 档研究的触发信号，不存在"只刷数据不动分析"。
4. **不要静默关停变量**：所有变量关停必须经 staging 队列 3 次连续判定，且 changelog 必须记录。
5. **不要修改 `raw/` 目录下任何文件**。
6. **不要修改 `context/` 目录下任何文件**（用户维护，AI 只读）。
7. **不要删除任何文件**：关停变量只是状态变更，不删除档案。
8. **不要跳过 staging 二次确认**：即使 confidence: high 的 LIKELY_DEAD 也要进 staging，不能直接关停。
9. **不要在阶段 3a 省略搜索**：每个变量档案都要查最新状态，不能用"上次查过没变"为借口跳过。
10. **不要在阶段 4 创建无数据支撑的关联**：传导链必须 ≥2 跳 + 数据点支撑，否则丢弃。

## 失败模式

| ID | 触发 | 处理 |
|----|------|------|
| F-PRE | WIKI_ROOT 未设置 | 终止 |
| F-API | C 档探查中 MCP 工具调用失败 | 跳过该变量本次探查，changelog 标记 `data-fetch-failed`，下次再试 |
| F-LLM | Claude 调用超时 | 跳过该变量，下次再查 |
| F-STAGING | staging.json 解析失败 | 视为空 staging，从头开始 |
| F-CONFLICT | 用户在 sweep 期间手改了同一文件 | 以用户版本为准，sweep 改动放弃 |

## 路径约定

- 变量档案：`$WIKI_ROOT/wiki/variables/variables/{slug}.md`
- registry：`$WIKI_ROOT/wiki/variables/registry.md`
- edges：`$WIKI_ROOT/wiki/variables/edges.md`
- macro-tracker：`$WIKI_ROOT/wiki/analysis/{标的}/follow-ups/macro-tracker.md`
- changelog：`$WIKI_ROOT/wiki/changelog.md`
- staging：`$WIKI_ROOT/.cron/snapshots/staging.json`
- sweep-issues：`$WIKI_ROOT/.cron/sweep-issues.md`
- 微信推送：`source $WIKI_ROOT/.cron/scripts/wechat-push.sh`

## 关联资源

- 备份：`.cron/scripts/snapshot-backup.sh`（每日快照 + 恢复）
- 推送：`.cron/scripts/wechat-push.sh`（微信推送 wrapper）
- V1 扫描维度：保留 A-M 全部维度，本文件描述如何将它们分到两档执行
- cron 调度：`.cron/config.sh` 中的 SWEEP_DAY/SWEEP_TIME

## 手动触发防重复

手动执行完 sweep 后，**必须**追加一条 last-run 记录：

```bash
echo "wiki-sweep=$(date +%s)  # $(date '+%Y-%m-%d %H:%M')" >> "$WIKI_ROOT/.cron/logs/last-runs.txt"
```
