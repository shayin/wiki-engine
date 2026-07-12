# Wiki 系统使用指南

> 核心是**变量资产 + 标的跟踪**。其他都是围绕这个核心的工具。

## 分层地图

```
                ┌─────────────────────────────────┐
                │   registry.md (74 变量)          │
                │   各标的的 macro-tracker         │  ← 核心
                │   edges.md (传导边)              │
                └─────────────────────────────────┘
                          ↑ ↓ 喂养 ↑ ↓
        ┌─────────────────┬──────────────────┬─────────────────┐
   ① 输入            ② 维护              ③ 挖掘            ④ 输出
   (新信息进来)      (保持新鲜)          (主动找盲区)      (产出判断)
```

## 技能调用关系图

```
① 输入类（内容进知识库）
   wiki-digest ──步骤 10.5──→ wiki-lens
   wiki-research ──步骤 3 / R3.1──→ wiki-mine（内部）
                 ──收尾──→ wiki-lens
                 （支持新课题 + 已有课题刷新两种模式）

② 维护类（知识库自检与更新）
   wiki-sweep ──维度 M──→ wiki-mine（内部）
              ──维度 L──→ wiki-lens
              ──推送──→ wiki-push
   wiki-review（独立作业）

③ 挖掘类
   wiki-lens（用户可直接调，也被 ① ② 集成）
   wiki-mine（🔒 内部 skill，只被 sweep/research 调用，用户不直接调）

④ 输出类（从知识库出东西）
   wiki-ask / wiki-write / wiki-learn（只读知识库）

⑤ 工具类（基础能力，被其他 skill 调用）
   wiki-push / wiki-todo
```

**核心协作链**：
- 研究链：`research → mine（挖盲区）→ lens（透镜碰撞）→ 入库`（新课题）
- **刷新链**：`/wiki-research {已有课题} → 读基线 → mine（再挖一轮）→ 反面论据 + 透镜 + 二级效应 → delta report + tracker 更新` ← 用户日常高频
- 维护链：`sweep → mine（盲区体检）→ lens（透镜回顾）→ push（推送变更）`
- 体检链：`/wiki-sweep {标的} → mine（挖盲区）+ C 档（查证）+ push（推送）` ← 一站式
- 文章链：`digest → lens（透镜扫描）→ 入库`

## 一、自动跑（你不用管）

这些技能由 cron 在后台定时跑，推送结果到微信：

| 技能 | 频率 | 做什么 |
|------|------|--------|
| **wiki-sweep V2** | 每天 23:15 | 全自动维护：A 机械修复 / C 变量状态查证。两档全自动 + 关键事实翻转实时推 + 日报告 |
| **wiki-digest** | 每天 23:00 | 自动处理 inbox 文章：抓全文、生成知识卡片、归档原文 |
| **wiki-review** | 每天 23:30 | 自动复盘到期决策：扫 decisions/，回顾实际结果，记录教训 |
| **wiki-todo** | 11:00 / 22:55 | 待办提醒：统计 work/personal/tracking 数量推微信 |
| **snapshot-backup** | 每天 sweep 前 | rsync 快照备份（日保 14 / 周保 8 / 月保 6） |
| **research-trigger** | 周一/月初 | 触发定期重研究（holding 每周轮一个 / watching 季度 / sector 半年） |

**你的日常 = 看微信报告就够了**

## 二、你主动调（按需）

### 🔵 高频（每周可能用）

| 场景 | 技能 | 例子 |
|------|------|------|
| **深度刷新已有课题** ⭐ | `/wiki-research {已有课题}` | "/wiki-research ai-stock-picking"（自动识别已有 → 刷新模式：挖盲区 + 反面论据 + 透镜 + 二级效应 + delta report + tracker 更新，深度等同新课题） |
| **基于知识库问答** | `/wiki-ask` | "/wiki-ask PDD 当前关税敞口" |
| **看到大事件想深挖** | `/wiki-lens` | "/wiki-lens 联储降息 50bp" |

### 🟡 中频（每月 1-2 次）

| 场景 | 技能 | 例子 |
|------|------|------|
| **研究新标的** | `/wiki-research {新课题}` | "/wiki-research NVDA"（自动识别为新课题 → 完整研究流程） |
| **轻量体检单标的** | `/wiki-sweep {标的}` | "/wiki-sweep 拼多多"（3-5 分钟：盲区挖掘 + 变量查证 + 微信推送） |
| **写短文输出** | `/wiki-write` | "/wiki-write PDD 深度" |

### 🟢 低频（很少调）

| 场景 | 技能 | 说明 |
|------|------|------|
| 手动推微信 | `/wiki-push` | sweep V2 自动用了，特殊场景才手动 |
| 学习路径 | `/wiki-learn` | 把知识库内容组织成有序阅读路径 |

## 三、典型一周工作流

```
周一
 上午: 看微信 sweep 日报告 → 知道周末发生了什么
 下午: "高通仓位对吗？" → /wiki-ask 高通当前主要风险

周二
 sweep 23:15 自动跑 → 微信推"今日变更 N 条"

周三
 sweep 自动重研究某个 holding 标的（research-trigger 触发）

周四
 看到一篇 PDD 深度文章 → 扔 inbox
   → 23:00 digest 自动处理
   → 23:15 sweep 发现 PDD tracker 数据过时 → C 档自动刷新

周五
 周末看 issues.md → 有 2 个待决策项
   → /wiki-mine PDD 挖盲区 → 找到 registry 没有的新变量

周六
 觉得 NVDA 有机会 → /wiki-research NVDA → 沉淀新变量
```

## 四、变量资产的扩充路径

registry.md（核心变量库）的变量从哪里来：

| 来源 | 机制 | 当前贡献 |
|------|------|---------|
| 研究 → 沉淀 | `/wiki-research` 步骤 7c | ~50 个（主路径） |
| sweep A5 自动同步 | wiki-sweep V2 阶段 2 | ~10 个 |
| wiki-mine 盲区挖掘 | `/wiki-mine` 6 种机制 | ~5 个 |
| wiki-lens 透镜反推 | `/wiki-lens` 方法论审视 | ~5 个 |
| C 档聚焦研究产出 | wiki-sweep V2 阶段 4b | ~4 个 |

## 五、核心原则

**少调，多看**：
- **80% 时间**：看 sweep 微信日报告 + changelog
- **15% 时间**：用 `/wiki-research {已有课题}` 深度刷新，或 `/wiki-ask` 基于知识库深度问答
- **5% 时间**：触发 `/wiki-research {新课题}` 研究新标的，或 `/wiki-sweep {标的}` 轻量体检

**让 wiki-sweep V2 当你的"知识库管家"**，每天自动维护+推送。你只在需要决策或挖掘时主动调技能。

## 六、关键文件位置

```
ai-wiki/
├── wiki/
│   ├── index.md              # 知识库总索引
│   ├── changelog.md          # 变更日志（sweep V2 自动维护）
│   ├── variables/
│   │   ├── registry.md       # ★ 核心变量库
│   │   ├── edges.md          # 传导边
│   │   ├── variables/        # 关键变量深度档案
│   │   └── sectors/          # 板块模板
│   ├── analysis/{标的}/
│   │   ├── report.md         # 研究报告
│   │   ├── follow-ups/
│   │   │   └── macro-tracker.md  # ★ 标的变量跟踪
│   │   └── research-log/     # 定期重研究报告（按月）
│   └── connections/          # 跨课题关联
├── decisions/                # 决策记录
├── todos/active.md           # 当前待办
├── context/finance.md        # 持仓信息（用户维护）
└── .cron/
    ├── snapshots/            # 每日快照 + staging.json
    ├── sweep-issues.md       # sweep 待决策项
    └── scripts/              # 自动化脚本
```
