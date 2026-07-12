---
name: wiki-push
description: 推送消息到微信。触发：用户说"推送到微信"、"发微信"、"微信通知"
---

# Push — 微信消息推送

> **分层**：⑤ 工具类 · 基础能力（被其他 skill 调用）
> **被调**：用户、wiki-sweep（变更推送）、其他任何需要推送的 skill
> **调用**：无

## 触发条件

- "推送到微信"、"发微信"、"微信通知"、"微信告诉我"
- 其他 skill 需要推送微信时也可调用

## 前置检查

1. 定位配置文件：检查 `$WIKI_ROOT/.cron/config.sh` 是否存在
2. 读取配置：`WECHAT_ID`、`WECHAT_PUSH_KEY`、`WECHAT_PUSH_SERVER`
3. 三个配置项任一缺失则报错："微信推送未配置，请在 `.cron/config.sh` 中设置 WECHAT_ID、WECHAT_PUSH_KEY、WECHAT_PUSH_SERVER"
4. **密钥保护**：`WECHAT_PUSH_KEY` 是敏感凭据。禁止在对话中回显、打印、输出其值；确认消息中只显示"已发送"或错误信息，绝不能包含密钥

## 推送消息

1. **提取推送内容**：
   - 用户指定了内容（如"推送到微信：XXX"）→ 取 `:` 后的内容作为消息
   - 其他 skill 调用 → 取调用方传入的内容
   - 用户未提供内容（如只说"微信通知"）→ **追问**："推送什么内容？"，不要发送空消息
2. **确认推送内容**（检查点）：向用户展示即将发送的消息内容，确认后再发送。以下情况可跳过确认直接发：
   - 用户刚说完的原话，内容 ≤ 50 字且无歧义
   - 其他 skill 传入的内容，调用方已确认
3. **消息长度限制**：单条消息不超过 4096 字符。超出时截断并追加 `\n\n[消息过长已截断]`
4. 用 Bash 执行 curl 发送：

```bash
PAYLOAD=$(jq -n --arg id "$WECHAT_ID" --arg text "$MESSAGE" \
  '{wechat_id: $id, text: $text}')
curl -s --max-time 10 -X POST "${WECHAT_PUSH_SERVER}/api/wechat/push" \
  -H "Authorization: Bearer ${WECHAT_PUSH_KEY}" \
  -H "Content-Type: application/json" \
  -d "$PAYLOAD"
```

- 使用 `jq -n` 构造 JSON，自动处理特殊字符转义（双引号、换行、反斜杠等）
- 发送成功返回 `{"status":"sent"}`
- 发送失败时告知用户，区分错误类型：
  - 超时（curl exit 28）→ "推送超时，网络可能不稳定，稍后重试"
  - 认证失败（HTTP 401/403）→ "推送认证失败，请检查 WECHAT_PUSH_KEY 配置"
  - 其他错误 → 输出 curl 返回的错误信息
- curl 超时设 10 秒：`--max-time 10`
- **重试**：非认证类失败（超时、网络错误）自动重试 1 次，间隔 3 秒，重试仍失败则报错

## 消息格式

- 如果用户指定了内容，按用户说的发
- 如果是其他 skill 触发（如课题更新后推送摘要），按调用方提供的内容发
- 消息较长时适当分段，保持可读性
- **幂等性**：每次调用都是独立发送。若用户重复触发相同内容，正常发送并提示"已重新推送"

## 不要做什么

- 不要在对话中显示 `WECHAT_PUSH_KEY` 的值（包括调试输出、确认消息、错误信息）
- 不要用字符串拼接构造 JSON（`"{\"key\":\"value\"}"`），必须用 `jq`
- 不要在消息内容为空时发送
- 不要把 curl 的完整命令行（含 Authorization header）打印到对话中
- 不要自动重试认证失败（401/403），这类错误需用户手动检查配置
- 不要将推送内容写文件缓存，每次调用都即时发送

## 路径约定

配置文件在 `$WIKI_ROOT/.cron/config.sh`。如果 `WIKI_ROOT` 未设置，报错提示。

## 关联资源

- 配置来源：`$WIKI_ROOT/.cron/config.sh`（由 cron 任务共用）
- 其他 skill 调用：wiki-research、wiki-sweep 等完成后可调用本 skill 推送摘要
- 待办提醒：`todo-remind.sh` 使用相同的微信推送接口
