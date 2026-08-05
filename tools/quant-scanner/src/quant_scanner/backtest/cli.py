"""backtest 命令：CLI 入口

风格对齐 scanner/cli.py（click + rich 终端输出 + Jinja2 HTML 报告）。
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from quant_scanner.backtest.engine import Backtester
from quant_scanner.backtest.reporter import BacktestHTMLReporter
from quant_scanner.data.loader import DataLoader
from quant_scanner.signals.can_slim import CANSLIMSignal
from quant_scanner.signals.trend_template import TrendTemplateSignal
from quant_scanner.signals.vcp import VCPSignal

console = Console()
log = logging.getLogger(__name__)


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


@click.command(name="backtest")
@click.option("--universe", required=True, help="逗号分隔的 ticker 列表，如 TSLA,NVDA,AAPL")
@click.option("--start", default="2020-01-01", help="回测起始日 YYYY-MM-DD")
@click.option("--end", default="2025-12-31", help="回测结束日 YYYY-MM-DD")
@click.option("--rebalance", default="W", type=click.Choice(["W", "D"]), help="调仓频率 W=周 D=日")
@click.option("--entry-threshold", default=0.7, type=float, help="综合分买入阈值")
@click.option("--exit-threshold", default=0.3, type=float, help="综合分卖出阈值")
@click.option("--stop-loss", default=0.08, type=float, help="止损比例（如 0.08=8%）")
@click.option("--time-limit", default=60, type=int, help="持仓上限天数")
@click.option("--max-positions", default=5, type=int, help="同时持仓上限")
@click.option("--commission", default=0.001, type=float, help="单边佣金比例")
@click.option("--slippage", default=0.001, type=float, help="单边滑点比例")
@click.option(
    "--signals",
    default="trend_template,vcp,can_slim",
    help="逗号分隔的信号列表，可选 trend_template/vcp/can_slim",
)
@click.option("--output", "-o", type=click.Path(path_type=Path), help="HTML 报告输出路径")
@click.option("--no-html", is_flag=True, help="不生成 HTML 报告")
@click.option("--verbose", "-v", is_flag=True, help="详细日志")
def backtest(
    universe: str,
    start: str,
    end: str,
    rebalance: str,
    entry_threshold: float,
    exit_threshold: float,
    stop_loss: float,
    time_limit: int,
    max_positions: int,
    commission: float,
    slippage: float,
    signals: str,
    output: Path | None,
    no_html: bool,
    verbose: bool,
) -> None:
    """对一组 ticker 运行回测，输出策略指标 + HTML 报告"""
    _setup_logging(verbose)

    ticker_list = [t.strip().upper() for t in universe.split(",") if t.strip()]
    if not ticker_list:
        console.print("[red]--universe 不能为空[/red]")
        raise click.exceptions.Exit(1)

    signal_map = {
        "trend_template": TrendTemplateSignal,
        "vcp": VCPSignal,
        "can_slim": CANSLIMSignal,
    }
    signal_names = [s.strip() for s in signals.split(",") if s.strip()]
    signal_instances = []
    for sn in signal_names:
        if sn not in signal_map:
            console.print(f"[red]未知信号: {sn}（可选: {','.join(signal_map.keys())}[/red]")
            raise click.exceptions.Exit(1)
        signal_instances.append(signal_map[sn]())

    console.print(
        f"[bold green]开始回测[/bold green] {len(ticker_list)} 只股票，"
        f"信号={[s.name for s in signal_instances]}，{start} ~ {end}，频率={rebalance}"
    )

    loader = DataLoader()
    bt = Backtester(
        signals=signal_instances,
        universe=ticker_list,
        loader=loader,
        entry_threshold=entry_threshold,
        exit_threshold=exit_threshold,
        stop_loss_pct=stop_loss,
        time_limit_days=time_limit,
        commission_pct=commission,
        slippage_pct=slippage,
        max_positions=max_positions,
    )

    report = bt.run(start=start, end=end, rebalance_freq=rebalance)
    summary = report.summary_dict()

    # 终端表格
    table = Table(title="回测摘要", show_lines=False)
    table.add_column("指标", style="bold cyan")
    table.add_column("值", justify="right")
    table.add_row("交易笔数（含未平仓）", f"{summary['n_trades']}（平仓 {summary['n_closed']}）")
    table.add_row("胜率", f"{summary['win_rate']*100:.1f}%")
    table.add_row("平均收益", f"{summary['avg_return']*100:.2f}%")
    table.add_row("总收益", f"{summary['total_return']*100:.2f}%")
    table.add_row("最大回撤", f"{summary['max_drawdown']*100:.2f}%")
    table.add_row("夏普（年化）", f"{summary['sharpe']:.2f}")
    pf = summary["profit_factor"]
    table.add_row("盈亏比", f"{pf:.2f}" if pf != float("inf") else "inf")
    console.print(table)

    # per-ticker 统计
    per_tk = report.per_ticker_stats()
    if not per_tk.empty:
        tk_table = Table(title="按股票统计", show_lines=False)
        tk_table.add_column("Ticker", style="bold cyan")
        tk_table.add_column("笔数", justify="right")
        tk_table.add_column("胜率", justify="right")
        tk_table.add_column("平均收益", justify="right")
        tk_table.add_column("累计收益", justify="right")
        for _, row in per_tk.iterrows():
            tk_table.add_row(
                row["ticker"],
                str(int(row["n_trades"])),
                f"{row['win_rate']*100:.1f}%",
                f"{row['avg_return']*100:.2f}%",
                f"{row['total_return']*100:.2f}%",
            )
        console.print(tk_table)

    # HTML 报告
    if not no_html:
        if output is None:
            stamp = datetime.now().strftime("%Y-%m-%d")
            output = Path("reports") / f"backtest-{stamp}.html"
        BacktestHTMLReporter().render(report, output)
        console.print(f"\n[bold green]HTML 报告已生成: {output}[/bold green]")
