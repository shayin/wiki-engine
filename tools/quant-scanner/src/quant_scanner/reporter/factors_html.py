"""Alpha 因子 HTML 报告生成器

独立于 scan HTML 报告：扫描报告按 Signal 聚合，因子报告按 alpha 聚合，结构完全不同。
保持模块独立避免 html.py 膨胀。

用法：
    from quant_scanner.reporter.factors_html import render_alpha_factors
    render_alpha_factors(ticker="NVDA", horizon=5, rows=[...], output=Path(...))
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from jinja2 import Environment

log = logging.getLogger(__name__)

TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>{{ ticker }} · Alpha101 因子扫描 {{ stamp }}</title>
<style>
* { box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Helvetica Neue", sans-serif;
       background: #f5f5f7; color: #1d1d1f; margin: 0; padding: 20px; }
.container { max-width: 1100px; margin: 0 auto; background: white; border-radius: 12px;
             padding: 32px; box-shadow: 0 2px 12px rgba(0,0,0,0.06); }
h1 { margin: 0 0 8px; font-size: 26px; }
.subtitle { color: #6e6e73; margin-bottom: 24px; font-size: 14px; }
.summary { display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin-bottom: 24px; }
.stat { background: #f5f5f7; padding: 16px; border-radius: 8px; }
.stat-label { font-size: 12px; color: #6e6e73; text-transform: uppercase; letter-spacing: 0.5px; }
.stat-value { font-size: 22px; font-weight: 600; margin-top: 4px; }
table { width: 100%; border-collapse: collapse; margin-top: 16px; }
th { background: #f5f5f7; text-align: left; padding: 12px 8px; font-size: 13px;
     text-transform: uppercase; letter-spacing: 0.5px; color: #6e6e73; }
td { padding: 12px 8px; border-bottom: 1px solid #e5e5e7; font-size: 14px; }
tr:hover { background: #fafafa; }
.ic-pos { color: #0e6e2e; font-weight: 600; }
.ic-neg { color: #9b1818; font-weight: 600; }
.ic-zero { color: #6e6e73; }
.ic-bar { background: #eee; border-radius: 4px; height: 8px; position: relative; min-width: 80px; display: inline-block; vertical-align: middle; }
.ic-bar-fill { height: 100%; border-radius: 4px; position: absolute; top: 0; }
.ic-bar-fill.pos { background: #4caf50; left: 50%; }
.ic-bar-fill.neg { background: #f44336; right: 50%; }
.ic-bar-mid { position: absolute; left: 50%; top: 0; bottom: 0; width: 1px; background: #999; }
.tag { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 12px; font-weight: 500; }
.tag-strong { background: #d1f4d8; color: #0e6e2e; }
.tag-mid { background: #fff4d1; color: #8a6d00; }
.tag-weak { background: #f0f0f0; color: #6e6e73; }
.note { background: #fff8e1; border-left: 3px solid #ffa000; padding: 12px 16px; margin-top: 16px; font-size: 13px; }
.interp { background: #e3f2fd; border-left: 3px solid #1976d2; padding: 12px 16px; margin-top: 16px; font-size: 13px; }
.interp h3 { margin: 0 0 8px 0; font-size: 14px; color: #0d47a1; }
</style>
</head>
<body>
<div class="container">
<h1>{{ ticker }} · Alpha101 因子扫描</h1>
<div class="subtitle">截至 {{ stamp }} · {{ period }} 样本 · {{ horizon }} 日前瞻收益 · 共 {{ total }} 个 alpha</div>

<div class="summary">
  <div class="stat">
    <div class="stat-label">有效因子数（|IC|&gt;0.05）</div>
    <div class="stat-value">{{ strong_count }}</div>
  </div>
  <div class="stat">
    <div class="stat-label">最高 |IC|</div>
    <div class="stat-value">{{ max_ic }}</div>
  </div>
  <div class="stat">
    <div class="stat-label">平均 |IC|</div>
    <div class="stat-value">{{ avg_ic }}</div>
  </div>
  <div class="stat">
    <div class="stat-label">置信度</div>
    <div class="stat-value">{{ confidence }}</div>
  </div>
</div>

<table>
<thead>
<tr>
  <th>排名</th>
  <th>Alpha</th>
  <th>IC</th>
  <th>IR</th>
  <th>非 NaN</th>
  <th>强度</th>
</tr>
</thead>
<tbody>
{% for r in rows %}
<tr>
  <td>{{ loop.index }}</td>
  <td>{{ r.name }}</td>
  <td>
    <span class="{{ 'ic-pos' if r.ic > 0.03 else 'ic-neg' if r.ic < -0.03 else 'ic-zero' }}">
      {{ '%+0.3f'|format(r.ic) if r.ic == r.ic else 'NaN' }}
    </span>
    <span class="ic-bar">
      <span class="ic-bar-mid"></span>
      {% if r.ic == r.ic and r.ic > 0 %}
        <span class="ic-bar-fill pos" style="width: {{ (r.ic * 1000) | int if r.ic * 1000 < 50 else 50 }}%"></span>
      {% elif r.ic == r.ic and r.ic < 0 %}
        <span class="ic-bar-fill neg" style="width: {{ (-r.ic * 1000) | int if -r.ic * 1000 < 50 else 50 }}%"></span>
      {% endif %}
    </span>
  </td>
  <td>{{ '%+0.3f'|format(r.ir) if r.ir == r.ir else 'NaN' }}</td>
  <td>{{ r.non_na }}</td>
  <td>
    {% if r.ic == r.ic and r.ic|abs > 0.05 %}
      <span class="tag tag-strong">有效</span>
    {% elif r.ic == r.ic and r.ic|abs > 0.03 %}
      <span class="tag tag-mid">有信号</span>
    {% else %}
      <span class="tag tag-weak">噪声</span>
    {% endif %}
  </td>
</tr>
{% endfor %}
</tbody>
</table>

{% if warning %}
<div class="note">⚠️ {{ warning }}</div>
{% endif %}

<div class="interp">
  <h3>解读参考</h3>
  <p><strong>IC</strong>（Information Coefficient，信息系数）：因子值 vs 前瞻收益的 Spearman 秩相关。学术标准 |IC|&gt;0.03 有信号 / &gt;0.05 有效 / &gt;0.10 警惕过拟合。</p>
  <p><strong>IR</strong>（Information Ratio，信息比率）：IC 均值 / IC 标准差，&gt;0.5 算高质量稳定因子。</p>
  <p><strong>统计套利视角</strong>：技术指标（RSI/MA）回答"现在超买还是超卖"，Alpha 因子回答"哪些数学结构在历史上预测过这只股票"。两者互补。</p>
</div>

</div>
</body>
</html>
"""


def _confidence_label(strong_count: int, total: int, top_ic_abs: float) -> tuple[str, str]:
    """返回 (置信度等级, 警告文本)。"""
    if total == 0:
        return "无数据", "无有效 alpha 数据"
    ratio = strong_count / total
    if ratio >= 0.25 and top_ic_abs > 0.05:
        return "高", ""
    if ratio <= 0.10 and top_ic_abs < 0.02:
        return "低", f"20 个 alpha 中仅 {strong_count} 个有效，当前走势无历史可类比结构，技术面结论建议降级"
    if top_ic_abs > 0.10:
        return "中", f"top alpha |IC|={top_ic_abs:.3f} 异常高，警惕过拟合或被挖烂"
    return "中", ""


def render_alpha_factors(
    *,
    ticker: str,
    horizon: int,
    period: str,
    rows: list[dict],
    output: Path,
) -> None:
    """渲染 Alpha 因子扫描 HTML 报告

    Args:
        ticker: 标的代码
        horizon: 前瞻收益天数
        period: 样本周期（如 '2y'）
        rows: 每行 dict {name, ic, ir, non_na}，按 |IC| 降序
        output: HTML 输出路径
    """
    valid_ics = [abs(r["ic"]) for r in rows if r["ic"] == r["ic"]]
    strong_count = sum(1 for v in valid_ics if v > 0.05)
    max_ic = max(valid_ics) if valid_ics else 0.0
    avg_ic = (sum(valid_ics) / len(valid_ics)) if valid_ics else 0.0
    confidence, warning = _confidence_label(strong_count, len(rows), max_ic)

    env = Environment()
    tmpl = env.from_string(TEMPLATE)
    html = tmpl.render(
        ticker=ticker,
        stamp=datetime.now().strftime("%Y-%m-%d %H:%M"),
        horizon=horizon,
        period=period,
        total=len(rows),
        rows=rows,
        strong_count=strong_count,
        max_ic=f"{max_ic:.3f}",
        avg_ic=f"{avg_ic:.3f}",
        confidence=confidence,
        warning=warning,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html, encoding="utf-8")
    log.info(f"Alpha 因子 HTML 报告已生成: {output}")


__all__ = ["render_alpha_factors", "render_alpha_matrix"]


MATRIX_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>{{ ticker }} · Alpha101 多周期 IC 矩阵 {{ stamp }}</title>
<style>
* { box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Helvetica Neue", sans-serif;
       background: #f5f5f7; color: #1d1d1f; margin: 0; padding: 20px; }
.container { max-width: 1200px; margin: 0 auto; background: white; border-radius: 12px;
             padding: 32px; box-shadow: 0 2px 12px rgba(0,0,0,0.06); }
h1 { margin: 0 0 8px; font-size: 26px; }
.subtitle { color: #6e6e73; margin-bottom: 24px; font-size: 14px; }
table { width: 100%; border-collapse: collapse; margin-top: 16px; }
th { background: #f5f5f7; text-align: right; padding: 12px 8px; font-size: 13px;
     text-transform: uppercase; letter-spacing: 0.5px; color: #6e6e73; }
th:first-child { text-align: left; }
td { padding: 12px 8px; border-bottom: 1px solid #e5e5e7; font-size: 14px; text-align: right; }
td:first-child { text-align: left; font-weight: 500; }
tr:hover { background: #fafafa; }
tr.best-row { background: #f1f8e9; }
.ic-cell { display: inline-block; padding: 2px 6px; border-radius: 3px; min-width: 50px; }
.ic-strong-pos { background: #4caf50; color: white; }
.ic-mid-pos { background: #c8e6c9; color: #1b5e20; }
.ic-strong-neg { background: #f44336; color: white; }
.ic-mid-neg { background: #ffcdd2; color: #b71c1c; }
.ic-weak { color: #999; }
.best-h { background: #fff8e1; padding: 3px 8px; border-radius: 3px; font-weight: 600; color: #6a4c00; }
.note { background: #e3f2fd; border-left: 3px solid #1976d2; padding: 12px 16px; margin-top: 16px; font-size: 13px; }
</style>
</head>
<body>
<div class="container">
<h1>{{ ticker }} · Alpha101 多周期 IC 矩阵</h1>
<div class="subtitle">截至 {{ stamp }} · {{ period }} 样本 · horizons: {{ horizons|join(', ') }} 天</div>

<table>
<thead>
<tr>
  <th>Alpha</th>
  {% for h in horizons %}<th>IC@{{ h }}d</th>{% endfor %}
  <th>最佳周期</th>
  <th>最佳 IC</th>
</tr>
</thead>
<tbody>
{% for r in rows %}
<tr class="{{ 'best-row' if r.best_ic == r.best_ic and r.best_ic|abs > 0.05 else '' }}">
  <td>{{ r.name }}</td>
  {% for h in horizons %}
    {% set v = r.ics.get(h, none) %}
    <td>
      {% if v is none or v != v %}
        <span class="ic-cell ic-weak">NaN</span>
      {% elif v > 0.05 %}
        <span class="ic-cell ic-strong-pos">{{ '%+0.3f'|format(v) }}</span>
      {% elif v > 0.03 %}
        <span class="ic-cell ic-mid-pos">{{ '%+0.3f'|format(v) }}</span>
      {% elif v < -0.05 %}
        <span class="ic-cell ic-strong-neg">{{ '%+0.3f'|format(v) }}</span>
      {% elif v < -0.03 %}
        <span class="ic-cell ic-mid-neg">{{ '%+0.3f'|format(v) }}</span>
      {% else %}
        <span class="ic-cell ic-weak">{{ '%+0.3f'|format(v) }}</span>
      {% endif %}
    </td>
  {% endfor %}
  <td>{% if r.best_horizon %}<span class="best-h">{{ r.best_horizon }}d</span>{% else %}—{% endif %}</td>
  <td>{{ '%+0.3f'|format(r.best_ic) if r.best_ic == r.best_ic else 'NaN' }}</td>
</tr>
{% endfor %}
</tbody>
</table>

<div class="note">
<strong>解读</strong>：颜色越深表示 IC 绝对值越大（绿色=正相关/看多，红色=负相关/反向因子）。
<br>每个 alpha 在不同持仓周期下表现不同——IC@1d 强 = 短线因子，IC@60d 强 = 长线因子。
<br>「最佳周期」是该 alpha 在所有周期里 |IC| 最大的那个，可决定该 alpha 适合的持有期。
</div>

</div>
</body>
</html>
"""


def render_alpha_matrix(
    *,
    ticker: str,
    period: str,
    horizons: list[int],
    rows: list[dict],
    output: Path,
) -> None:
    """渲染 Alpha 因子多周期 IC 矩阵 HTML 报告

    Args:
        ticker: 标的
        period: 样本周期
        horizons: 周期列表（如 [1, 5, 10, 20, 60]）
        rows: 每行 dict {name, ics: {horizon: ic}, best_horizon, best_ic}
        output: HTML 输出路径
    """
    env = Environment()
    tmpl = env.from_string(MATRIX_TEMPLATE)
    html = tmpl.render(
        ticker=ticker,
        stamp=datetime.now().strftime("%Y-%m-%d %H:%M"),
        period=period,
        horizons=horizons,
        rows=rows,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html, encoding="utf-8")
    log.info(f"Alpha 矩阵 HTML 报告已生成: {output}")
