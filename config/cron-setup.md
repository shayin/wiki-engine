# 复盘定时任务配置

## 说明

每周日自动触发决策复盘。使用系统 crontab 调用 claude CLI。

## 配置方法

编辑 crontab：

```bash
crontab -e
```

添加以下行（每周日 9:00 执行）：

```
0 9 * * 0 cd /Users/shayin/data1/htdocs/project/mind/ai-wiki && claude -p "执行 /wiki-review" >> /tmp/wiki-review.log 2>&1
```

## 验证

```bash
crontab -l
```

## 注意事项

- 需要 claude CLI 已安装且认证有效
- 工作目录必须是 ai-wiki/（包含 decisions/ 和 wiki/）
- 日志输出到 /tmp/wiki-review.log
