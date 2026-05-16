---
name: wiki-sweep
description: 扫描知识库健康度。检查遗漏跟进项 + 知识库维护（topic 更新、标签碎片、孤立文章、断链）。触发：用户说"扫一下"、"检查遗漏"、"整理知识库"、"sweep"，或由 cron wrapper 自动触发
---

# Sweep — 知识库健康度扫描

## 触发条件

- 用户说"扫一下"、"检查遗漏"、"有没有漏掉的"、"整理知识库"、"sweep"
- cron wrapper 传入粗筛结果时自动触发

## 前置检查

当前目录下必须有 `wiki/`、`todos/` 目录。如果没有，报错并提示用户切换到 wiki 根目录。

## 执行模式

### 模式 A：交互模式（用户手动触发）

完整扫描所有维度。

### 模式 B：精析模式（cron wrapper 传入粗筛结果）

当 prompt 中包含「粗筛已发现以下问题」时激活。此时：
- **跳过全量扫描**，直接基于粗筛结果分析
- 只读取粗筛命中的文件做深度判断
- **不自动创建跟进项**（cron 无用户确认），仅输出分析报告和 NOTIFY 通知
- 用户下次交互时可说"处理 sweep 结果"来确认创建

### 粗筛结果格式

粗筛脚本输出以换行分隔，每行一条，竖线分隔字段：

| 类型 | 格式 | 示例 |
|------|------|------|
| 到期决策 | `OVERDUE_DECISION\|文件路径\|标题\|review_date` | `OVERDUE_DECISION\|decisions/pdd.md\|PDD投资决策\|2026-05-01` |
| 缺跟进的研究 | `MISSING_FOLLOWUP\|report路径\|课题名` | `MISSING_FOLLOWUP\|wiki/analysis/pdd/report.md\|拼多多` |
| 过期跟进项 | `STALE_FOLLOWUP\|文件路径\|跟踪项名\|最后跟踪日期` | `STALE_FOLLOWUP\|wiki/analysis/pdd/follow-ups/半托管.md\|半托管模式\|2026-04-01` |
| Topic 缺更新 | `STALE_TOPIC\|topic路径\|标题\|未关联篇数` | `STALE_TOPIC\|wiki/topics/拼多多.md\|拼多多\|3 篇未关联` |
| 孤立文章 | `ORPHAN_SOURCE\|source路径\|标题\|原因` | `ORPHAN_SOURCE\|wiki/sources/2026-05-01-xxx.md\|XXX文章\|无标签无关联` |
| 断链 | `BROKEN_LINK\|源文件路径\|源文件标题\|断链目标` | `BROKEN_LINK\|wiki/sources/xxx.md\|文章A\|sources/不存在文件` |
| 标签统计 | `TAG_STATS\|all\|标签统计\|多行统计` | `TAG_STATS\|all\|标签统计\|10 股票\n8 技术` |

精析时按类型映射到对应扫描维度：OVERDUE_DECISION → C，MISSING_FOLLOWUP → A，STALE_FOLLOWUP → A，STALE_TOPIC → E，ORPHAN_SOURCE → G，BROKEN_LINK → H，TAG_STATS → F。

## 执行步骤

### 1. 收集扫描素材

**交互模式**：读取以下内容（如果目录/文件不存在则跳过该来源，不报错）：
- `wiki/log.md` — 读取全部，按行内日期筛选最近 30 天（`grep` 含当前月份和上月日期的行）
- `wiki/analysis/*/report.md` — 所有研究报告
- `wiki/analysis/*/follow-ups/*.md` — 所有跟进项（通过 frontmatter `status: active` 筛选）
- `todos/active.md` — 当前待办
- `decisions/*.md` — 通过 frontmatter `status: open` 筛选的决策

**精析模式**：只读取粗筛结果中列出的文件路径。如果路径不存在，跳过该条并记录。

### 2. 扫描维度

逐项检查以下遗漏（精析模式只检查粗筛命中的维度）：

**A. 研究报告遗漏跟进**
- 读 report.md 的 `## 核心发现` 和 `## 未解决问题`
- 检查同级 `follow-ups/` 目录是否存在且非空
- **值得跟踪的判定标准**（满足任一即标记）：
  - 包含具体数据/数字（如"占比 35%"、"增长 20%"）
  - 涉及持续变化的指标（业务数据、市场份额、技术采纳率）
  - 有明确的"需要关注"、"值得跟踪"、"后续观察"等表述
  - 未解决问题中有可验证的假设
- **不值得跟踪**：纯观点、历史事实、一次性事件、通用知识
- 创建前先检查同课题下是否已有相同主题的跟进项（防重复）

**B. 文章内容遗漏跟进**（仅交互模式）
- 读取 `wiki/sources/` 下 frontmatter `date` 在 30 天内的卡片（限制最多 50 篇，避免 token 过高）
- 按标签聚合，如果同一标签累计 >=3 篇且 topics/ 下无对应主题页面，标记为建议跟进
- 标签匹配：frontmatter 中 `tags` 数组的交集，非精确匹配

**C. 决策遗漏跟进**
- 读 decisions/ 下 frontmatter `status: open` 的决策
- 如果决策涉及可量化的预期（如"预计营收增长20%"）但没有对应 follow-up，标记为遗漏
- review_date 已过但未复盘的，也标记为遗漏

**D. 长期待办缺失**
- 读 `todos/active.md` 中 `### 长期` 下的待办
- 检查每个长期待办是否有对应的 follow-up 文件（反向检查）
- 检查每个 active follow-up 是否有对应的长期待办
- 不一致项标记为遗漏

**E. Topic 页面缺更新**
- 读 `wiki/topics/` 下每个 topic 页面的 tags
- 扫描 `wiki/sources/` 下同标签的文章
- 对比 topic 的 `## 关联文章` 列表，找出未关联的文章
- 超过 2 篇未关联 → 建议更新 topic
- 更新方式：追加新文章到 `## 关联文章`，如有必要更新 `## 核心发现`

**F. 标签碎片**
- 读取粗筛输出的标签统计（TAG_STATS）
- 识别可能重复的标签：
  - 同义词（如"拼多多" vs "PDD" vs "Pinduoduo"）
  - 单复数（如"芯片" vs "芯片产业"）
  - 中英文混用（如"AI" vs "人工智能"）
- 建议合并方向，不自动执行
- 标签统一后需要批量更新对应 sources 的 frontmatter

**G. 孤立文章**
- 检查 `wiki/sources/` 下无标签且无 `[[]]` 关联的文章
- 这些文章难以被检索到，建议：
  - 补充标签（如果内容明确）
  - 建立关联（如果与已有文章/主题相关）
  - 标记为待清理（如果内容过时或质量差）

**H. 断链检查**
- 提取所有 `[[]]` 链接，检查目标文件是否存在
- 跳过 `raw/` 下的链接（原文归档不强制存在）
- 断链原因可能是：文件被重命名、路径变更、未创建
- 建议修复方式：更新链接指向或创建缺失文件

### 3. 输出扫描结果

汇总分为两部分：**跟进项** + **知识库维护**，按优先级排序：

```
扫描完成，发现 X 个问题：

## 跟进项（需要用户确认）

🔴 应该跟进但完全缺失：
1. [研究：PDD] 半托管模式占比变化（报告核心发现，无跟进项）
2. [决策：xxx] xxx（决策有预期但无跟踪）

🟡 可能值得跟进：
3. [文章] 新能源主题已有 4 篇文章，建议持续跟踪行业趋势

🟢 已有跟进但缺少长期待办：
4. [PDD 半托管模式] follow-up 存在但 todos 里没有对应项

## 知识库维护（可一键修复）

📦 Topic 需更新：
5. [拼多多] 有 3 篇新文章未关联到 topic 页面

🏷️ 标签碎片：
6. "PDD"(2篇) 和 "拼多多"(5篇) 可能是同一概念，建议合并

🔗 断链：
7. [文章A] → sources/不存在文件.md（目标不存在）

📄 孤立文章：
8. [XXX文章] 无标签无关联，建议补充分类

回复"全部处理"处理所有项，或指定编号（如"1 3 5"）选择性处理。
回复"只修维护"只处理知识库维护部分（5-8）。
```

**精析模式**：同上格式，但末尾改为"此结果由定时扫描发现，下次对话时可确认处理。"

### 4. 处理确认结果（仅交互模式）

用户确认后，分别处理两类问题：

**跟进项创建**（1-4 号）：
1. 在对应课题的 `follow-ups/` 下创建跟踪文件（格式见 CLAUDE.md follow-up 格式）
2. 在 `todos/active.md` 对应分类的 `### 长期` 下添加：`- [ ] 跟进：{课题名} - {跟踪项} \`cadence: monthly\` \`created: YYYY-MM-DD\``
3. 更新 `wiki/log.md`：`- YYYY-MM-DD HH:mm: sweep 创建了 {N} 个跟进项`

**知识库维护**（5-8 号）：
- **Topic 更新**：读取未关联的文章，提取摘要追加到 topic 的 `## 关联文章`，如有重要新发现更新 `## 核心发现`
- **标签合并**：将碎片标签统一（如 PDD → 拼多多），批量更新对应 sources 的 frontmatter
- **断链修复**：更新 `[[]]` 指向正确路径，或创建缺失文件
- **孤立文章补充**：为孤立文章打标签、建关联，或标记为待清理

## 通知输出

回复末尾**必须**输出一行 NOTIFY（供 cron wrapper 解析，用 `grep "^NOTIFY:"` 提取）：

```
NOTIFY: sweep 完成：发现 X 个跟进项 + Y 个维护项
```

- 有问题：`NOTIFY: sweep 完成：发现 2 个跟进项 + 3 个维护项`
- 无问题：`NOTIFY: sweep 完成：一切正常`
- 精析模式有问题：`NOTIFY: sweep 完成：发现 1 个跟进项 + 2 个维护项（待确认）`

## 关联资源

- 粗筛脚本：`scripts/sweep-check.sh`（cron 模式下由 wiki-cron.sh 调用，输出粗筛结果）
- 决策记录格式：参见 `CLAUDE.md` 中「决策记录格式」
- follow-up 格式：参见 `CLAUDE.md` 中「follow-up 格式」
- cron 调度：`wiki/.cron/config.sh` 中的 SWEEP_DAY/SWEEP_TIME 配置

## 路径约定

所有路径基于当前工作目录（相对路径）。不使用配置文件，不向上查找。
