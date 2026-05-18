# 收件箱（inbox/）

待处理文章的入口。文章从这里进入知识库。

## 放入方式

- 微信转发链接
- 浏览器插件保存
- 手动放入文件

## 处理

AI 执行 `/wiki-digest` 时读取此目录，处理后：
- 生成知识卡片到 `wiki/sources/`
- 原文移到 `raw/YYYY-MM/`
- 更新 `wiki/index.md` 索引

处理完成后 inbox 清空。
