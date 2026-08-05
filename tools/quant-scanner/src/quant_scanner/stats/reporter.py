"""胜率统计 HTML 报告 + CLI 子命令

辩论共识第 11 条报告披露要求：
- 标注 stats 时点（t+1 open）与 engine t close 闭环分开披露
- 标注 long-only
- 标注 holdout 状态
- 展示 Wilson 区间
- 低置信度行灰显

可读性增强（用户反馈 2026-07-18）：
- 表头/术语中文解释卡片
- 自动结论生成器（数据 → 3-5 条中文洞察）
- signal_name / event_type / tier / direction 全中文化
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

import pandas as pd
from jinja2 import Environment

from quant_scanner.stats.analyzer import SliceStats, HoldoutSplit
from quant_scanner.utils.config import get_reports_dir

log = logging.getLogger(__name__)


# =====================================================================
# 中文化映射表
# =====================================================================

SIGNAL_NAME_ZH: dict[str, str] = {
    "trend_template": "趋势模板（Minervini 8 条）",
    "vcp": "VCP 波动率收缩",
    "pivot_point": "中枢点",
    "base_counting": "基底计数（Minervini 卖出信号）",
    "sell_signals": "卖出信号（基底计数）",
    "cup_handle": "杯柄形态",
    "market_direction": "市场方向（O'Neil FTD）",
    "trend_regime": "趋势制度",
    "major_reversal": "重大反转形态",
    "continuation": "持续形态",
    "oscillator_timing": "振荡器择时（RSI/MACD）",
    "rs_rating": "相对强度评级",
    "can_slim": "CAN SLIM 七字母",
    "new_high_supply": "新高 + 供需",
    "dow_phases": "道氏三阶段",
    "support_resistance": "支撑/阻力角色互换",
}

EVENT_TYPE_ZH: dict[str, str] = {
    # major_reversal
    "DOUBLE_TOP": "双顶",
    "DOUBLE_BOTTOM": "双底",
    "HEAD_SHOULDERS_TOP": "头肩顶",
    "HEAD_SHOULDERS_BOTTOM": "头肩底",
    "TRIPLE_TOP": "三重顶",
    "TRIPLE_BOTTOM": "三重底",
    "ROUNDING_TOP": "圆弧顶",
    "ROUNDING_BOTTOM": "圆弧底",
    "V_TOP": "V 型顶",
    "V_BOTTOM": "V 型底",
    "ISLAND_TOP": "岛形顶",
    "ISLAND_BOTTOM": "岛形底",
    # continuation
    "BULL_FLAG": "牛市旗形",
    "BEAR_FLAG": "熊市旗形",
    "RECTANGLE": "矩形",
    "ASCENDING_TRIANGLE": "上升三角",
    "DESCENDING_TRIANGLE": "下降三角",
    "SYMMETRIC_TRIANGLE": "对称三角",
    "RISING_WEDGE": "上升楔形",
    "FALLING_WEDGE": "下降楔形",
    # oscillator_timing
    "RSI_CLASSIC_DIVERGENCE": "RSI 经典背离",
    "MACD_CLASSIC_DIVERGENCE": "MACD 经典背离",
    "HIDDEN_DIVERGENCE": "隐藏背离",
    "FAILURE_SWING": "失败摆动",
    "EXTREME": "超买/超卖极值",
    # default extractor（signal_name.upper()）
    "TREND_TEMPLATE": "趋势模板触发",
    "VCP": "VCP 触发",
    "PIVOT_POINT": "中枢点触发",
    "BASE_COUNTING": "基底计数触发",
    "CUP_HANDLE": "杯柄触发",
    "MARKET_DIRECTION": "市场方向触发",
    "TREND_REGIME": "趋势制度切换",
    "RS_RATING": "RS 评级触发",
    "CAN_SLIM": "CAN SLIM 触发",
    "NEW_HIGH_SUPPLY": "新高供需触发",
    "DOW_PHASES": "道氏阶段切换",
    "SUPPORT_RESISTANCE": "支撑/阻力触发",
    # 通用兜底
    "TRANSITION": "状态切换",
    "GENERIC": "通用事件",
}

DIRECTION_ZH: dict[str, str] = {
    "long": "做多",
    "short": "做空",
    "unknown": "方向不明",
}

TIER_ZH: dict[str, str] = {
    "grey": "灰色（样本 < 20，仅供探索）",
    "exploratory": "探索级（20-50）",
    "low": "低置信（50-200）",
    "default": "默认（≥ 200，可信赖）",
    "n/a": "未分层",
}


def _signal_zh(name: str) -> str:
    """signal_name → 中文，未知则原样返回。"""
    if not name:
        return name
    return SIGNAL_NAME_ZH.get(name, name)


def _event_type_zh(et: str) -> str:
    """event_type → 中文，未知则原样返回。"""
    if not et:
        return et
    return EVENT_TYPE_ZH.get(et, et)


def _direction_zh(d: str) -> str:
    return DIRECTION_ZH.get(d, d)


def _tier_zh(t: str) -> str:
    return TIER_ZH.get(t, t)


# =====================================================================
# 自动结论生成器
# =====================================================================


@dataclass
class Conclusion:
    """单条自动结论。"""

    severity: str  # positive / warning / negative / neutral
    title: str
    body: str


def generate_conclusions(
    top_level: list[SliceStats],
    second_level: list[SliceStats],
    holdout_table: "HoldoutTable | None",
    total_events: int,
    holdout_status: str,
) -> list[Conclusion]:
    """基于数据自动生成 3-5 条中文结论。

    生成逻辑：
    - 最强形态（EV 最高且 tier=default/low）
    - 最弱形态（EV 最低或为负）
    - 过拟合警示（训练 vs holdout 背离）
    - 样本量警告（grey/exploratory 占比过高）
    - 总体把握（事件数、置信度分布）
    """
    out: list[Conclusion] = []

    # 1. 最强 / 最弱形态
    trustworthy = [s for s in top_level if s.confidence_tier in ("default", "low") and s.n >= 30]
    if trustworthy:
        best = max(trustworthy, key=lambda s: s.ev)
        worst = min(trustworthy, key=lambda s: s.ev)
        if best.ev > 0:
            pf_text = "∞" if best.profit_factor == float("inf") or (
                isinstance(best.profit_factor, float) and math.isinf(best.profit_factor)
            ) else f"{best.profit_factor:.2f}"
            out.append(Conclusion(
                severity="positive",
                title="最强形态",
                body=(
                    f"<strong>{_signal_zh(best.signal_name)}</strong>（{_direction_zh(best.direction)}）"
                    f"平均每笔净收益 <strong>+{best.ev*100:.2f}%</strong>，"
                    f"胜率 {best.win_rate*100:.1f}%（Wilson {best.win_rate_low*100:.0f}%-{best.win_rate_high*100:.0f}%），"
                    f"盈亏比 {pf_text}，样本 n={best.n}（{_tier_zh(best.confidence_tier)}）。"
                    f"可重点纳入实盘观察池。"
                ),
            ))
        if worst.ev < 0:
            out.append(Conclusion(
                severity="negative",
                title="最弱形态（警示）",
                body=(
                    f"<strong>{_signal_zh(worst.signal_name)}</strong>（{_direction_zh(worst.direction)}）"
                    f"平均每笔净收益 <strong>{worst.ev*100:+.2f}%</strong>，"
                    f"胜率仅 {worst.win_rate*100:.1f}%。建议暂停实盘触发，回查算法参数。"
                ),
            ))

    # 2. 二级表中最优切片
    if second_level:
        trustworthy_2nd = [s for s in second_level if s.confidence_tier in ("default", "low") and s.n >= 30]
        if trustworthy_2nd:
            best2 = max(trustworthy_2nd, key=lambda s: s.ev)
            # 仅当与顶层最强形态不同时才单独列出，避免重复
            top_best_names_zh = {
                c.body.split("</strong>")[0].split("<strong>")[-1]
                for c in out
                if "<strong>" in c.body and "</strong>" in c.body
            }
            if best2.ev > 0 and _signal_zh(best2.signal_name) not in top_best_names_zh:
                out.append(Conclusion(
                    severity="positive",
                    title="细分最优切片",
                    body=(
                        f"<strong>{_signal_zh(best2.signal_name)} / {_event_type_zh(best2.event_type)}</strong> "
                        f"是二级表中 EV 最高的组合，+{best2.ev*100:.2f}%，"
                        f"胜率 {best2.win_rate*100:.1f}%，n={best2.n}。"
                        f"建议在扫描器中为该组合单独标注优先级。"
                    ),
                ))

    # 3. 过拟合警示（训练 vs holdout）
    if holdout_table and holdout_table.is_validated and top_level and holdout_table.top_level:
        train_map = {s.signal_name: s.ev for s in top_level}
        overfit_flags: list[str] = []
        for h in holdout_table.top_level:
            t_ev = train_map.get(h.signal_name)
            if t_ev is None:
                continue
            # 训练正、holdout 负，或训练大幅优于 holdout（差 > 3pp）
            if t_ev > 0 and h.ev < 0:
                overfit_flags.append(
                    f"<strong>{_signal_zh(h.signal_name)}</strong>"
                    f"训练 {t_ev*100:+.2f}% → holdout {h.ev*100:+.2f}%（方向反转）"
                )
            elif t_ev > 0 and (t_ev - h.ev) > 0.03:
                overfit_flags.append(
                    f"<strong>{_signal_zh(h.signal_name)}</strong>"
                    f"训练 {t_ev*100:+.2f}% → holdout {h.ev*100:+.2f}%（衰减 {(t_ev-h.ev)*100:.1f}pp）"
                )
        if overfit_flags:
            out.append(Conclusion(
                severity="warning",
                title="过拟合警示",
                body=(
                    "以下形态在 holdout 段表现显著低于训练段，存在过拟合风险：<br>"
                    + "<br>".join(f"• {x}" for x in overfit_flags[:3])
                    + ("<br>• ……" if len(overfit_flags) > 3 else "")
                ),
            ))
        else:
            out.append(Conclusion(
                severity="positive",
                title="holdout 段一致性",
                body="主要形态在 holdout 段表现与训练段一致，过拟合风险较低。",
            ))

    # 4. 样本量警告
    if top_level:
        grey_count = sum(1 for s in top_level if s.confidence_tier == "grey")
        expl_count = sum(1 for s in top_level if s.confidence_tier == "exploratory")
        low_count = sum(1 for s in top_level if s.confidence_tier == "low")
        default_count = sum(1 for s in top_level if s.confidence_tier == "default")
        if grey_count + expl_count > 0:
            out.append(Conclusion(
                severity="warning",
                title="样本量警告",
                body=(
                    f"在 {len(top_level)} 个顶层 signal 中："
                    f"默认级（≥200）{default_count} 个，低置信（50-200）{low_count} 个，"
                    f"探索级（20-50）{expl_count} 个，灰色（<20）{grey_count} 个。"
                    f"灰显行结论仅供参考，不建议直接用于实盘决策。"
                ),
            ))

    # 5. 总体把握
    if not out:
        out.append(Conclusion(
            severity="neutral",
            title="数据不足",
            body=f"共采集 {total_events} 个事件，但可信样本不足，无法生成结论。建议扩大 universe 或回溯周期。",
        ))

    return out


# =====================================================================
# HTML 模板
# =====================================================================

TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>形态胜率统计 {{ stamp }}</title>
<style>
* { box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Helvetica Neue", sans-serif;
       background: #f5f5f7; color: #1d1d1f; margin: 0; padding: 20px; }
.container { max-width: 1400px; margin: 0 auto; background: white; border-radius: 12px;
             padding: 32px; box-shadow: 0 2px 12px rgba(0,0,0,0.06); }
h1 { margin: 0 0 8px; font-size: 28px; }
h2 { margin: 28px 0 10px; font-size: 18px; color: #1d1d1f;
     border-left: 4px solid #007aff; padding-left: 10px; }
h3 { margin: 18px 0 6px; font-size: 15px; color: #1d1d1f; }
.subtitle { color: #6e6e73; margin-bottom: 12px; font-size: 14px; }
.disclosure { background: #fff8e1; border-left: 3px solid #ff9800;
              padding: 12px 16px; margin: 12px 0; font-size: 13px;
              border-radius: 3px; color: #555; }
.disclosure ul { margin: 6px 0 0 16px; padding: 0; }
.conclusions { margin: 16px 0; }
.conclusion { padding: 12px 14px; margin: 8px 0; border-radius: 6px; font-size: 14px; line-height: 1.6; }
.conclusion-positive { background: #e8f7ed; border-left: 3px solid #0e6e2e; }
.conclusion-negative { background: #fdeaea; border-left: 3px solid #9b1818; }
.conclusion-warning { background: #fff4e0; border-left: 3px solid #cc7700; }
.conclusion-neutral { background: #f0f0f3; border-left: 3px solid #6e6e73; }
.conclusion-title { font-weight: 600; margin-bottom: 4px; color: #1d1d1f; }
.glossary { background: #f5f5f7; padding: 16px; border-radius: 8px; margin: 12px 0; font-size: 13px; }
.glossary-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 8px 24px; margin-top: 8px; }
.glossary-item { line-height: 1.6; }
.glossary-term { font-weight: 600; color: #1d1d1f; }
.glossary-def { color: #555; }
table { width: 100%; border-collapse: collapse; margin-top: 12px; }
th { background: #f5f5f7; text-align: right; padding: 10px 8px; font-size: 12px;
     text-transform: uppercase; letter-spacing: 0.4px; color: #6e6e73; }
th.tl { text-align: left; }
td { padding: 10px 8px; border-bottom: 1px solid #e5e5e7; font-size: 13px; text-align: right; }
td.tl { text-align: left; }
tr:hover { background: #fafafa; }
.pos { color: #0e6e2e; }
.neg { color: #9b1818; }
.muted { color: #999; font-size: 11px; }

.tier-grey { opacity: 0.4; }
.tier-exploratory { opacity: 0.65; }
.tier-low { opacity: 0.85; }
.tier-default { opacity: 1.0; }

.tag { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 11px;
       font-weight: 500; white-space: nowrap; }
.tag-grey { background: #e0e0e0; color: #555; }
.tag-exploratory { background: #ffe1e1; color: #9b1818; }
.tag-low { background: #fff3cd; color: #8a6d3b; }
.tag-default { background: #d1f4d8; color: #0e6e2e; }

.stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin-bottom: 16px; }
.stat { background: #f5f5f7; padding: 14px; border-radius: 8px; }
.stat-label { font-size: 11px; color: #6e6e73; text-transform: uppercase; letter-spacing: 0.4px; }
.stat-value { font-size: 20px; font-weight: 600; margin-top: 4px; }
</style>
</head>
<body>
<div class="container">
  <h1>形态胜率统计</h1>
  <div class="subtitle">生成时间 {{ generated_at }} · 标的池 {{ universe_size }} · 前瞻窗口 {{ horizon }} 个交易日 · 共采集 {{ total_events }} 个事件</div>

  <div class="disclosure">
    <strong>⚠ 报告披露（防误读）：</strong>
    <ul>
      <li>本报告按 <strong>独立日频</strong>采集事件，与回测引擎的周频调仓策略是两套独立结果，<strong>不可直接对比</strong></li>
      <li>收益计算时点为 <strong>T+1 开盘价</strong>（保守，比回测引擎的 T 收盘价晚一天起算）</li>
      <li>第一期 <strong>仅统计做多（long）方向</strong>；做空/方向不明的事件进入"诊断桶"仅展示样本量</li>
      <li>holdout 状态：<strong>{{ holdout_status }}</strong>（{{ holdout_detail }}）</li>
      <li>默认排序键为 <strong>EV（平均净收益）</strong>降序，胜率仅作展示</li>
      <li>胜率列括号内为 <strong>Wilson 95% 置信区间</strong>，样本越小区间越宽</li>
      <li>低置信度行<strong>灰显</strong>（样本量分层见下方术语表）</li>
    </ul>
  </div>

  <div class="stats">
    <div class="stat"><div class="stat-label">事件总数</div><div class="stat-value">{{ total_events }}</div></div>
    <div class="stat"><div class="stat-label">训练段事件</div><div class="stat-value">{{ train_events }}</div></div>
    <div class="stat"><div class="stat-label">holdout 事件</div><div class="stat-value">{{ holdout_events }}</div></div>
    <div class="stat"><div class="stat-label">censored 事件</div><div class="stat-value">{{ total_censored }}</div></div>
  </div>

  {% if conclusions %}
  <h2>🎯 核心结论（自动生成）</h2>
  <div class="conclusions">
    {% for c in conclusions %}
    <div class="conclusion conclusion-{{ c.severity }}">
      <div class="conclusion-title">{{ c.title }}</div>
      <div>{{ c.body | safe }}</div>
    </div>
    {% endfor %}
  </div>
  {% endif %}

  <h2>📖 术语表（看不懂的请先看这里）</h2>
  <div class="glossary">
    <div class="glossary-grid">
      <div class="glossary-item"><span class="glossary-term">n</span> <span class="glossary-def">= 该形态触发的样本事件数（去掉冷却期重复后）。n 越大统计越可信。</span></div>
      <div class="glossary-item"><span class="glossary-term">胜率</span> <span class="glossary-def">= T+1 开盘买入持有 N 日后，收益 &gt; 0 的比例。括号内是 Wilson 95% 置信区间。</span></div>
      <div class="glossary-item"><span class="glossary-term">EV（平均净收益）</span> <span class="glossary-def">= 每笔事件 T+N 收盘卖出的平均净收益率（已扣滑点佣金）。<strong>默认排序键</strong>，正值代表期望赚钱。</span></div>
      <div class="glossary-item"><span class="glossary-term">中位收益</span> <span class="glossary-def">= 所有样本收益的中位数。与 EV 背离时说明存在极端值。</span></div>
      <div class="glossary-item"><span class="glossary-term">平均盈 / 平均亏</span> <span class="glossary-def">= 盈利样本的平均涨幅 / 亏损样本的平均跌幅。</span></div>
      <div class="glossary-item"><span class="glossary-term">PF（盈亏比）</span> <span class="glossary-def">= 总盈利 / 总亏损绝对值。&gt;1 为正期望，&gt;2 为优秀，∞ 表示无亏损样本。</span></div>
      <div class="glossary-item"><span class="glossary-term">MAE</span> <span class="glossary-def">= Maximum Adverse Excursion，持仓期内最大浮亏（用于设止损）。</span></div>
      <div class="glossary-item"><span class="glossary-term">MFE</span> <span class="glossary-def">= Maximum Favorable Excursion，持仓期内最大浮盈（用于设止盈）。</span></div>
      <div class="glossary-item"><span class="glossary-term">censored</span> <span class="glossary-def">= 因前瞻窗口不足或跨 holdout 边界而无法计算收益的事件。不计入分母。</span></div>
      <div class="glossary-item"><span class="glossary-term">Wilson 区间</span> <span class="glossary-def">= 胜率的 95% 置信区间。区间越窄样本越可信。</span></div>
      <div class="glossary-item"><span class="glossary-term">置信度分层</span> <span class="glossary-def">= grey &lt;20 / exploratory 20-50 / low 50-200 / default ≥200。</span></div>
      <div class="glossary-item"><span class="glossary-term">holdout</span> <span class="glossary-def">= 独立验证段，不参与训练阈值。训练 vs holdout 一致 → 可信；背离 → 过拟合。</span></div>
    </div>
  </div>

  {% if top_level %}
  <h2>📊 顶层信号总表（按 EV 降序）</h2>
  <table>
    <thead><tr>
      <th class="tl">信号（中文）</th><th class="tl">英文原名</th><th class="tl">方向</th>
      <th>n</th><th>胜率 (Wilson 95%)</th><th>EV ↓</th><th>中位</th>
      <th>平均盈</th><th>平均亏</th><th>PF</th>
      <th>MAE</th><th>MFE</th><th>置信度</th>
    </tr></thead>
    <tbody>
    {% for s in top_level %}
      <tr class="tier-{{ s.confidence_tier }}">
        <td class="tl">{{ s.signal_name | signal_zh }}</td>
        <td class="tl muted">{{ s.signal_name }}</td>
        <td class="tl">{{ s.direction | direction_zh }}</td>
        <td>{{ s.n }}{% if s.n_censored > 0 %}<span class="muted"> (+{{ s.n_censored }} cen)</span>{% endif %}</td>
        <td>{{ "%.1f"|format(s.win_rate * 100) }}% <span class="muted">[{{ "%.0f"|format(s.win_rate_low * 100) }}-{{ "%.0f"|format(s.win_rate_high * 100) }}]</span></td>
        <td class="{{ 'pos' if s.ev > 0 else 'neg' if s.ev < 0 else '' }}">{{ "%+.2f"|format(s.ev * 100) }}%</td>
        <td>{{ "%+.2f"|format(s.median_return * 100) }}%</td>
        <td class="pos">{{ "%+.2f"|format(s.avg_win * 100) }}%</td>
        <td class="neg">{{ "%+.2f"|format(s.avg_loss * 100) }}%</td>
        <td>{{ "%.2f"|format(s.profit_factor) if not s.profit_factor_inf else '∞' }}</td>
        <td>{{ "%.2f"|format(s.avg_mae * 100) }}%</td>
        <td>{{ "%+.2f"|format(s.avg_mfe * 100) }}%</td>
        <td><span class="tag tag-{{ s.confidence_tier }}">{{ s.confidence_tier | tier_zh }}</span></td>
      </tr>
    {% endfor %}
    </tbody>
  </table>
  {% endif %}

  {% if second_level %}
  <h2>🔍 二级细分表（信号 × 形态类型 × 方向）</h2>
  <table>
    <thead><tr>
      <th class="tl">信号</th><th class="tl">形态类型（中文）</th><th class="tl">英文原名</th><th class="tl">方向</th>
      <th>n</th><th>胜率</th><th>EV</th><th>中位</th>
      <th>PF</th><th>MAE</th><th>MFE</th><th>置信度</th>
    </tr></thead>
    <tbody>
    {% for s in second_level %}
      <tr class="tier-{{ s.confidence_tier }}">
        <td class="tl">{{ s.signal_name | signal_zh }}</td>
        <td class="tl">{{ s.event_type | event_type_zh }}</td>
        <td class="tl muted">{{ s.event_type }}</td>
        <td class="tl">{{ s.direction | direction_zh }}</td>
        <td>{{ s.n }}{% if s.n_censored > 0 %}<span class="muted"> (+{{ s.n_censored }} cen)</span>{% endif %}</td>
        <td>{{ "%.1f"|format(s.win_rate * 100) }}% <span class="muted">[{{ "%.0f"|format(s.win_rate_low * 100) }}-{{ "%.0f"|format(s.win_rate_high * 100) }}]</span></td>
        <td class="{{ 'pos' if s.ev > 0 else 'neg' if s.ev < 0 else '' }}">{{ "%+.2f"|format(s.ev * 100) }}%</td>
        <td>{{ "%+.2f"|format(s.median_return * 100) }}%</td>
        <td>{{ "%.2f"|format(s.profit_factor) if not s.profit_factor_inf else '∞' }}</td>
        <td>{{ "%.2f"|format(s.avg_mae * 100) }}%</td>
        <td>{{ "%+.2f"|format(s.avg_mfe * 100) }}%</td>
        <td><span class="tag tag-{{ s.confidence_tier }}">{{ s.confidence_tier | tier_zh }}</span></td>
      </tr>
    {% endfor %}
    </tbody>
  </table>
  {% endif %}

  {% if diagnostic_events %}
  <h2>🪣 诊断桶（做空/方向不明，未做收益模拟）</h2>
  <p class="muted">第一期仅做多合同：以下事件不计入主排名，仅展示样本量用于诊断。</p>
  <table>
    <thead><tr>
      <th class="tl">信号</th><th class="tl">形态类型（中文）</th><th class="tl">英文原名</th><th class="tl">方向</th>
      <th>n</th>
    </tr></thead>
    <tbody>
    {% for d in diagnostic_events %}
      <tr>
        <td class="tl">{{ d.signal_name | signal_zh }}</td>
        <td class="tl">{{ d.event_type | event_type_zh }}</td>
        <td class="tl muted">{{ d.event_type }}</td>
        <td class="tl">{{ d.direction | direction_zh }}</td>
        <td>{{ d.n }}</td>
      </tr>
    {% endfor %}
    </tbody>
  </table>
  {% endif %}

  {% if holdout_table and holdout_table.is_validated %}
  <h2>🛡 holdout 段独立验证（不参与训练决策）</h2>
  <p class="muted">以下统计基于 holdout 段独立事件，用于验证训练段结论。与训练段趋势一致 → 更可信；背离 → 警示过拟合。</p>
  <h3>holdout 顶层信号总表</h3>
  <table>
    <thead><tr>
      <th class="tl">信号</th><th class="tl">方向</th>
      <th>n</th><th>胜率</th><th>EV</th><th>置信度</th>
    </tr></thead>
    <tbody>
    {% for s in holdout_table.top_level %}
      <tr class="tier-{{ s.confidence_tier }}">
        <td class="tl">{{ s.signal_name | signal_zh }}</td>
        <td class="tl">{{ s.direction | direction_zh }}</td>
        <td>{{ s.n }}</td>
        <td>{{ "%.1f"|format(s.win_rate * 100) }}%</td>
        <td class="{{ 'pos' if s.ev > 0 else 'neg' if s.ev < 0 else '' }}">{{ "%+.2f"|format(s.ev * 100) }}%</td>
        <td><span class="tag tag-{{ s.confidence_tier }}">{{ s.confidence_tier | tier_zh }}</span></td>
      </tr>
    {% endfor %}
    </tbody>
  </table>
  <h3>holdout 二级细分表</h3>
  <table>
    <thead><tr>
      <th class="tl">信号</th><th class="tl">形态类型</th><th class="tl">方向</th>
      <th>n</th><th>胜率</th><th>EV</th><th>置信度</th>
    </tr></thead>
    <tbody>
    {% for s in holdout_table.second_level %}
      <tr class="tier-{{ s.confidence_tier }}">
        <td class="tl">{{ s.signal_name | signal_zh }}</td>
        <td class="tl">{{ s.event_type | event_type_zh }}</td>
        <td class="tl">{{ s.direction | direction_zh }}</td>
        <td>{{ s.n }}</td>
        <td>{{ "%.1f"|format(s.win_rate * 100) }}%</td>
        <td class="{{ 'pos' if s.ev > 0 else 'neg' if s.ev < 0 else '' }}">{{ "%+.2f"|format(s.ev * 100) }}%</td>
        <td><span class="tag tag-{{ s.confidence_tier }}">{{ s.confidence_tier | tier_zh }}</span></td>
      </tr>
    {% endfor %}
    </tbody>
  </table>
  {% elif holdout_table and not holdout_table.is_validated %}
  <h2>🛡 holdout 段：样本不足</h2>
  <p class="muted">holdout 段交易日或事件数不足，无法生成独立验证表。本报告整体标为"全样本探索性"。</p>
  {% endif %}

  <p class="muted" style="margin-top: 24px;">— 报告结束 —</p>
</div>
</body>
</html>
"""


@dataclass
class DiagnosticRow:
    signal_name: str
    event_type: str
    direction: str
    n: int


@dataclass
class HoldoutTable:
    """holdout 段聚合结果，用于报告并列展示。"""

    top_level: list[SliceStats]
    second_level: list[SliceStats]
    is_validated: bool  # 是否满足"已验证"条件（两段都达标且 holdout 非空）


def _holdout_status_text(split: HoldoutSplit) -> tuple[str, str]:
    """生成 holdout 状态文本。"""
    if split.holdout_start is None:
        if split.train_satisfied:
            return "全样本（holdout 关闭）", "用户指定 holdout_months=0"
        return "样本不足", f"训练段交易日不足 {split.min_trade_days}"
    if split.train_satisfied and split.holdout_satisfied:
        return "已验证", f"holdout 起 {split.holdout_start.date()}，训练/holdout 均满足最小交易日"
    if not split.train_satisfied:
        return "样本不足", f"训练段交易日 < {split.min_trade_days}"
    if not split.holdout_satisfied:
        return "训练段可用，holdout 不足", f"holdout 段交易日 < {split.min_trade_days}"
    return "部分验证", "训练/holdout 部分满足"


class StatsReporter:
    """胜率统计 HTML 报告生成器。"""

    def default_output_path(self, stamp: str | None = None) -> Path:
        stamp = stamp or datetime.now().strftime("%Y-%m-%d")
        return get_reports_dir() / f"{stamp}-winrate-stats.html"

    def render(
        self,
        top_level: list[SliceStats],
        second_level: list[SliceStats],
        diagnostic: list[DiagnosticRow],
        split: HoldoutSplit,
        total_events: int,
        total_censored: int,
        horizon: int,
        universe_size: int,
        output: Path,
        holdout_table: "HoldoutTable | None" = None,
    ) -> None:
        env = Environment()
        env.filters["signal_zh"] = _signal_zh
        env.filters["event_type_zh"] = _event_type_zh
        env.filters["direction_zh"] = _direction_zh
        env.filters["tier_zh"] = _tier_zh
        tmpl = env.from_string(TEMPLATE)
        holdout_status, holdout_detail = _holdout_status_text(split)

        # 给每个 stats 加 profit_factor_inf 标志，避免 jinja 里写 float('inf')
        for lst in (top_level, second_level):
            for s in lst:
                s.profit_factor_inf = bool(
                    s.profit_factor == float("inf")
                    or (isinstance(s.profit_factor, float) and math.isinf(s.profit_factor))
                )
        if holdout_table:
            for s in holdout_table.top_level + holdout_table.second_level:
                s.profit_factor_inf = bool(
                    s.profit_factor == float("inf")
                    or (isinstance(s.profit_factor, float) and math.isinf(s.profit_factor))
                )

        # 自动结论
        conclusions = generate_conclusions(
            top_level=top_level,
            second_level=second_level,
            holdout_table=holdout_table,
            total_events=total_events,
            holdout_status=holdout_status,
        )

        html = tmpl.render(
            stamp=datetime.now().strftime("%Y-%m-%d %H:%M"),
            generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            universe_size=universe_size,
            horizon=horizon,
            total_events=total_events,
            train_events=len(split.train_events),
            holdout_events=len(split.holdout_events),
            total_censored=total_censored,
            holdout_status=holdout_status,
            holdout_detail=holdout_detail,
            top_level=top_level,
            second_level=second_level,
            diagnostic_events=diagnostic,
            holdout_table=holdout_table,
            conclusions=conclusions,
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(html, encoding="utf-8")
        log.info("[stats-reporter] HTML 报告: %s", output)
