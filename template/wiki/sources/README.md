# 知识卡片（sources/）

每篇文章提炼为一张独立卡片，是知识库的最小单元。

## 卡片格式

```markdown
---
title: 文章标题
date: YYYY-MM-DD
tags: [标签1, 标签2]
source: article | self | research
origin: 原文URL或 raw/路径
related: [[关联卡片1]], [[关联卡片2]]
---

## 一句话
50字以内的摘要。

## 关键要点
- 要点1
- 要点2
- 要点3

## 原文
→ [[raw/文件名]]
```

## 来源类型

- `article`：外部文章，经 `/wiki-digest` 处理
- `self`：用户自己写的内容，保留原文不重写
- `research`：课题研究的知识型产出，origin 指向 analysis/ 报告
