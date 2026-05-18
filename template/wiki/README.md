# 知识库（wiki/）

AI 维护的核心知识区域。每次操作前先读 `index.md` 了解全貌。

## 目录说明

- **index.md** — 总索引，所有知识卡片的导航入口，按标签聚合
- **log.md** — 处理日志，记录所有文章处理和研究完成的操作历史
- **sources/** — 知识卡片，每篇文章提炼为一张卡片（一句话摘要 + 关键要点 + 原文链接）
- **topics/** — 跨文章聚合主题，当同一标签积累 ≥3 篇文章时自动生成
- **analysis/** — 课题研究工作区，每个课题一个子目录（plan → materials → notes → report → follow-ups）

## 数据流向

```
inbox/文章 → wiki-digest → sources/知识卡片 + raw/原文归档
                                    ↓
                              topics/聚合主题（≥3篇同类）
研究课题 → wiki-research → analysis/{课题}/ + follow-ups/
                                    ↓
                          sources/知识卡片（知识型）或 decisions/（决策型）
```
