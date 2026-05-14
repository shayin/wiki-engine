---
name: digest
description: 处理收件箱中的新文章，抓取全文、生成知识卡片、归档原文、更新索引
trigger: 用户发送文章链接，或用户说"处理文章"、"digest"、"收录"
---

# Digest — 文章处理流程

## 触发条件

- 用户发送了一个 URL（http/https 开头的链接）
- 用户说"处理"、"收录"、"digest"等相关指令
- 用户发送一段长文本（非 URL，视为自己写的文章）

## 执行步骤

### 1. 获取内容

**如果是 URL：**
- 使用 web fetch 工具抓取文章全文
- 提取标题、正文、来源
- 如果抓取失败，告知用户并建议手动粘贴正文

**如果是长文本：**
- 视为用户自己写的文章（`source: self`）
- 直接使用用户提供的文本

### 2. 存入 inbox/

将原始内容保存到 inbox/，文件名格式：`YYYY-MM-DD-简短标识.md`

```markdown
---
url: 原始链接（如有）
fetched: YYYY-MM-DD HH:mm
status: pending
---

原始文章标题

原始正文内容...
```

### 3. 生成知识卡片

读取 `wiki/index.md` 了解现有知识库结构，然后生成知识卡片。

**对于 source: article（外部文章）：**

```markdown
---
title: 文章标题
date: YYYY-MM-DD
tags: [标签1, 标签2, 标签3]
source: article
origin: 原文URL
related: [[已有相关页面1]], [[已有相关页面2]]
---

## 一句话
50字以内的精炼摘要。

## 关键要点
- 要点1
- 要点2
- 要点3

## 原文
→ [[raw/YYYY-MM/YYYY-MM-DD-简短标识]]
```

**对于 source: self（用户自己写的）：**

```markdown
---
title: 文章标题
date: YYYY-MM-DD
tags: [标签1, 标签2]
source: self
related: [[已有相关页面1]]
---

（保留用户原文，不重写）

## 原文
→ [[raw/YYYY-MM/YYYY-MM-DD-简短标识]]
```

标签规则：
- 自动打标签，基于文章内容判断
- 标签分两层：领域（技术/股票/生活/工作/读书...）+ 具体主题
- 查看已有 wiki/sources/ 下的文章标签，尽量复用已有标签
- related 通过搜索 wiki/ 下已有内容，找出相关页面

### 4. 写入 wiki/sources/

将知识卡片保存到 `wiki/sources/YYYY-MM-DD-简短标识.md`

### 5. 归档原文

创建 `raw/YYYY-MM/` 目录（如不存在），将 inbox/ 中的原文移过去。

### 6. 更新 wiki/index.md

在 index.md 的「最近更新」部分追加一条记录：
```
- YYYY-MM-DD: [文章标题](sources/YYYY-MM-DD-简短标识.md) #标签1 #标签2
```

更新统计数字。

### 7. 检查是否需要更新 topic

检查本次文章的标签，如果 wiki/topics/ 下已有对应主题页面，追加到该主题的关联文章列表中。如果某个标签下的文章达到 3 篇且还没有 topic 页面，自动创建一个。

### 8. 更新 wiki/log.md

追加处理日志：
```
- YYYY-MM-DD HH:mm: 处理了「文章标题」→ sources/YYYY-MM-DD-简短标识.md
```

### 9. 回复用户

简洁回复，格式：
```
已收录「文章标题」

一句话摘要。

#标签1 #标签2
```

## 错误处理

- 抓取失败：告知用户，建议手动粘贴正文
- 内容为空或太短（<100字）：告知用户，确认是否仍要收录
- 文件名冲突：在文件名后追加序号

## 路径约定

知识库根目录通过 CLAUDE.md 的位置来确定。如果当前工作目录下有 `wiki/` 目录，则以此为根。否则向上查找包含 `wiki/` 的父目录。
