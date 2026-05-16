---
name: wiki-research
description: 课题研究流程。搜集资料、分析研究、输出报告、知识入库。触发：用户说"研究XX"、"调研XX"、"帮我了解XX"、"深入看看XX"
---

# Research — 课题研究流程

## 触发条件

- 用户说"研究"、"调研"、"了解"、"深入看看"，并带有明确主题
- 示例："研究成交量择时因子"、"调研 Rust 异步运行时"

**缺少主题时**：回复"研究什么主题？"并停止执行。

## 前置检查

1. 检查 `inbox/`、`raw/`、`wiki/` 三个目录是否存在
2. 检查 `wiki/analysis/` 是否存在（不存在则创建）
3. 检查 `context/` 是否存在（如存在，列出文件供后续按需读取）
4. 检查 `decisions/` 是否存在（不存在则创建）
5. 不满足时报错并提示切换到 wiki 根目录

## 执行步骤

### 1. 开题

1. 读取 `wiki/index.md` 了解知识库全貌
2. 检查 `context/` 下是否有相关个人上下文（如涉及投资读 `finance.md`），纳入研究背景
3. 扫描 `wiki/sources/` 和 `wiki/topics/` 下已有相关内容，标记已知/未知
4. 创建 `wiki/analysis/{课题slug}/plan.md`（slug 规则：英文小写+短横线，中文保留）

plan.md 格式：
```markdown
---
topic: 课题名称
created: YYYY-MM-DD
status: active
---

## 研究问题
要回答的核心问题（1-3个）。

## 已有认知
来自知识库的已有内容（引用 sources/），如无则写"知识库暂无相关内容"。

## 信息缺口
还不知道、需要搜索的方向。

## 搜索计划
- 方向1：具体搜索关键词和目标
- 方向2：...

## 进度
- [x] 开题
- [ ] 搜集资料
- [ ] 研究分析
- [ ] 输出报告
- [ ] 知识入库
```

**向用户展示 plan.md 的核心内容**（研究问题、已有认知、信息缺口），确认研究方向。用户可以补充/调整/直接确认。

### 2. 搜集资料

基于 plan.md 的搜索计划搜集：

**搜索策略**：
- 每个方向用 WebSearch 搜索，关键词从搜索计划提取
- 多个独立方向可并行（使用 Agent 工具）
- 每轮搜索 3-5 个查询，优先中文内容（除非用户指定英文源）

**抓取规则**：
- 搜索结果中有价值的页面，用 `mcp__web_reader__webReader` 抓取全文
- 保存到 `wiki/analysis/{课题slug}/materials/`，文件名 `001-简短标题.md`、`002-...`
- 每个文件 frontmatter：`url`、`title`、`fetched`
- 抓取失败的 URL 跳过，在 notes.md 记录失败原因

**知识库交叉**：同时从 `wiki/sources/` 找已有相关文章，在 notes.md 引用。

### 3. 研究分析

创建 `wiki/analysis/{课题slug}/notes.md`，逐篇阅读 materials/：

- 记录关键发现（标注来源 material 编号）
- 交叉验证不同来源
- 标记信息缺口
- **信息冲突处理**：不同来源说法矛盾时，在 notes.md 标注 `[冲突]`，记录各方来源和依据，向用户汇报让用户判断

**多轮循环**：发现缺口 → 回到步骤 2 继续搜索 → 补充到 materials/ → 继续分析
- 最多 3 轮，避免无限搜索
- 每轮完成后向用户简要汇报发现

**用户反馈点**：
- "继续深入 XX" → 补充搜索
- "差不多了，出报告" → 进入步骤 4
- 不回复 → 继续下一轮

### 4. 输出报告

创建 `wiki/analysis/{课题slug}/report.md`：

```markdown
---
topic: 课题名称
created: YYYY-MM-DD
completed: YYYY-MM-DD
status: completed
---

## 结论
对研究问题的直接回答（简洁明确）。

## 核心发现
- 发现1（→ 来源：materials/001-xxx.md）
- 发现2（→ 来源：materials/002-xxx.md）

## 个人建议（基于 context/）
<!-- 如读取了个人上下文，给出针对性建议；否则省略此节 -->

## 详细分析
### 子问题1
...

### 子问题2
...

## 未解决问题
- ...

## 参考资料
- [资料标题1](materials/001-xxx.md) - 一句话概括
- [资料标题2](materials/002-xxx.md) - 一句话概括
- 外部链接...
```

更新 plan.md 状态为 `completed`。

向用户展示**结论和核心发现**（不展示完整报告）。

### 5. 讨论与补充

报告输出后，用户可能继续追问和讨论：

- 有价值的讨论追加到 `notes.md` 的 `## 讨论记录`，按日期：`### YYYY-MM-DD：主题`
- 讨论产生新决策 → 更新 `decisions/`
- 发现新信息缺口 → 更新 notes.md

### 6. 知识入库（可选）

判断产出类型：

**知识型**（方法论、原理、通用框架）：
1. 创建知识卡片到 `wiki/sources/`，`source: research`，`origin: analysis/{slug}/report.md`
2. 更新相关 topic，达到 3 篇时创建新 topic
3. 更新 `wiki/index.md` 和 `wiki/log.md`

**决策型**（时效性分析、个股研究、个人建议）：
- 报告保留在 `analysis/`，不入库
- 在 `decisions/` 创建 `YYYY-MM-DD-简短标识.md`（含决策内容、理由、预期、review_date 默认 3 个月后）
- 写入 `wiki/log.md`

**判断规则**：
- 特定个股/行业当前状态 → 决策型
- 通用方法论/原理/框架 → 知识型
- 不确定时问用户："这是时效性分析，建议保留在 analysis/ 不入库，可以吗？"

### 7. 跟进（强制，不可跳过）

研究完成后 **必须** 执行：

1. 回顾 report.md 的核心发现和未解决问题
2. 识别需要持续跟踪的项（数据变化、业务指标、政策动态、技术演进）
3. 向用户确认：
   ```
   以下发现可能需要持续跟进：
   1. xxx（如：PDD 半托管模式占比变化）
   2. xxx
   需要创建跟踪项吗？可以选择全部或指定编号。
   ```
4. 用户确认后：
   - 在 `follow-ups/` 下创建跟踪文件（格式见 CLAUDE.md）
   - 在 `todos/active.md` 对应分类的 `### 长期` 下添加：`- [ ] 跟进：{课题} - {项} \`cadence: monthly\` \`created: YYYY-MM-DD\``
5. 用户说不需要 → 仅记录到 log.md

## 错误处理

- **搜索无结果**：告知用户，建议调整关键词或方向
- **网页抓取失败**：跳过，在 notes.md 记录失败 URL，继续其他来源
- **课题目录已存在**：检查 plan.md status，active → 问是否继续；completed → 问是否重新研究
- **信息严重不足**：诚实告知已获取信息和剩余缺口，由用户决定是否继续

## 关联资源

- 研究产出格式：参见 `CLAUDE.md` 中「plan.md 格式」「report.md 格式」「follow-up 格式」
- 决策记录格式：参见 `CLAUDE.md` 中「决策记录格式」
- 知识卡片格式：参见 `CLAUDE.md` 中「知识卡片格式」
- 待办管理：`todos/active.md`（由 wiki-todo 管理，research 写入跟踪项）
- 抓取工具：`mcp__web_reader__webReader`

## 路径约定

所有路径基于当前工作目录（相对路径）。不使用配置文件，不向上查找。
