# AI Wiki Engine

基于 Claude Code 的个人知识管理系统。核心理念：**用户只做三件事——存、问、研究**，中间的整理、分析、跟踪全交给 AI。

## 六条核心路径

```
路径 1：看到文章 → 发链接 → digest 自动入库成知识卡片
路径 2：有想法 → 说"研究XX" → research 产出分析专题 → 生成跟踪项 & 决策
路径 3：有事情 → 说"加个待办" → todo 管理待办和跟踪项
路径 4：定时任务 → review 扫描到期决策 → 提醒复盘
路径 5：定时任务 → sweep 扫描知识库 → 发现遗漏的跟进项
路径 6：用户提问 → AI 读取知识库 → 基于已有内容回答
```

## 快速开始

### 1. 部署

```bash
cd wiki-engine
./scripts/deploy.sh --target ~/my-wiki
cp -r skills/* ~/.claude/skills/
```

部署后的目录结构：

```
my-wiki/
├── inbox/              # 待处理（文章入口）
├── raw/                # 原文归档（按月 YYYY-MM/，不可变）
├── context/            # 个人上下文（用户维护，AI 只读）
├── decisions/          # 决策日志（AI 写入，用户回看）
├── todos/              # AI 待办管理
│   ├── active.md       #   待办 + 跟踪项（按课题分组）
│   └── archive/        #   按周归档（YYYY-WXX.md）
├── wiki/               # AI 维护的知识库
│   ├── index.md        #   总索引
│   ├── log.md          #   处理日志
│   ├── sources/        #   文章摘要卡片
│   ├── topics/         #   跨文章聚合主题
│   ├── analysis/       #   课题研究工作区（含 follow-ups/）
│   └── .cron-logs/     #   定时任务日志
├── scripts/            # 定时任务脚本
├── CLAUDE.md           # 治理文件（AI 入口）
└── skills/             # CC Skills 源码（wiki-engine 仓库内）
```

### 2. 配置个人上下文（可选）

在 `context/` 下创建个人资料文件，供 AI 研究时参考：

```bash
cat > context/finance.md << 'EOF'
# 投资上下文
## 风险偏好
...
## 当前持仓
...
EOF
```

### 3. 配置定时任务

```bash
# 添加到 crontab（每分钟检查一次）
crontab -e
# * * * * * cd /path/to/my-wiki && ./scripts/cron-check.sh
```

## 自动化任务

所有定时任务由 `cron-check.sh` 统一调度（每分钟检查，按时间触发），支持合盖/断网后自动补跑：

| 任务 | 调度时间 | 做什么 | token 消耗 |
|------|---------|--------|-----------|
| **digest** | 每天 23:00 | 检查 inbox/，有新文章就自动处理入库 | inbox 非空才消耗 |
| **sweep** | 每周六 23:15 | 扫描知识库，找遗漏跟进项 | shell 粗筛零 token，有问题才调 Claude |
| **review** | 每月 1/15 号 23:15 | 扫描到期决策，提醒复盘 | 同上 |
| **todo-remind** | 每天 11:00 & 23:00 | 统计待办数写入 pending.md 提醒用户 | 零 token |

任务执行流程：

```
cron-check.sh（每分钟，零 token）
  ├── 未到调度时间 → 跳过（静默）
  ├── 到时间 + 无事可做 → 跳过（零 token）
  └── 到时间 + 有工作 → 调用 wiki-cron.sh
      ├── 阶段1: shell 粗筛
      │   ├── ALL CLEAR → 记录时间戳，结束（零 token）
      │   └── 发现问题 → 阶段2
      └── 阶段2: Claude 精析 → 写 pending.md 通知用户
```

## Skills

| Skill | 触发方式 | 说明 |
|-------|---------|------|
| `wiki-digest` | 发链接、说"收录"/"处理" | 抓取文章、生成知识卡片、归档原文、更新索引 |
| `wiki-research` | 说"研究"/"调研"/"了解" | 开题→搜集→分析→报告→入库→跟进 |
| `wiki-todo` | 说"加个待办"/"完成了"/"看看待办" | 添加、完成、删除、编辑、归档待办 |
| `wiki-review` | 说"复盘"/"回顾"，或 cron | 扫描到期决策，逐条复盘，记录教训 |
| `wiki-sweep` | 说"扫一下"/"检查遗漏"，或 cron | 四维度扫描遗漏跟进项 |

## 核心流程详解

### 文章收录（Digest）

```
用户发链接 → 抓取全文 → inbox/ → 生成知识卡片 → wiki/sources/ → 原文归档 raw/ → 更新索引
```

- 每篇文章生成一张知识卡片：一句话摘要、关键要点、标签、关联文章
- 同标签积累到 3 篇自动生成主题页面（topics/）
- 入库时自动检查是否与已有跟进项相关
- 支持批量 URL 和用户自写文章（`source: self`）

### 课题研究（Research）

```
用户说"研究 XX" → 开题确认 → 搜集资料 → 研究分析 → 输出报告 → 入库 → 生成跟进项
```

- 研究产出分两类：
  - **知识型**（方法论、原理）→ 入库到 sources/
  - **决策型**（个股分析、个人建议）→ 保留在 analysis/ + 创建决策记录
- 研究完成后**必须**向用户确认跟进项（数据变化、指标跟踪等）
- 自动读取 `context/` 下的个人上下文，给出针对性建议

### 待办管理（Todo）

```
说"加个待办" → 写入 todos/active.md → 工作/个人分区 + 长期项
说"完成了XX" → 移到已完成区 → 周归档时汇总
```

- 自动判断工作/个人分类
- 长期待办关联跟进项和跟踪项
- 跟踪项按课题分组（如"拼多多"、"高通"）

### 遗漏扫描（Sweep）

四个维度检查知识库健康度：

| 维度 | 检查内容 |
|------|---------|
| A. 研究缺跟进 | 报告有值得跟踪的发现但没有 follow-up |
| B. 文章缺主题 | 同标签 >=3 篇但没创建 topic 页面 |
| C. 决策缺跟踪 | 决策有量化预期但没有跟踪机制 |
| D. 待办不一致 | follow-up 和长期待办不对应 |

### 决策复盘（Review）

```
定时扫描 → 找到到期决策 → 展示当时的决策/理由/预期 → 用户回顾实际结果 → 记录教训
```

### 知识问答

```
用户提问 → 读取 wiki/index.md → 定位相关 sources/topics/analysis → 基于知识库回答
```

## 文件格式

### 知识卡片（sources/）

```markdown
---
title: 文章标题
date: YYYY-MM-DD
tags: [标签1, 标签2]
source: article | self | research
origin: 原文URL
related: [[关联页面]]
---

## 一句话
50字以内的摘要。

## 关键要点
- 要点1
- 要点2

## 原文
→ [[raw/文件名]]
```

### 决策记录（decisions/）

```markdown
---
title: 决策标题
date: YYYY-MM-DD
tags: [投资/职业/生活]
status: open
review_date: YYYY-MM-DD
context: analysis/xxx
---

## 决策
做了什么决定。

## 当时理由
为什么这样做。

## 预期
期待什么结果。

## 复盘
（待复盘）
```

### 跟进项（analysis/{topic}/follow-ups/）

```markdown
---
topic: 跟踪项名称
parent: analysis/{课题名}
cadence: daily | weekly | monthly
status: active | closed
created: YYYY-MM-DD
---

## 为什么要跟踪
研究报告中的哪个发现触发了这个跟踪项。

## 跟踪记录
- **YYYY-MM-DD**: 初始状态——xxx
- **YYYY-MM-DD**: 更新——xxx
```

## 设计原则

1. **用户只做输入，AI 负责中间过程**：发链接、提问题、说研究方向，其余交给 AI
2. **以问代找**：知识库是给 AI 读的，用户只需提问，不需要自己翻文件
3. **知识库和个人数据分离**：wiki/ 是关于世界的知识，context/ 是关于用户自身的信息，decisions/ 是决策
4. **标签自动管理**：AI 自动打标签，同类文章多了自动聚合成主题
5. **决策可复盘**：每条决策有 review_date，定期回顾实际结果，持续改进判断力
6. **研究可跟踪**：研究完成后强制确认跟进项，定时扫描防止遗漏
7. **低 token 消耗**：shell 粗筛优先，无事可做零 token

## 依赖

- Claude Code CLI
- webReader MCP（抓取网页内容）
- WebSearch（课题研究时搜索资料）
