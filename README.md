# AI Wiki Engine

基于 Claude Code 的个人知识管理系统。用户只做三件事：**存、问、研究**。中间的整理、调研、分析，全是 AI 的事。

## 核心能力

| 能力 | 触发方式 | 说明 |
|------|---------|------|
| 文章收录 | 发链接 → `/wiki-digest` | 抓取文章、生成知识卡片、归档原文 |
| 课题研究 | 说"研究 XX" → `/wiki-research` | 搜集资料、分析研究、输出报告 |
| 决策复盘 | 说"复盘" → `/wiki-review` | 定期回顾决策，记录教训 |
| 知识问答 | 直接提问 | 基于知识库内容回答 |

## 快速开始

### 1. 初始化

```bash
# 克隆本仓库，改名为你想要的知识库名称
git clone <repo-url> my-wiki
cd my-wiki

# 复制模板创建目录结构
cp -r template/* .
```

初始化后的目录结构：

```
my-wiki/
├── inbox/              # 待处理（文章入口）
├── raw/                # 原文归档（按月 YYYY-MM/，不可变）
├── context/            # 个人上下文（用户维护，AI 只读）
├── decisions/          # 决策日志（AI 写入，用户回看）
├── wiki/               # AI 维护的知识库
│   ├── index.md        #   总索引
│   ├── log.md          #   处理日志
│   ├── sources/        #   文章摘要卡片
│   ├── topics/         #   跨文章聚合主题
│   └── analysis/       #   课题研究工作区
├── CLAUDE.md           # 治理文件（AI 入口）
└── skills/             # CC Skills
```

### 2. 安装 Skills

将 skills 复制到 Claude Code 全局 skills 目录：

```bash
cp -r skills/* ~/.claude/skills/
```

### 3. 配置个人上下文（可选）

在 `context/` 下创建个人资料文件，供 AI 在研究时参考：

```bash
# 示例：投资上下文
cat > context/finance.md << 'EOF'
# 投资上下文

## 风险偏好
...

## 当前持仓
...
EOF
```

### 4. 配置定时复盘（可选）

```bash
crontab -e
# 每周日 9:00 执行复盘
# 0 9 * * 0 cd /path/to/my-wiki && claude -p "执行 /wiki-review" >> /tmp/wiki-review.log 2>&1
```

详见 `config/cron-setup.md`。

## 四大流程

### 一、文章收录（Digest）

```
用户发链接 → inbox/ → AI 生成知识卡片 → wiki/sources/ → 原文归档 raw/
```

每篇文章生成一张知识卡片，包含：一句话摘要、关键要点、标签、关联文章。同类标签积累到 3 篇以上自动生成主题页面。

### 二、课题研究（Research）

```
用户说"研究 XX" → 开题确认 → 搜集资料 → 研究分析 → 输出报告 → 知识入库
```

研究产出分两类：
- **知识型**（方法论、原理）→ 入库到 sources/
- **决策型**（个股分析、个人建议）→ 保留在 analysis/，同时在 decisions/ 创建决策记录

研究过程中会自动读取 `context/` 下的个人上下文，给出针对性建议。

### 三、决策复盘（Review）

```
定时/手动触发 → 扫描到期决策 → 逐条回顾 → 记录教训 → 更新状态
```

每条决策记录包含：决策内容、当时理由、预期结果。复盘时回顾实际结果，提炼教训。

### 四、知识问答

```
用户提问 → 读取 wiki/index.md → 定位相关文件 → 基于知识库回答
```

## Skills 说明

| Skill | 路径 | 触发 |
|-------|------|------|
| `wiki-digest` | `skills/wiki-digest/` | 发链接、说"收录"/"处理" |
| `wiki-research` | `skills/wiki-research/` | 说"研究"/"调研"/"了解" |
| `wiki-review` | `skills/wiki-review/` | 说"复盘"/"回顾"，或 cron 触发 |

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

## 目录职责一览

| 目录 | 维护者 | 内容 | 生命周期 |
|------|--------|------|---------|
| `inbox/` | 用户/AI | 待处理文章 | 临时，处理后清空 |
| `raw/` | AI | 原文归档 | 永久，不可变 |
| `context/` | 用户 | 个人上下文 | 持续更新 |
| `decisions/` | AI | 决策记录 | 永久 |
| `wiki/sources/` | AI | 知识卡片 | 永久 |
| `wiki/topics/` | AI | 聚合主题 | 永久 |
| `wiki/analysis/` | AI | 研究工作区 | 永久（过程记录） |
| `wiki/index.md` | AI | 总索引 | 持续更新 |
| `wiki/log.md` | AI | 处理日志 | 只追加 |

## 设计原则

1. **用户只做输入，AI 负责中间过程**：发链接、提问题、说研究方向，其余交给 AI
2. **知识库和个人数据分离**：wiki/ 是关于世界的知识，context/ 是关于用户自身的信息，decisions/ 是用户的决策
3. **所有内容中文**：生成的文件内容使用中文，文件名可包含英文
4. **标签自动管理**：AI 自动打标签，不预定义，同类文章多了自动聚合成主题
5. **决策可复盘**：每条决策有 review_date，定期回顾实际结果，持续改进判断力

## 依赖

- Claude Code CLI
- webReader MCP（抓取网页内容）
- WebSearch（课题研究时搜索资料）
