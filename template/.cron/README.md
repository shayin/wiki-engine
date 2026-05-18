# 定时任务（.cron/）

无人值守时自动运行的任务，用于定期维护知识库和发送提醒。

## 目录结构

- **config.sh** — 用户配置（Bark key、调度时间等）
- **scripts/** — 定时任务脚本
- **logs/** — 执行日志
- **pending.md** — 通知队列（定时任务发现问题写入，AI 对话时检查并提醒用户）
- **sweep-issues.md** — 知识库扫描发现的问题清单

## 任务列表

| 任务 | 说明 |
|------|------|
| wiki-digest | 处理 inbox 中的文章 |
| wiki-sweep | 扫描知识库健康度 |
| wiki-review | 决策复盘提醒 |
| todo-remind | 待办提醒（早晚汇总 + 定时提醒） |
