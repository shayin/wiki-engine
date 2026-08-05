"""HTML 报告生成器

P5 升级：CAN SLIM 字母条、形态徽章、综合分柱状图、目标价/颈线高亮。
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from jinja2 import Environment

from quant_scanner.utils.config import get_reports_dir

if TYPE_CHECKING:
    from quant_scanner.scanner.engine import ScanReport

log = logging.getLogger(__name__)

TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>quant-scanner 报告 {{ stamp }}</title>
<style>
* { box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Helvetica Neue", sans-serif;
       background: #f5f5f7; color: #1d1d1f; margin: 0; padding: 20px; }
.container { max-width: 1400px; margin: 0 auto; background: white; border-radius: 12px;
             padding: 32px; box-shadow: 0 2px 12px rgba(0,0,0,0.06); }
h1 { margin: 0 0 8px; font-size: 28px; }
.subtitle { color: #6e6e73; margin-bottom: 24px; font-size: 14px; }
.stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin-bottom: 24px; }
.stat { background: #f5f5f7; padding: 16px; border-radius: 8px; }
.stat-label { font-size: 12px; color: #6e6e73; text-transform: uppercase; letter-spacing: 0.5px; }
.stat-value { font-size: 24px; font-weight: 600; margin-top: 4px; }
table { width: 100%; border-collapse: collapse; margin-top: 16px; }
th { background: #f5f5f7; text-align: left; padding: 12px 8px; font-size: 13px;
     text-transform: uppercase; letter-spacing: 0.5px; color: #6e6e73;
     position: sticky; top: 0; z-index: 1; }
td { padding: 12px 8px; border-bottom: 1px solid #e5e5e7; font-size: 14px; vertical-align: middle; }
tr:hover { background: #fafafa; }
.tag { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 12px;
       font-weight: 500; }
.tag-pass { background: #d1f4d8; color: #0e6e2e; }
.tag-fail { background: #ffe1e1; color: #9b1818; }
.score { font-weight: 600; }
.score-pos { color: #0e6e2e; }
.score-neg { color: #9b1818; }
.score-zero { color: #6e6e73; }

/* 综合分柱状图 */
.bar-cell { position: relative; min-width: 120px; }
.bar-track { background: #eee; border-radius: 4px; height: 20px; position: relative; overflow: hidden; }
.bar-fill { height: 100%; border-radius: 4px; }
.bar-fill.pos { background: linear-gradient(90deg, #b7e8b7, #4caf50); }
.bar-fill.neg { background: linear-gradient(90deg, #e8b7b7, #f44336); }
.bar-label { position: absolute; top: 0; left: 50%; transform: translateX(-50%);
             font-size: 11px; font-weight: 600; color: #1d1d1f; line-height: 20px;
             text-shadow: 0 0 2px white; }

/* CAN SLIM 字母条 */
.canslim-bar { display: inline-flex; gap: 2px; }
.canslim-letter { display: inline-block; width: 18px; height: 18px;
                  border-radius: 3px; font-size: 10px; text-align: center;
                  line-height: 18px; font-weight: 700; color: white; }
.canslim-strong { background: #4caf50; }
.canslim-mid    { background: #ff9800; }
.canslim-weak   { background: #9b1818; }
.canslim-na     { background: #bbb; color: #555; }

/* 形态徽章 */
.pattern-badge { display: inline-block; padding: 3px 10px; border-radius: 12px;
                 font-size: 11px; font-weight: 600; margin-right: 4px; }
.pat-bullish { background: #d1f4d8; color: #0e6e2e; }
.pat-bearish { background: #ffe1e1; color: #9b1818; }
.pat-neutral { background: #e8e8ec; color: #444; }
.pat-none    { background: transparent; color: #999; font-weight: 400; }

/* 目标价 / 颈线高亮 */
.target-box { background: #fff8e1; border-left: 3px solid #ff9800;
              padding: 4px 8px; margin: 2px 0; font-size: 12px; border-radius: 3px; }

.details { background: #f9f9fb; padding: 14px; border-radius: 6px; margin-top: 8px;
           font-size: 12px; color: #444; line-height: 1.6; }
.details h4 { margin: 6px 0 2px 0; font-size: 13px; color: #1d1d1f; }
.details ul { margin: 4px 0; padding-left: 20px; }
.details .meta { color: #6e6e73; font-size: 11px; }

.expandable { cursor: pointer; user-select: none; }
.expandable:hover { background: #f0f0f5; }

/* 信号详情卡片 */
.signal-card { background: white; border: 1px solid #e5e5e7; border-radius: 6px;
               padding: 10px; margin: 6px 0; }
.signal-card-header { display: flex; justify-content: space-between;
                      align-items: center; margin-bottom: 6px; }
.signal-name { font-weight: 600; color: #1d1d1f; }

.legend { display: flex; gap: 16px; flex-wrap: wrap; margin-top: 16px; font-size: 12px; color: #6e6e73; }
.legend-item { display: inline-flex; align-items: center; gap: 6px; }
.legend-dot { width: 12px; height: 12px; border-radius: 2px; display: inline-block; }
</style>
</head>
<body>
<div class="container">
  <h1>quant-scanner 扫描报告</h1>
  <div class="subtitle">生成时间：{{ generated_at }} · 共扫描 {{ total }} 只 · 通过 {{ passed }} 只 · 平均分 {{ "%.2f"|format(avg_score) }}</div>

  <div class="stats">
    <div class="stat">
      <div class="stat-label">扫描总数</div>
      <div class="stat-value">{{ total }}</div>
    </div>
    <div class="stat">
      <div class="stat-label">通过筛子</div>
      <div class="stat-value" style="color: #0e6e2e">{{ passed }}</div>
    </div>
    <div class="stat">
      <div class="stat-label">通过率</div>
      <div class="stat-value">{{ "%.1f"|format(passed * 100 / total if total else 0) }}%</div>
    </div>
    <div class="stat">
      <div class="stat-label">最高综合分</div>
      <div class="stat-value">{{ "%.2f"|format(max_score) }}</div>
    </div>
  </div>

  <div class="legend">
    <span class="legend-item"><span class="legend-dot canslim-strong"></span> 字母强</span>
    <span class="legend-item"><span class="legend-dot canslim-mid"></span> 中</span>
    <span class="legend-item"><span class="legend-dot canslim-weak"></span> 弱</span>
    <span class="legend-item"><span class="pattern-badge pat-bullish">看多形态</span></span>
    <span class="legend-item"><span class="pattern-badge pat-bearish">看空形态</span></span>
  </div>

  <table>
    <thead>
      <tr>
        <th>Ticker</th>
        <th>综合分</th>
        {% for s in signal_names %}
        <th>{{ s }}</th>
        {% endfor %}
        <th>关键形态</th>
        <th>通过</th>
        <th>详情</th>
      </tr>
    </thead>
    <tbody>
      {% for r in results %}
      <tr>
        <td><strong>{{ r.ticker }}</strong></td>
        <td class="bar-cell">
          <div class="bar-track">
            <div class="bar-fill {{ 'pos' if r.composite_score >= 0 else 'neg' }}"
                 style="width: {{ '%.0f'|format((r.composite_score|abs) * 100) }}%"></div>
            <span class="bar-label">{{ "%.2f"|format(r.composite_score) }}</span>
          </div>
        </td>
        {% for sr in r.signals %}
        <td>
          {% if sr.signal_name == 'can_slim' and sr.details and sr.details.letters %}
            <div class="canslim-bar" title="{{ sr.reasons|join('; ') }}">
              {% for letter in 'CANSLIM' %}
                {% set v = sr.details.letters.get(letter, none) %}
                {% if v is none %}
                  <span class="canslim-letter canslim-na">{{ letter }}</span>
                {% elif v >= 0.7 %}
                  <span class="canslim-letter canslim-strong">{{ letter }}</span>
                {% elif v >= 0.4 %}
                  <span class="canslim-letter canslim-mid">{{ letter }}</span>
                {% else %}
                  <span class="canslim-letter canslim-weak">{{ letter }}</span>
                {% endif %}
              {% endfor %}
            </div>
          {% else %}
            <span class="score {{ 'score-pos' if sr.value > 0.05 else 'score-neg' if sr.value < -0.05 else 'score-zero' }}">
              {{ "%.2f"|format(sr.value) }}
            </span>
            {% if sr.passed %}<span class="tag tag-pass">✓</span>{% else %}<span class="tag tag-fail">✗</span>{% endif %}
          {% endif %}
        </td>
        {% endfor %}
        <td>{{ r.signals | pattern_badge }}</td>
        <td>
          {% if r.passed %}<span class="tag tag-pass">PASS</span>{% else %}<span class="tag tag-fail">FAIL</span>{% endif %}
        </td>
        <td class="expandable" onclick="var n=this.nextElementSibling; n.style.display = n.style.display === 'none' ? 'table-row' : 'none';">
          [展开]
        </td>
      </tr>
      <tr style="display: none;">
        <td colspan="{{ r.signals|length + 5 }}">
          <div class="details">
            {% for sr in r.signals %}
            <div class="signal-card">
              <div class="signal-card-header">
                <span class="signal-name">{{ sr.signal_name }}</span>
                <span class="score {{ 'score-pos' if sr.value > 0.05 else 'score-neg' if sr.value < -0.05 else 'score-zero' }}">
                  {{ "%.2f"|format(sr.value) }} {% if sr.passed %}✓{% else %}✗{% endif %}
                </span>
              </div>
              {% if sr.details %}
                {% if sr.details.target or sr.details.neckline %}
                  <div class="target-box">
                    {% if sr.details.neckline %}颈线 {{ "%.2f"|format(sr.details.neckline) }} · {% endif %}
                    {% if sr.details.target %}目标 {{ "%.2f"|format(sr.details.target) }}{% endif %}
                    {% if sr.details.confirmed is defined %}
                      · {% if sr.details.confirmed %}已确认{% else %}未确认{% endif %}
                    {% endif %}
                  </div>
                {% endif %}
                {% if sr.signal_name == 'can_slim' and sr.details.letters %}
                  <div class="meta">字母得分：{% for L, v in sr.details.letters.items() %}{{ L }}={{ "%.2f"|format(v) }} {% endfor %}</div>
                {% endif %}
                {% if sr.details.phase %}<div class="meta">阶段：{{ sr.details.phase }}</div>{% endif %}
                {% if sr.details.state %}<div class="meta">市场状态：{{ sr.details.state }}</div>{% endif %}
                {% if sr.details.pattern_type %}<div class="meta">形态：{{ sr.details.pattern_type }}</div>{% endif %}
              {% endif %}
              <ul>{% for reason in sr.reasons %}<li>{{ reason }}</li>{% endfor %}</ul>
            </div>
            {% endfor %}
          </div>
        </td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
</div>
</body>
</html>
"""


def _pattern_badge_filter(signals: list) -> str:
    """从信号列表提取最值得展示的形态徽章。

    Jinja 自定义 filter：在所有 signal 的 details 中找 pattern_type / phase / state，
    返回一个或多个徽章 HTML 字符串。
    """
    from markupsafe import Markup
    badges: list[str] = []
    for sr in signals or []:
        d = getattr(sr, "details", None) or {}
        ptype = d.get("pattern_type")
        if ptype:
            bullish_types = (
                "DOUBLE_BOTTOM", "HEAD_SHOULDERS_BOTTOM", "TRIPLE_BOTTOM",
                "ROUNDING_BOTTOM", "V_BOTTOM", "ISLAND_BOTTOM", "BULL_FLAG",
                "ASCENDING_TRIANGLE",
            )
            bearish_types = (
                "DOUBLE_TOP", "HEAD_SHOULDERS_TOP", "TRIPLE_TOP",
                "ROUNDING_TOP", "V_TOP", "ISLAND_TOP", "BEAR_FLAG",
                "DESCENDING_TRIANGLE", "RISING_WEDGE",
            )
            if ptype in bullish_types:
                cls = "pat-bullish"
            elif ptype in bearish_types:
                cls = "pat-bearish"
            else:
                cls = "pat-neutral"
            badges.append(f'<span class="pattern-badge {cls}">{ptype}</span>')
    if not badges:
        return Markup('<span class="pattern-badge pat-none">—</span>')
    return Markup("".join(badges))


class HTMLReporter:
    def default_output_path(self, stamp: str | None = None) -> Path:
        stamp = stamp or datetime.now().strftime("%Y-%m-%d")
        return get_reports_dir() / f"{stamp}-scan.html"

    def render(self, report: ScanReport, output: Path) -> None:
        # 按 composite_score 倒序
        results = sorted(report.results, key=lambda x: -x["composite_score"])
        signal_names = [s.signal_name for s in report.results[0]["signals"]] if report.results else []
        passed = len(report.passed())
        max_score = max((r["composite_score"] for r in report.results), default=0.0)
        scores = [r["composite_score"] for r in report.results] or [0.0]
        avg_score = sum(scores) / len(scores)

        env = Environment()
        env.filters["pattern_badge"] = _pattern_badge_filter
        tmpl = env.from_string(TEMPLATE)
        html = tmpl.render(
            stamp=datetime.now().strftime("%Y-%m-%d %H:%M"),
            generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            total=len(report.results),
            passed=passed,
            max_score=max_score,
            avg_score=avg_score,
            signal_names=signal_names,
            results=results,
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(html, encoding="utf-8")
        log.info(f"HTML 报告已生成: {output}")
