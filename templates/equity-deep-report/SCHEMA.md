# equity-deep-report 模版契约（SCHEMA）

> 个股深度研究报告模版。版本：template_version 1.0 / schema_version 1.0
> 来源：cross-ai-debate 共识（Claude×codex，2026-07-19）。详见 `tmp/debate-report-template.md`。

## 14 节结构（必填/选填）

| # | 节 | 必填 | 来源/说明 |
|---|---|------|----------|
| 1 | 投资论点（3可检验假设+variant view+证伪条件） | ✅ | 论点必须含KPI+验证期+"什么事实推翻我" |
| 2 | 经营预测+估值桥（分业务模型+SOTP+可比+"为何非同业"+敏感性） | ✅ | 不可只罗列区间，必须可审计 |
| 3 | 产业链/竞争/结构性风险地图 | ✅ | 区分"周期低估"vs"盈利能力永久受损" |
| 4 | 基本面：实际 vs 预期差 | ✅ | 找 beat/miss 机会 |
| 5 | 情绪面 | ✅ | 共识/评级/内部人/回购/Reddit |
| 6 | 技术面（wiki-quant 16信号详解） | ✅ | wiki-quant skill 模式1 |
| 7 | 历史类比胜率 | 条件 | 有数据时；含归因+regime+样本数+置信；<20样本标"仅供参考" |
| 8 | 透镜碰撞 | 增强 | 4-5透镜含≥1跨界；wiki-lens skill |
| 9 | MPV 定价验证 | 增强 | 采用方法论时必填；核心骨架不依赖此专有框架 |
| 10 | 持仓者偏差自检 | ✅ | 最强空头论点+空仓测试+减仓触发+财报反方回测 |
| 11 | 催化剂冻结快照 | ✅ | 事前下注（日期/市场隐含/自己预期/KPI/动作）+ macro-tracker 链接 |
| 12 | 持仓建议（仓位框架） | ✅* | 有持仓时必填；仓位上限逻辑（期限/最大亏损/相关性/流动性） |
| 13 | 证据账本（可折叠） | ✅ | 每结论配来源/日期/口径/计算式，第三方可复算 |
| 14 | 数据时效 + template_version | ✅ | 每数据标截至日期 |

## 脱敏规则（强制）

引擎模版**不含**任何：
- 个人持仓（股数/成本/仓位）
- 个股具体数值（现价/EPS/目标价）
- 数据源凭据（token/API key）
- 用户个人信息

模版用占位符（`{{ticker}}`/`{{current_price}}`/`{{position}}`/`{{date}}`），实例填充。

## 放置与分发

- **引擎唯一规范源**：`wiki-engine/templates/equity-deep-report/`（本目录）
  - `report-template.html`：脱敏 HTML/CSS 壳（夜间模式 + 可折叠证据组件）
  - `SCHEMA.md`：本契约
- **wiki-research skill**：步骤4输出报告时引用本模版，强制必填字段
- **wiki 实例产物**：`ai-wiki/wiki/analysis/{课题}/`（report.md/delta + materials + follow-ups）
- **HTML 分享件**：`data/claude-html-share/YYYY-MM-DD/`（自包含，CSS/JS 内联），report.md 链接分享件路径

## 升级规则（防版本漂移）

- 引擎模版是唯一事实源。实例报告 frontmatter 写 `template_version` + `schema_version`
- install.sh 分发：复制 `templates/` 到新实例
- 模版升级（schema 变更）→ bump schema_version；旧报告标注"基于 vX，建议刷新"
- skill 用稳定引擎路径解析模版（不硬编码实例路径）

## 检查清单（报告生成前自检）

- [ ] 节1有3条可检验假设（含证伪条件）
- [ ] 节2估值可审计（分业务+可比+敏感性，非区间罗列）
- [ ] 节3区分周期性 vs 结构性风险
- [ ] 节10有最强空头论点（不只列亏钱理由）
- [ ] 节11催化剂是"事前快照"（非事后总结）
- [ ] 节13每个核心结论有可折叠证据
- [ ] 节14所有数据标截至日期 + template_version
- [ ] 脱敏（如作为模版分发）：无个人/个股具体数据
