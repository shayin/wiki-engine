---
name: wiki-push
description: 推送消息到微信。触发：用户说"推送到微信"、"发微信"、"微信通知"
---

# Push — 微信消息推送

## 触发条件

- "推送到微信"、"发微信"、"微信通知"、"微信告诉我"
- 其他 skill 需要推送微信时也可调用

## 前置检查

1. 定位配置文件：检查 `$WIKI_ROOT/.cron/config.sh` 是否存在
2. 读取配置：`WECHAT_ID`、`WECHAT_PUSH_KEY`、`WECHAT_PUSH_SERVER`
3. 三个配置项任一缺失则报错："微信推送未配置，请在 `.cron/config.sh` 中设置 WECHAT_ID、WECHAT_PUSH_KEY、WECHAT_PUSH_SERVER"

## 推送消息

用 Bash 执行 curl 发送：

```bash
curl -s -X POST "${WECHAT_PUSH_SERVER}/api/wechat/push" \
  -H "Authorization: Bearer ${WECHAT_PUSH_KEY}" \
  -H "Content-Type: application/json" \
  -d "{\"wechat_id\":\"${WECHAT_ID}\",\"text\":\"消息内容\"}"
```

- `text` 字段支持 `\n` 换行
- 发送成功返回 `{"status":"sent"}`
- 发送失败时告知用户

## 消息格式

- 如果用户指定了内容，按用户说的发
- 如果是其他 skill 触发（如课题更新后推送摘要），按调用方提供的内容发
- 消息较长时适当分段，保持可读性

## 路径约定

配置文件在 `$WIKI_ROOT/.cron/config.sh`。如果 `WIKI_ROOT` 未设置，报错提示。
