"""回测 HTML 报告生成器

输出风格与 reporter/html.py 一致（Apple 风、Jinja2 模板）。
权益曲线用 matplotlib 生成 SVG 内嵌，避免引入 plotly 依赖。
"""
from __future__ import annotations

import io
import logging
from datetime import datetime
from pathlib import Path

import matplotlib

# 无 GUI 后端，避免在服务器/CI 环境报错
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from jinja2 import Template

from quant_scanner.backtest.engine import BacktestReport

log = logging.getLogger(__name__)


TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>quant-scanner 回测报告 {{ stamp }}</title>
<style>
* { box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Helvetica Neue", sans-serif;
       background: #f5f5f7; color: #1d1d1f; margin: 0; padding: 20px; }
.container { max-width: 1200px; margin: 0 auto; background: white; border-radius: 12px;
             padding: 32px; box-shadow: 0 2px 12px rgba(0,0,0,0.06); }
h1 { margin: 0 0 8px; font-size: 28px; }
.subtitle { color: #6e6e73; margin-bottom: 24px; font-size: 14px; }
.kpi-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; margin-bottom: 24px; }
.kpi { background: #f5f5f7; padding: 16px; border-radius: 8px; }
.kpi-label { font-size: 12px; color: #6e6e73; text-transform: uppercase; letter-spacing: 0.5px; }
.kpi-value { font-size: 24px; font-weight: 600; margin-top: 4px; }
.kpi-value.pos { color: #0e6e2e; }
.kpi-value.neg { color: #9b1818; }
.section-title { font-size: 18px; font-weight: 600; margin: 24px 0 12px; border-bottom: 1px solid #e5e5e7; padding-bottom: 8px; }
.chart { margin: 16px 0; text-align: center; background: #fafafa; border-radius: 8px; padding: 12px; }
.chart svg { max-width: 100%; height: auto; }
table { width: 100%; border-collapse: collapse; margin-top: 8px; }
th { background: #f5f5f7; text-align: left; padding: 10px 8px; font-size: 12px;
     text-transform: uppercase; letter-spacing: 0.5px; color: #6e6e73; }
td { padding: 10px 8px; border-bottom: 1px solid #e5e5e7; font-size: 13px; }
tr:hover { background: #fafafa; }
.tag { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 11px;
       font-weight: 500; }
.tag-stop_loss { background: #ffe1e1; color: #9b1818; }
.tag-signal_exit { background: #fff4cc; color: #8a6d00; }
.tag-time_limit { background: #e1e8ff; color: #28338c; }
.tag-end_of_data { background: #e6e6e6; color: #555; }
.muted { color: #6e6e73; font-size: 12px; }
</style>
</head>
<body>
<div class="container">
  <h1>quant-scanner 回测报告</h1>
  <div class="subtitle">回测区间：{{ start }} ~ {{ end }} · 生成时间：{{ generated_at }}</div>

  <div class="kpi-grid">
    <div class="kpi"><div class="kpi-label">交易笔数</div><div class="kpi-value">{{ n_trades }}（{{ n_closed }} 平仓）</div></div>
    <div class="kpi"><div class="kpi-label">胜率</div><div class="kpi-value {{ 'pos' if win_rate >= 0.5 else 'neg' }}">{{ "%.1f"|format(win_rate * 100) }}%</div></div>
    <div class="kpi"><div class="kpi-label">平均收益</div><div class="kpi-value {{ 'pos' if avg_return >= 0 else 'neg' }}">{{ "%.2f"|format(avg_return * 100) }}%</div></div>
    <div class="kpi"><div class="kpi-label">总收益</div><div class="kpi-value {{ 'pos' if total_return >= 0 else 'neg' }}">{{ "%.2f"|format(total_return * 100) }}%</div></div>
    <div class="kpi"><div class="kpi-label">最大回撤</div><div class="kpi-value neg">{{ "%.2f"|format(max_drawdown * 100) }}%</div></div>
    <div class="kpi"><div class="kpi-label">夏普（年化）</div><div class="kpi-value">{{ "%.2f"|format(sharpe) }}</div></div>
    <div class="kpi"><div class="kpi-label">盈亏比</div><div class="kpi-value">{{ "%.2f"|format(profit_factor) }}</div></div>
    <div class="kpi"><div class="kpi-label">持仓上限</div><div class="kpi-value">{{ max_positions }}</div></div>
    <div class="kpi"><div class="kpi-label">信号</div><div class="kpi-value" style="font-size:14px">{{ signals_text }}</div></div>
  </div>

  <div class="section-title">权益曲线</div>
  <div class="chart">
    {{ equity_svg }}
  </div>

  <div class="section-title">按股票统计</div>
  {% if per_ticker_html %}
  {{ per_ticker_html|safe }}
  {% else %}
  <p class="muted">无平仓交易</p>
  {% endif %}

  <div class="section-title">最近 {{ recent_trades|length }} 笔交易</div>
  {% if recent_trades %}
  <table>
    <thead><tr>
      <th>Ticker</th><th>买入日</th><th>买入价</th><th>卖出日</th><th>卖出价</th>
      <th>收益</th><th>持有天数</th><th>原因</th>
    </tr></thead>
    <tbody>
    {% for t in recent_trades %}
      <tr>
        <td><strong>{{ t.ticker }}</strong></td>
        <td>{{ t.entry_date }}</td>
        <td>{{ "%.2f"|format(t.entry_price) }}</td>
        <td>{{ t.exit_date or "-" }}</td>
        <td>{{ "%.2f"|format(t.exit_price) if t.exit_price else "-" }}</td>
        <td style="color: {% if t.return_pct and t.return_pct > 0 %}#0e6e2e{% elif t.return_pct %}#9b1818{% else %}#666{% endif %};">
          {{ "%.2f%%"|format(t.return_pct * 100) if t.return_pct is not none else "-" }}
        </td>
        <td>{{ t.holding_days if t.holding_days is not none else "-" }}</td>
        <td>{% if t.exit_reason %}<span class="tag tag-{{ t.exit_reason }}">{{ t.exit_reason }}</span>{% endif %}</td>
      </tr>
    {% endfor %}
    </tbody>
  </table>
  {% else %}
  <p class="muted">无交易记录</p>
  {% endif %}
</div>
</body>
</html>
"""


def _render_equity_svg(equity_curve) -> str:
    """生成权益曲线 SVG 字符串"""
    if equity_curve is None or len(equity_curve) < 2:
        return '<p class="muted">权益曲线数据不足</p>'

    fig, ax = plt.subplots(figsize=(10, 3.5), dpi=100)
    eq = equity_curve.copy()
    # 转 100 起点
    eq_norm = eq / eq.iloc[0] * 100.0
    ax.plot(eq_norm.index, eq_norm.values, color="#1d1d1f", linewidth=1.4)
    ax.fill_between(eq_norm.index, 100, eq_norm.values, where=(eq_norm.values >= 100), alpha=0.15, color="#0e6e2e")
    ax.fill_between(eq_norm.index, 100, eq_norm.values, where=(eq_norm.values < 100), alpha=0.15, color="#9b1818")
    ax.axhline(100, color="#999", linewidth=0.6, linestyle="--")

    ax.set_title("Equity Curve (base = 100)", fontsize=12, loc="left", pad=8)
    ax.set_ylabel("Equity", fontsize=10)
    ax.grid(True, alpha=0.25)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    for label in ax.get_xticklabels():
        label.set_rotation(15)
        label.set_fontsize(9)

    buf = io.StringIO()
    fig.tight_layout()
    fig.savefig(buf, format="svg", bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()


class BacktestHTMLReporter:
    """回测 HTML 报告渲染器"""

    def render(self, report: BacktestReport, output: Path) -> None:
        # 按 ticker 统计表
        per_ticker_df = report.per_ticker_stats()
        if not per_ticker_df.empty:
            per_ticker_html = per_ticker_df.to_html(
                index=False,
                border=0,
                classes="",
                float_format=lambda x: f"{x*100:.2f}%" if abs(x) < 10 else f"{x:.2f}",
            )
        else:
            per_ticker_html = ""

        # 最近 20 笔交易（按平仓日倒序）
        closed_sorted = sorted(
            [t for t in report.trades if t.exit_date is not None],
            key=lambda t: t.exit_date,
            reverse=True,
        )[:20]

        def _holding_days(t):
            if t.exit_date is None or t.entry_date is None:
                return None
            return (t.exit_date - t.entry_date).days

        recent_trades = [
            {
                "ticker": t.ticker,
                "entry_date": t.entry_date.strftime("%Y-%m-%d") if hasattr(t.entry_date, "strftime") else str(t.entry_date),
                "entry_price": t.entry_price,
                "exit_date": t.exit_date.strftime("%Y-%m-%d") if t.exit_date and hasattr(t.exit_date, "strftime") else (str(t.exit_date) if t.exit_date else None),
                "exit_price": t.exit_price,
                "return_pct": t.return_pct,
                "holding_days": _holding_days(t),
                "exit_reason": t.exit_reason,
            }
            for t in closed_sorted
        ]

        # 权益曲线 SVG
        equity_svg = _render_equity_svg(report.equity_curve)

        # summary
        summary = report.summary_dict()

        html = Template(TEMPLATE).render(
            stamp=datetime.now().strftime("%Y-%m-%d %H:%M"),
            generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            start=report.start,
            end=report.end,
            n_trades=summary["n_trades"],
            n_closed=summary["n_closed"],
            win_rate=summary["win_rate"],
            avg_return=summary["avg_return"],
            total_return=summary["total_return"],
            max_drawdown=summary["max_drawdown"],
            sharpe=summary["sharpe"],
            profit_factor=summary["profit_factor"],
            max_positions=getattr(report, "_max_positions", "-"),
            signals_text=", ".join(getattr(report, "_signals", []) or ["-"]),
            equity_svg=equity_svg,
            per_ticker_html=per_ticker_html,
            recent_trades=recent_trades,
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(html, encoding="utf-8")
        log.info(f"[backtest] HTML 报告已生成: {output}")
