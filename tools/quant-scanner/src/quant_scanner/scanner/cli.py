"""命令行接口"""
from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from quant_scanner.data.loader import DataLoader
from quant_scanner.reporter.html import HTMLReporter
from quant_scanner.scanner.engine import Scanner
from quant_scanner.signals.trend_template import TrendTemplateSignal
from quant_scanner.signals.vcp import VCPSignal
from quant_scanner.signals.pivot_point import PivotPointSignal
from quant_scanner.signals.sell_signals import BaseCountingSignal
from quant_scanner.signals.can_slim import CANSLIMSignal
from quant_scanner.signals.market_direction import MarketDirectionSignal
from quant_scanner.signals.new_high_supply import NewHighSupplySignal
from quant_scanner.signals.dow_phases import DowPhasesSignal
from quant_scanner.signals.support_resistance import SupportResistanceSignal

console = Console()

# 默认 watchlist（用户当前关注）
DEFAULT_WATCHLIST = ["TSLA", "NVDA", "AAPL", "BABA", "PDD", "TME", "QCOM", "META", "MSFT", "GOOGL"]


def setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def load_watchlist(path: Path | None) -> list[str]:
    if path is None:
        return DEFAULT_WATCHLIST
    text = path.read_text(encoding="utf-8")
    return [line.strip().upper() for line in text.splitlines() if line.strip() and not line.startswith("#")]


@click.group()
def main() -> None:
    """quant-scanner: 美股短线/中线量化扫描器"""


@main.command()
@click.argument("tickers", nargs=-1)
@click.option("--watchlist", type=click.Path(exists=True, path_type=Path), help="watchlist 文件路径（每行一个 ticker）")
@click.option("--output", "-o", type=click.Path(path_type=Path), help="HTML 报告输出路径")
@click.option("--period", default="2y", help="拉取历史数据周期")
@click.option("--no-html", is_flag=True, help="不生成 HTML 报告")
@click.option("--verbose", "-v", is_flag=True, help="详细日志")
@click.option("--workers", "-w", type=int, default=4, help="并发 worker 数（IO bound，建议 4-8，默认 4）")
def scan(
    tickers: tuple[str, ...],
    watchlist: Path | None,
    output: Path | None,
    period: str,
    no_html: bool,
    verbose: bool,
    workers: int,
) -> None:
    """扫描一组 ticker，输出信号打分"""
    setup_logging(verbose)

    if watchlist:
        ticker_list = load_watchlist(watchlist)
    elif tickers:
        ticker_list = list(tickers)
    else:
        ticker_list = DEFAULT_WATCHLIST
        console.print(f"[yellow]未指定 ticker，使用默认 watchlist: {ticker_list}[/yellow]")

    console.print(f"[bold green]开始扫描 {len(ticker_list)} 只股票（workers={workers}）...[/bold green]")

    signals = [
        TrendTemplateSignal(),
        VCPSignal(),
        PivotPointSignal(),
        BaseCountingSignal(),
        CANSLIMSignal(),
        MarketDirectionSignal(),
        NewHighSupplySignal(),
        DowPhasesSignal(),
        SupportResistanceSignal(),
    ]
    scanner = Scanner(signals=signals)
    report = scanner.scan(ticker_list, period=period, max_workers=workers)

    # ---- 终端表格输出（动态列）----
    table = Table(title="扫描结果", show_lines=True)
    table.add_column("Ticker", style="bold cyan")
    # 按信号名生成列
    signal_names = [s.name for s in signals] if report.results else []
    for sn in signal_names:
        table.add_column(sn, justify="right")
    table.add_column("综合", justify="right", style="bold")
    table.add_column("通过", justify="center")

    for r in sorted(report.results, key=lambda x: -x["composite_score"]):
        sig_map = {s.signal_name: s for s in r["signals"]}
        cells = []
        for sn in signal_names:
            sr = sig_map.get(sn)
            if sr:
                mark = "[green]✓[/green]" if sr.passed else "[red]✗[/red]"
                cells.append(f"{sr.value:.2f}{mark}")
            else:
                cells.append("-")
        table.add_row(
            r["ticker"],
            *cells,
            f"{r['composite_score']:.2f}",
            "[green]✓[/green]" if r["passed"] else "[red]✗[/red]",
        )

    console.print(table)

    # ---- HTML 报告 ----
    if not no_html:
        reporter = HTMLReporter()
        if output is None:
            stamp = datetime.now().strftime("%Y-%m-%d")
            output = reporter.default_output_path(stamp)
        reporter.render(report, output)
        console.print(f"\n[bold green]HTML 报告已生成: {output}[/bold green]")


@main.command()
@click.argument("ticker")
@click.option("--period", default="2y", help="历史数据周期（默认 2 年）")
@click.option("--account", type=float, default=None,
              help="账户权益 USD，传入则给出具体股数和仓位建议")
@click.option("--html", type=click.Path(path_type=Path), default=None,
              help="输出 HTML 报告路径（可选）")
def analyze(ticker: str, period: str, account: float | None, html: Path | None) -> None:
    """一键分析：输入股票代码，自动跑完整流水线，用人话输出结论

    \b
    自动完成：
    1. 9 个技术信号扫描（趋势/VCP/CANSLIM/支撑阻力/道氏阶段等）
    2. Alpha101 因子历史预测力（找当前最有效的因子）
    3. 机器学习 Meta-Labeling（历史类似情况 primary 命中率 → ML 过滤后提升多少）
    4. 操作建议（方向/止损/目标/仓位，基于 2% 风控）

    \b
    用法：
      scanner analyze NVDA             # 最简：只给代码
      scanner analyze NVDA --account 100000   # 传入账户给仓位建议
    """
    setup_logging(False)
    ticker = ticker.upper()
    console.print(f"\n[bold cyan]═══ {ticker} 一键分析 ═══[/bold cyan]")
    console.print(f"[dim]数据周期 {period} · 跑完整流水线（约 30 秒）[/dim]\n")

    loader = DataLoader()
    df = loader.load(ticker, period=period)
    if df.empty:
        console.print(f"[red]无法获取 {ticker} 数据[/red]")
        sys.exit(1)

    last_close = float(df["close"].iloc[-1])
    last_date = df.index[-1]
    if hasattr(last_date, "date"):
        last_date = last_date.date()

    # ---- 1. 技术信号扫描（快速模式：CANSLIM 禁用 SP500 RS 注入，避免拉全市场） ----
    console.print("[dim]▸ 扫描 9 个技术信号...[/dim]")
    try:
        can_slim_fast = CANSLIMSignal(auto_inject_rs=False)
    except TypeError:
        can_slim_fast = CANSLIMSignal()
    signals = [
        TrendTemplateSignal(), VCPSignal(), PivotPointSignal(),
        BaseCountingSignal(), can_slim_fast, MarketDirectionSignal(),
        NewHighSupplySignal(), DowPhasesSignal(), SupportResistanceSignal(),
    ]
    sig_results = []
    for sig in signals:
        try:
            sr = sig.evaluate(ticker, df)
            sig_results.append((sig.name, sr))
        except Exception as e:
            sig_results.append((sig.name, None))
            console.print(f"[dim red]{sig.name} 失败: {e}[/dim red]")

    n_pass = sum(1 for _, sr in sig_results if sr and sr.passed)
    n_total = sum(1 for _, sr in sig_results if sr is not None)
    composite = sum(sr.value for _, sr in sig_results if sr) / max(n_total, 1)

    # ---- 2. Alpha101 因子预测力（top 3） ----
    console.print("[dim]▸ 跑 Alpha101 因子历史预测力（找当前最有效的因子）...[/dim]")
    from quant_scanner.factors.alpha101 import Alpha101
    from quant_scanner.factors.operators import factor_ic, log_returns
    alpha_engine = Alpha101()
    all_alphas = alpha_engine.compute_all(df)
    fwd = log_returns(df["close"]).shift(-5)
    ic_scores = {n: factor_ic(s, fwd) for n, s in all_alphas.items()}
    sorted_alphas = sorted(
        [(n, v) for n, v in ic_scores.items() if v == v],
        key=lambda x: -abs(x[1])
    )[:5]

    # ---- 3. 机器学习 Meta-Labeling ----
    console.print("[dim]▸ 跑 ML Meta-Labeling（历史类似情况的胜率 + ML 过滤后提升）...[/dim]")
    ml_summary = None
    ml_error = None
    try:
        from quant_scanner.ml import run_meta_labeling_pipeline, pipeline_summary
        from quant_scanner.ml.secondary_model import SecondaryModel
        from sklearn.ensemble import GradientBoostingClassifier

        # 自动选 primary alpha：选 IC 绝对值最大的那个（排除常数/NaN）
        primary_name = sorted_alphas[0][0] if sorted_alphas else "alpha_42"
        primary = all_alphas.get(primary_name, all_alphas.get("alpha_42"))

        # 自动选 top 10 features（按 |IC|，排除 primary）
        keep = [n for n, _ in sorted_alphas if n != primary_name][:10]
        features = {k: v for k, v in all_alphas.items() if k in keep}

        clf = GradientBoostingClassifier(n_estimators=100, max_depth=3, random_state=42)
        result = run_meta_labeling_pipeline(
            df, primary_alpha=primary, feature_alphas=features,
            tp_atr_mult=2.0, sl_atr_mult=2.0, vertical_barrier_bars=10,
            test_size=0.3, clf=clf,
        )
        ml_summary = pipeline_summary(result)
        ml_summary["_primary_name"] = primary_name
        ml_summary["_result"] = result
    except Exception as e:
        ml_error = str(e)

    # ---- 4. 止损/目标价（新入场视角：当前价入场，止损 = entry - 3×ATR） ----
    console.print("[dim]▸ 计算止损/目标价/仓位建议...[/dim]")
    import pandas as _pd

    _high = df["high"]
    _low = df["low"]
    _close = df["close"]
    _prev_close = _close.shift(1)
    _tr = _pd.concat(
        [_high - _low,
         (_high - _prev_close).abs(),
         (_low - _prev_close).abs()],
        axis=1,
    ).max(axis=1)
    _atr = float(_tr.ewm(alpha=1.0 / 14, adjust=False, min_periods=14).mean().iloc[-1])

    entry = last_close
    initial_stop = entry - 3.0 * _atr  # 3×ATR 宽止损（回测最优）
    risk_per_share = max(entry - initial_stop, 1e-9)
    target = entry + 3.0 * risk_per_share  # 3:1 风险比
    exit_details = {
        "entry_price": entry,
        "current_stop": initial_stop,
        "target": target,
        "atr": _atr,
    }

    # ---- 输出结论 ----
    console.print(f"\n[bold]{'═' * 60}[/bold]")
    console.print(f"[bold]结论（{ticker}，数据截至 {last_date}，收盘 ${last_close:.2f}）[/bold]")
    console.print(f"[bold]{'═' * 60}[/bold]\n")

    # 一句话总结
    trend_sig = next((sr for n, sr in sig_results if n == "trend_template" and sr), None)
    regime_word = ""
    if trend_sig:
        if trend_sig.passed:
            regime_word = "处于趋势第二阶段（上涨趋势）"
        else:
            regime_word = "未进入明确上涨趋势"
    else:
        regime_word = "趋势状态不明"

    # ML 信心
    ml_sentence = ""
    if ml_summary:
        pp = ml_summary["primary_precision"]
        sp = ml_summary["secondary_oos_precision"]
        lift = ml_summary["lift"]
        pname = ml_summary.get("_primary_name", "alpha_42")

        def _fmt_pct(v):
            return f"{v*100:.1f}%" if v == v else "NaN"

        if sp == sp and lift == lift and lift > 0:
            ml_sentence = (
                f"过去 2 年，因子 {pname} 的历史命中率 {_fmt_pct(pp)}，"
                f"机器学习过滤后可提升到 [bold green]{_fmt_pct(sp)}[/bold green]"
                f"（+{lift*100:.1f}个百分点）"
            )
        elif sp == sp:
            ml_sentence = (
                f"因子 {pname} 的历史命中率 {_fmt_pct(pp)}，"
                f"ML 过滤后 {_fmt_pct(sp)}（提升不明显，可能因样本不足或信号弱）"
            )
        else:
            ml_sentence = f"因子 {pname} 的历史命中率 {_fmt_pct(pp)}（ML 评估样本不足）"
    elif ml_error:
        ml_sentence = f"[dim]ML 评估未完成（{ml_error}）[/dim]"
    else:
        ml_sentence = ""

    # 操作建议
    action_word = "观望"
    if n_pass >= 5 and composite >= 0.5:
        action_word = "[bold green]可以考虑小仓位跟进[/bold green]"
    elif n_pass >= 3:
        action_word = "[yellow]中性偏多，等突破再介入[/yellow]"
    elif n_pass <= 2:
        action_word = "[red]技术面偏弱，不建议介入[/red]"

    console.print(f"[bold]一句话结论[/bold]：")
    console.print(
        f"  {ticker} {regime_word}，{n_pass}/{n_total} 个技术信号看涨（综合得分 {composite:.2f}）。"
    )
    if ml_sentence:
        console.print(f"  {ml_sentence}")
    console.print(f"  → 当前建议：{action_word}\n")

    # 技术面状态
    console.print(f"[bold]① 技术面状态[/bold]")
    sig_map = {n: sr for n, sr in sig_results}
    for name in ["trend_template", "vcp", "can_slim", "market_direction",
                 "dow_phases", "support_resistance", "new_high_supply",
                 "pivot_point", "sell_signals"]:
        sr = sig_map.get(name)
        if not sr:
            continue
        mark = "[green]✓[/green]" if sr.passed else "[red]✗[/red]"
        # 提取一句关键 reason
        key_reason = sr.reasons[0] if sr.reasons else ""
        console.print(f"  {mark} {name:20s} {sr.value:.2f}  [dim]{key_reason}[/dim]")
    console.print()

    # 关键价位
    console.print(f"[bold]② 关键价位[/bold]")
    sup_res = sig_map.get("support_resistance")
    if sup_res and sup_res.details:
        ns = sup_res.details.get("nearest_support")
        nr = sup_res.details.get("nearest_resistance")
        if ns:
            console.print(f"  最近支撑: [green]${ns:.2f}[/green]（距当前 {(ns/last_close-1)*100:+.1f}%）")
        if nr:
            console.print(f"  最近阻力: [red]${nr:.2f}[/red]（距当前 {(nr/last_close-1)*100:+.1f}%）")
    # 止损/目标
    stop = exit_details.get("current_stop")
    target = exit_details.get("target")
    entry = exit_details.get("entry_price", last_close)
    if stop and target:
        risk_pct = (last_close - stop) / last_close * 100
        reward_pct = (target - last_close) / last_close * 100
        payoff = (target - last_close) / max(last_close - stop, 1e-9)
        console.print(
            f"  建议入场: [cyan]${entry:.2f}[/cyan] | "
            f"止损: [red]${stop:.2f}[/red]（-{risk_pct:.1f}%） | "
            f"目标: [green]${target:.2f}[/green]（+{reward_pct:.1f}%） | "
            f"盈亏比 [bold]{payoff:.1f}:1[/bold]"
        )
    console.print()

    # 因子Top
    if sorted_alphas:
        console.print(f"[bold]③ 当前最有效的历史因子（前 5）[/bold]")
        for name, ic in sorted_alphas[:5]:
            direction = "看多" if ic > 0 else "看空"
            strength = "强" if abs(ic) >= 0.08 else ("有效" if abs(ic) >= 0.05 else ("弱" if abs(ic) >= 0.03 else "噪声"))
            color = "green" if abs(ic) >= 0.05 else ("yellow" if abs(ic) >= 0.03 else "dim")
            console.print(
                f"  [{color}]{name}[/{color}] IC={ic:+.4f}（{strength}，{direction}）"
            )
        console.print()

    # 仓位建议
    if account and stop and target:
        risk_per_share = last_close - stop
        if risk_per_share > 0:
            # 2% 风控
            risk_amount = account * 0.02
            shares = int(risk_amount / risk_per_share)
            position_value = shares * last_close
            pct = position_value / account * 100
            console.print(f"[bold]④ 仓位建议（账户 ${account:,.0f}，2% 风控）[/bold]")
            console.print(
                f"  建议买入: [bold cyan]{shares} 股[/bold cyan] ≈ ${position_value:,.0f}"
                f"（占账户 {pct:.1f}%）"
            )
            console.print(
                f"  单笔最大亏损: ${risk_amount:,.0f}（账户的 2%）"
            )
            console.print(f"  [dim]盈亏比 ≥ 2:1 才值得入场，当前 {payoff:.1f}:1[/dim]\n")

    # 风险提示
    console.print(f"[bold]⚠️ 风险提示[/bold]")
    market_sr = sig_map.get("market_direction")
    if market_sr and not market_sr.passed:
        console.print("  [red]大盘方向偏弱（M 字母 fail），个股信号要打折看[/red]")
    if ml_summary and ml_summary.get("lift") is not None:
        lift = ml_summary["lift"]
        if lift != lift or lift < 0:
            console.print("  [yellow]ML 过滤后无提升或负提升，历史信号不可靠，建议等更明确形态[/yellow]")
    if composite < 0.4:
        console.print("  [yellow]综合得分偏低，技术面不够强，建议观望[/yellow]")
    if stop and (last_close - stop) / last_close > 0.15:
        console.print("  [yellow]止损距离 >15%，波动较大，仓位可适当减小[/yellow]")
    if not (stop and target):
        console.print("  [dim]数据不足无法计算止损/目标，建议手动确认入场点[/dim]")

    console.print(f"\n[dim]参考：趋势回测 60d 胜率 60.3%/盈亏比 2.12；ML 基于 López de Prado AfML Ch 4[/dim]")
    if html:
        console.print(f"\n[dim]HTML 报告暂不支持 analyze 命令，后续版本加入[/dim]")


@main.command()
@click.argument("ticker")
@click.option("--period", default="2y")
def detail(ticker: str, period: str) -> None:
    """查看单只股票详细信号"""
    setup_logging(False)
    ticker = ticker.upper()

    console.print(f"[bold cyan]{ticker} 详细分析[/bold cyan]")
    loader = DataLoader()
    df = loader.load(ticker, period=period)
    if df.empty:
        console.print(f"[red]无法获取 {ticker} 数据[/red]")
        sys.exit(1)

    signals = [TrendTemplateSignal(), VCPSignal(), CANSLIMSignal()]
    for sig in signals:
        sr = sig.evaluate(ticker, df)
        style = "green" if sr.passed else "red"
        console.print(f"\n[bold {style}]{sig.name}: {sr.value:.2f} {'✓' if sr.passed else '✗'}[/bold {style}]")
        for reason in sr.reasons:
            console.print(f"  • {reason}")
        if sr.details:
            for k, v in sr.details.items():
                if not isinstance(v, (list, dict)):
                    console.print(f"  [dim]{k}: {v}[/dim]")


@main.command()
@click.argument("ticker")
@click.option("--period", default="2y", help="历史数据周期")
def indicators(ticker: str, period: str) -> None:
    """全套技术指标（~22 个，含成交量维度 OBV/MFI/VWAP/布林/KDJ/ADX 等）"""
    setup_logging(False)
    ticker = ticker.upper()
    console.print(f"\n[bold cyan]{ticker} 技术指标（~22 个）[/bold cyan]")
    loader = DataLoader()
    df = loader.load(ticker, period=period)
    if df.empty:
        console.print(f"[red]无法获取 {ticker} 数据[/red]")
        sys.exit(1)
    from quant_scanner.features.indicators import compute_all
    table = compute_all(df)
    cat_names = {"price_momentum": "📊 价格动量", "volume": "📦 成交量",
                 "trend": "📈 趋势", "volatility": "🌀 波动", "divergence": "⚠️ 量价背离"}
    for cat, inds in table.items():
        console.print(f"\n[bold]{cat_names.get(cat, cat)}[/bold]")
        for i in inds:
            v = i["value"]
            if isinstance(v, dict):
                v = "  ".join(f"{k}={val:.2f}" if isinstance(val, float) else f"{k}={val}"
                              for k, val in v.items() if val is not None)
            elif isinstance(v, float):
                v = f"{v:.2f}"
            console.print(f"  [cyan]{i['name']}[/cyan]: {v}  [yellow]{i['signal']}[/yellow]")
            if i.get("detail"):
                console.print(f"    [dim]{i['detail']}[/dim]")


@main.command()
def sector() -> None:
    """板块相对强度 + 轮动 + 启动信号（找板块机会）"""
    setup_logging(False)
    from quant_scanner.features.sector import compute_sector_rs, sector_summary, compute_market_regime
    # 当前市场 regime（仅状态描述；10y 回测证明 launch 信号在所有 regime 下均无预测力，不作交易断言）
    _spy = DataLoader().load("SPY", period="1y")
    reg = compute_market_regime(_spy)
    reg_style = {"bull_strong": "green", "bull_correction": "yellow",
                 "bear": "red", "warmup": "dim"}.get(reg["regime"], "white")
    _fmt = lambda v: f"{v:.2f}" if isinstance(v, (int, float)) else "—"
    console.print(f"\n[bold]市场 regime: [{reg_style}]{reg['regime']}[/bold] | "
                  f"SPY {_fmt(reg.get('close'))} (MA50 {_fmt(reg.get('ma50'))} / MA200 {_fmt(reg.get('ma200'))})")
    if reg.get("warning"):
        console.print(f"[yellow]{reg['warning']}[/yellow]")
    console.print("\n[bold cyan]板块相对强度排名（vs SPY，按 RS 3 月变化降序）[/bold cyan]")
    ranking = compute_sector_rs()
    if not ranking:
        console.print("[red]无数据（检查网络/缓存）[/red]")
        return
    table = Table(show_lines=False)
    for col in ["ETF", "板块", "RS", "RS1月%", "RS3月%", "趋势", "ETF1月%", "信号"]:
        table.add_column(col)
    for r in ranking:
        sig = "🔥启动" if r["launch_signal"] else ("⚡突破" if r["breakout_20d"] else ("放量" if r["volume_surge"] else ""))
        style = "green" if r["rs_3m_chg"] > 2 else ("red" if r["rs_3m_chg"] < -2 else "")
        table.add_row(r["etf"], r["name"], str(r["rs_ratio"]),
                      f"{r['rs_1m_chg']:+.1f}", f"{r['rs_3m_chg']:+.1f}",
                      r["trend"], f"{r['etf_chg_1m']:+.1f}", sig, style=style)
    console.print(table)
    s = sector_summary(ranking)
    if s.get("launching"):
        console.print(f"\n[bold green]🔥 启动板块（突破+放量+RS升）: "
                      + ", ".join(r["etf"] + "(" + r["name"] + ")" for r in s["launching"]) + "[/bold green]")
    if s.get("rotating_in"):
        console.print("[green]↗ 资金流入（RS 走强）: "
                      + ", ".join(r["etf"] for r in s["rotating_in"]) + "[/green]")
    if s.get("rotating_out"):
        console.print("[red]↘ 资金流出（RS 走弱）: "
                      + ", ".join(r["etf"] for r in s["rotating_out"]) + "[/red]")


@main.command()
@click.argument("strategy", type=click.Choice(["breakout", "trend", "momentum"]))
@click.option("--watchlist", type=click.Path(exists=True, path_type=Path),
              help="watchlist 文件（每行一个 ticker），默认内置热门池")
@click.option("--period", default="6mo", help="历史数据周期（yfinance 格式：6mo/1y/2y...，注意是 mo 不是 m）")
@click.option("--plan", is_flag=True,
              help="（trend 专用）输出可执行交易计划：entry/stop/target/持有期，锚定 trend_template@60d 回测（胜率60.3%/盈亏比1.98）")
@click.option("--account", type=float, default=None, help="账户权益 USD，配合 --plan 按 2% 风控算股数")
def screen(strategy: str, watchlist: Path | None, period: str, plan: bool, account: float | None) -> None:
    """选股扫描：breakout(突破+放量) / trend(趋势第二阶段) / momentum(短线动量)"""
    setup_logging(False)
    from quant_scanner.features import screener as scr
    tickers = scr.DEFAULT_WATCHLIST
    if watchlist:
        tickers = [l.strip().upper() for l in watchlist.read_text().splitlines()
                   if l.strip() and not l.startswith("#")]
    console.print(f"\n[bold cyan]选股扫描：{strategy}（池 {len(tickers)} 只）[/bold cyan]")

    # trend + --plan：中线交易计划模式（基于 trend_template@60d 回测锚点）
    if strategy == "trend" and plan:
        hits = scr.screen_trend_plan(tickers, period=period, account=account)
        if not hits:
            console.print("[yellow]无符合条件的股票[/yellow]")
            return
        console.print(f"[green]命中 {len(hits)} 只 · 中线趋势计划（持有 60 交易日，锚定 trend@60d 胜率 60.3%/盈亏比 1.98）[/green]")
        for h in hits:
            console.print(
                f"\n[bold cyan]{h['ticker']}[/bold cyan] "
                f"entry=${h['close']} | stop=${h['stop']} (风险 {h['risk_pct']}%) | "
                f"target=${h['target']} (收益 {h['reward_pct']}%) | "
                f"盈亏比 {h['payoff']} | 回测EV {h['backtest_ev_hold']}%/60d(hold) | ATR {h['atr']}"
            )
            if account:
                console.print(
                    f"  [dim]账户 ${account:,.0f} × 2% 风控 → {h['shares']} 股 ≈ "
                    f"${h['position_value']:,.0f} 市值（单笔风险 ${h['account_risk']:,.0f}）[/dim]"
                )
        console.print(f"[dim]止损 = entry - 4×ATR（宽止损给趋势空间）；目标 = 3:1；期望基于 60.3% 胜率估算[/dim]")
        console.print(f"[yellow]⚠️ 组合回测结论（SP500×5y）：纯持有 60d 最优 EV+4.21%/盈亏比 2.12；2×ATR 紧止损有害（60% 被洗出，EV 仅 +0.34%）。止损作风控上限，别紧设[/yellow]")
        return

    fn = {"breakout": scr.screen_breakout, "trend": scr.screen_trend,
          "momentum": scr.screen_momentum}[strategy]
    hits = fn(tickers, period=period) if strategy != "trend" else fn(tickers)
    if not hits:
        console.print("[yellow]无符合条件的股票[/yellow]")
        return
    console.print(f"[green]命中 {len(hits)} 只：[/green]")
    for h in hits:
        console.print(f"  [bold]{h['ticker']}[/bold] {h}")


@main.command()
def market() -> None:
    """大盘背景：趋势(SPY/QQQ/IWM vs MA200) + VIX + 风格轮动 + risk-on/off"""
    setup_logging(False)
    from quant_scanner.features.market import market_regime
    console.print("\n[bold cyan]大盘背景 + 风格轮动[/bold cyan]")
    state = market_regime()
    if not state.get("trend"):
        console.print("[red]无数据[/red]")
        return
    console.print(f"\n[bold yellow]市场状态: {state['regime']}[/bold yellow]")
    vix = state.get("vix")
    if vix:
        console.print(f"VIX: {vix['value']} ({vix['level']}, 5日 {vix['chg_5d']:+.1f}%)")
    console.print(f"\n[bold]大盘趋势:[/bold]")
    for t in state["trend"]:
        console.print(f"  {t['symbol']:4} {t['name']:16} {t['price']:8.2f} | MA50={t['ma50']:.2f} MA200={t['ma200']:.2f} | "
                      f"{t['bull_bear']} {t['alignment']} (距MA200 {t['dist_ma200']:+.1f}%)")
    console.print(f"\n[bold]风格轮动:[/bold]")
    for s in state["style"]:
        arrow = "↑" if s["chg_1m"] > 0 else "↓"
        console.print(f"  {s['pair']:24} {s['a']}/{s['b']}={s['ratio']:.3f} | "
                      f"1月 {s['chg_1m']:+5.1f}% 3月 {s['chg_3m']:+5.1f}% | {arrow} {s['leading']} 占优")
    # 市场宽度
    from quant_scanner.features.market import market_breadth
    breadth = market_breadth()
    if breadth:
        console.print(f"\n[bold]市场宽度（{breadth['universe_size']} 只样本）:[/bold]")
        console.print(f"  %above MA200: {breadth['pct_above_ma200']}% ({breadth['breadth_health']}) | "
                      f"%above MA50: {breadth['pct_above_ma50']}%")
        console.print(f"  新高/新低: {breadth['new_52w_highs']}/{breadth['new_52w_lows']} "
                      f"(比值 {breadth['high_low_ratio']}) | 涨/跌: {breadth['advancers']}/{breadth['decliners']} "
                      f"(比值 {breadth['ad_ratio']})")


# 注册 backtest 子命令（顶层 import 会导致循环，所以放在文件尾部）
from quant_scanner.backtest.cli import backtest as _backtest_cmd  # noqa: E402
from quant_scanner.stats.cli import stats as _stats_cmd  # noqa: E402
from quant_scanner.stats.cli import sector_stats as _sector_stats_cmd  # noqa: E402

main.add_command(_backtest_cmd)
main.add_command(_stats_cmd)
main.add_command(_sector_stats_cmd)


# ============================================================
# factors 子命令：算子库 + Alpha101 评估
# ============================================================

@main.command(name="factors")
@click.argument("action", type=click.Choice(["list", "eval", "matrix", "cs-eval", "regime-eval", "shap-eval", "gp-mine", "deflate", "purged-cv", "liquidity"]))
@click.option("--ticker", default="NVDA", help="标的（默认 NVDA，eval 模式必填）")
@click.option("--period", default="2y", help="历史数据周期")
@click.option("--alpha", type=int, default=None, help="只跑指定编号（如 --alpha 1）")
@click.option("--horizon", type=int, default=5, help="前瞻收益天数（IC 评估）")
@click.option("--horizons", default="1,5,10,20,60",
              help="多周期 IC 矩阵的 horizon 列表，逗号分隔（matrix 模式专用，默认 1,5,10,20,60）")
@click.option("--html", type=click.Path(path_type=Path), default=None,
              help="HTML 报告输出路径（不传则只打印表格）")
@click.option("--tickers", default=None,
              help="cs-eval 模式专用：多标的列表，逗号分隔（如 NVDA,AAPL,MSFT）")
def factors(action: str, ticker: str, period: str, alpha: int | None,
            horizon: int, horizons: str, html: Path | None,
            tickers: str | None) -> None:
    """因子库：list / eval / matrix / cs-eval / regime-eval / shap-eval / gp-mine / deflate（Harvey haircut）/ purged-cv（OOS 评估）/ liquidity（Amihud+CS Spread）"""
    setup_logging(False)
    from quant_scanner.factors.alpha101 import Alpha101
    from quant_scanner.factors.operators import factor_ic, factor_ir, log_returns

    a = Alpha101()
    names = a.list_alpha_names()

    if action == "list":
        console.print(f"\n[bold cyan]Alpha101 已实现 {len(names)} 个 alpha[/bold cyan]")
        console.print("  " + ", ".join(names))
        console.print("\n[dim]用法：factors eval --ticker NVDA --horizon 5 --html out.html[/dim]")
        console.print("[dim]     factors matrix --ticker NVDA（多周期矩阵）[/dim]")
        console.print("[dim]     factors cs-eval --tickers NVDA,AAPL,MSFT,GOOGL,META（横截面多标的）[/dim]")
        return

    if action == "cs-eval":
        # 横截面多标的因子 IC 评估（任务 #88）
        from quant_scanner.factors.alpha101 import (
            Alpha101CrossSectional, build_forward_returns_panel,
        )
        if not tickers:
            console.print("[red]cs-eval 模式需要 --tickers 参数（如 --tickers NVDA,AAPL,MSFT）[/red]")
            return
        ticker_list = [t.strip().upper() for t in tickers.split(",") if t.strip()]
        if len(ticker_list) < 3:
            console.print("[yellow]⚠️ 横截面 IC 至少需要 3 只标的，结果可能不可靠[/yellow]")

        console.print(f"\n[bold cyan]Alpha101 横截面评估（{len(ticker_list)} 只标的）[/bold cyan]")
        console.print(f"[dim]标的: {', '.join(ticker_list)} | 周期: {period} | horizon: {horizon}d[/dim]\n")

        # 加载 panel
        loader = DataLoader()
        panel = {}
        for t in ticker_list:
            try:
                df = loader.load(t, period=period)
                if not df.empty:
                    panel[t] = df
            except Exception as e:
                console.print(f"[red]加载 {t} 失败: {e}[/red]")
        if len(panel) < 3:
            console.print(f"[red]有效标的不足 3 只，无法计算横截面 IC[/red]")
            return

        cs = Alpha101CrossSectional()
        all_factors = cs.compute_all(panel)
        fwd = build_forward_returns_panel(panel, horizon=horizon)

        # 对每个 alpha 算横截面 IC + IR
        import numpy as _np
        rows = []
        for name in sorted(all_factors.keys()):
            factor_panel = all_factors[name]
            mean_ic, ic_series = cs.cross_sectional_ic(factor_panel, fwd)
            ir = cs.cross_sectional_ir(ic_series)
            rows.append({
                "alpha": name,
                "IC": mean_ic,
                "IR": ir,
                "N_days": len(ic_series),
            })

        # 按 |IC| 降序
        rows.sort(key=lambda r: -abs(r["IC"]) if not _np.isnan(r["IC"]) else 0)

        # 表格输出
        table = Table(title=f"Cross-sectional Alpha IC（horizon={horizon}d, N={len(panel)} tickers）")
        table.add_column("Alpha", style="cyan")
        table.add_column("IC", justify="right")
        table.add_column("IR", justify="right")
        table.add_column("N days", justify="right")
        table.add_column("强度", style="bold")

        for r in rows:
            ic = r["IC"]
            ir = r["IR"]
            if _np.isnan(ic):
                strength = "[dim]N/A[/dim]"
                ic_str = "NaN"
                ir_str = "NaN"
            elif abs(ic) >= 0.08:
                strength = "[bold green]强[/bold green]"
                ic_str = f"[bold green]{ic:+.4f}[/bold green]"
                ir_str = f"{ir:+.2f}"
            elif abs(ic) >= 0.05:
                strength = "[green]有效[/green]"
                ic_str = f"[green]{ic:+.4f}[/green]"
                ir_str = f"{ir:+.2f}"
            elif abs(ic) >= 0.03:
                strength = "[yellow]弱[/yellow]"
                ic_str = f"{ic:+.4f}"
                ir_str = f"{ir:+.2f}"
            else:
                strength = "[dim]噪声[/dim]"
                ic_str = f"{ic:+.4f}"
                ir_str = f"{ir:+.2f}"
            table.add_row(r["alpha"], ic_str, ir_str, str(r["N_days"]), strength)

        console.print(table)
        console.print(f"\n[dim]横截面 IC = 每天对 {len(panel)} 只标的做 rank corr 再取均值[/dim]")
        console.print(f"[dim]学术标准：|IC|≥0.03 有信号 / ≥0.05 有效 / ≥0.08 强信号[/dim]")
        return

    # eval / matrix 共享数据加载
    ticker = ticker.upper()
    loader = DataLoader()
    df = loader.load(ticker, period=period)
    if df.empty:
        console.print(f"[red]无法获取 {ticker} 数据[/red]")
        sys.exit(1)

    results = a.compute_all(df)

    if action == "regime-eval":
        # 按 regime 分段算条件 IC（任务 #85）
        from quant_scanner.factors.operators import classify_regime, regime_conditional_ic
        import numpy as _np

        # 前瞻收益
        fwd = log_returns(df["close"]).shift(-horizon)

        # 用 SPY 作 regime benchmark（如果失败退化为标的本身）
        regime_df = None
        for bench in ["SPY", "QQQ", "^GSPC"]:
            try:
                regime_df = loader.load(bench, period=period)
                if not regime_df.empty:
                    break
            except Exception:
                continue
        if regime_df is None or regime_df.empty:
            console.print("[yellow]⚠️ 无法加载 SPY/QQQ，退化为标的价格分类 regime[/yellow]")
            regime_df = df

        regime = classify_regime(regime_df)
        counts = regime.value_counts().to_dict()
        console.print(f"\n[bold cyan]{ticker} · Regime 条件 IC（horizon={horizon}d）[/bold cyan]")
        console.print(f"[dim]Benchmark: {('SPY' if regime_df is not df else ticker)} | "
                      f"bull={counts.get('bull', 0)} / bear={counts.get('bear', 0)} / "
                      f"sideways={counts.get('sideways', 0)} days[/dim]\n")

        # 对每个 alpha 分 regime 算 IC
        rows = []
        for name in sorted(results.keys()):
            factor = results[name]
            regime_result = regime_conditional_ic(factor, fwd, regime, window=20)
            rows.append({
                "alpha": name,
                "bull_ic": regime_result["bull"][0],
                "bull_n": regime_result["bull"][1],
                "bear_ic": regime_result["bear"][0],
                "bear_n": regime_result["bear"][1],
                "sideways_ic": regime_result["sideways"][0],
                "sideways_n": regime_result["sideways"][1],
            })

        # 按 bull |IC| 降序（找牛市最有效因子）
        rows.sort(key=lambda r: -abs(r["bull_ic"]) if not _np.isnan(r["bull_ic"]) else 0)

        table = Table(title=f"Regime 条件 IC（{ticker}）")
        table.add_column("Alpha", style="cyan")
        table.add_column("Bull IC", justify="right")
        table.add_column("Bear IC", justify="right")
        table.add_column("Sideways IC", justify="right")
        table.add_column("自适应", style="bold")

        for r in rows:
            def fmt(ic, n):
                if _np.isnan(ic) or n == 0 or _np.isinf(ic):
                    return "[dim]N/A[/dim]"
                color = "green" if abs(ic) >= 0.05 else ("yellow" if abs(ic) >= 0.03 else "dim")
                return f"[{color}]{ic:+.3f}[/{color}]"

            bull_str = fmt(r["bull_ic"], r["bull_n"])
            bear_str = fmt(r["bear_ic"], r["bear_n"])
            side_str = fmt(r["sideways_ic"], r["sideways_n"])

            # 自适应判定：某 regime |IC|≥0.05 且其他 ≤0.03
            adaptiveness = "[dim]通用[/dim]"
            ics = [(abs(r["bull_ic"]) if not _np.isnan(r["bull_ic"]) and not _np.isinf(r["bull_ic"]) else 0, "bull"),
                   (abs(r["bear_ic"]) if not _np.isnan(r["bear_ic"]) and not _np.isinf(r["bear_ic"]) else 0, "bear"),
                   (abs(r["sideways_ic"]) if not _np.isnan(r["sideways_ic"]) and not _np.isinf(r["sideways_ic"]) else 0, "side")]
            ics.sort(key=lambda x: -x[0])
            if ics[0][0] >= 0.05 and ics[1][0] < 0.03:
                label_map = {"bull": "牛市专属", "bear": "熊市专属", "side": "震荡专属"}
                adaptiveness = f"[bold magenta]{label_map[ics[0][1]]}[/bold magenta]"
            elif ics[0][0] >= 0.05 and ics[1][0] >= 0.05:
                adaptiveness = "[green]跨 regime[/green]"

            table.add_row(r["alpha"], bull_str, bear_str, side_str, adaptiveness)

        console.print(table)
        console.print(f"\n[dim]自适应 = 该因子仅在特定 regime 有效（|IC|≥0.05 且其他 <0.03）[/dim]")
        console.print(f"[dim]跨 regime = 多个 regime 都有效（通用因子）[/dim]")
        return

    if action == "shap-eval":
        # SHAP 多因子联合贡献评估（任务 #86 方法 3 入口）
        from quant_scanner.factors.shap_eval import ShapFactorEvaluator
        import numpy as _np2

        fwd_shap = log_returns(df["close"]).shift(-horizon)

        evaluator = ShapFactorEvaluator()
        result = evaluator.evaluate(results, fwd_shap, top_n=10)

        if "error" in result:
            console.print(f"[red]{result['error']}[/red]")
            return

        console.print(f"\n[bold cyan]{ticker} · SHAP 特征重要性（horizon={horizon}d）[/bold cyan]")
        console.print(f"[dim]N samples={result['n_samples']} | "
                      f"Test R²={result['test_r2']:+.3f} "
                      f"({'泛化良好' if result['test_r2'] > 0.1 else '泛化偏弱' if result['test_r2'] > 0 else '无泛化'})[/dim]\n")

        table = Table(title=f"Top 10 SHAP 特征（{ticker}）")
        table.add_column("Alpha", style="cyan")
        table.add_column("mean |SHAP|", justify="right")
        table.add_column("方向", style="bold")
        table.add_column("解读", style="dim")

        for name, shap_val, direction in result["top_features"]:
            dir_color = "green" if direction == "positive" else "red" if direction == "negative" else "dim"
            dir_str = f"[{dir_color}]{direction}[/{dir_color}]"
            interp = {
                "positive": "因子值大 → 推高预测（正向）",
                "negative": "因子值大 → 压低预测（反向）",
                "neutral": "方向不明确",
            }[direction]
            table.add_row(name, f"{shap_val:.4f}", dir_str, interp)

        console.print(table)

        # 关键判断
        top_shap = result["top_features"][0][1] if result["top_features"] else 0
        avg_shap = sum(r[1] for r in result["top_features"][:5]) / 5
        console.print(f"\n[dim]解读：top SHAP = {top_shap:.4f}，top 5 均值 = {avg_shap:.4f}[/dim]")
        if result["test_r2"] > 0.15:
            console.print("[green]模型泛化良好，alpha 组合有预测力[/green]")
        elif result["test_r2"] > 0.05:
            console.print("[yellow]模型泛化偏弱，SHAP 结论仅供参考[/yellow]")
        else:
            console.print("[red]R² 接近 0，alpha 组合无预测力，SHAP 不可靠[/red]")
        return

    if action == "gp-mine":
        # GP 因子挖掘（任务 #87 方法 3 主菜）
        from quant_scanner.factors.gp_mining import GPFactorMiner

        fwd_gp = log_returns(df["close"]).shift(-horizon)
        features = ["close", "open", "high", "low", "volume"]
        available = [c for c in features if c in df.columns]

        miner = GPFactorMiner(
            population_size=50,
            generations=20,
            max_depth=4,
            random_state=42,
        )
        console.print(f"\n[bold cyan]{ticker} · GP 因子挖掘（pop=50, gen=20, horizon={horizon}d）[/bold cyan]")
        console.print(f"[dim]特征集: {', '.join(available)}[/dim]\n")
        result = miner.mine(df, fwd_gp, feature_cols=available, verbose=True)

        if "error" in result:
            console.print(f"[red]{result['error']}[/red]")
            return

        console.print(f"\n[bold green]最佳因子公式[/bold green]")
        console.print(f"  [cyan]{result['best_formula']}[/cyan]")
        console.print(f"\n[bold]IC[/bold] = {result['best_ic']:+.4f}  "
                      f"[bold]|IC|[/bold] = {result['best_abs_ic']:.4f}  "
                      f"[bold]IR[/bold] = {result['best_ir']:+.4f}  "
                      f"N={result['n_samples']}")

        if result['best_abs_ic'] > 0.08:
            console.print("[green]强信号因子（|IC|>0.08），建议入库用 SHAP 验证边际贡献[/green]")
        elif result['best_abs_ic'] > 0.05:
            console.print("[yellow]有效因子（|IC|>0.05），值得进一步验证[/yellow]")
        elif result['best_abs_ic'] > 0.03:
            console.print("[dim]弱信号（|IC| 0.03-0.05），参考价值有限[/dim]")
        else:
            console.print("[red]无有效信号（|IC|<0.03），未挖出可用因子[/red]")

        console.print(f"\n[dim]提示：用 shap-eval 验证此因子相对已有 alpha 的边际贡献[/dim]")
        return

    if action == "eval":
        console.print(f"\n[bold cyan]{ticker} · Alpha101 IC 评估（horizon={horizon}d）[/bold cyan]")
        fwd = log_returns(df["close"]).shift(-horizon)
        table = Table(show_lines=False, title=f"{ticker} Alpha101 IC 排序")
        for col in ["Alpha", "IC", "IR", "非NaN"]:
            table.add_column(col)
        rows = []
        for name, series in results.items():
            if alpha is not None and name != f"alpha_{alpha}":
                continue
            ic = factor_ic(series, fwd)
            ir = factor_ir(series, fwd, window=60)
            non_na = int(series.notna().sum())
            rows.append({"name": name, "ic": ic, "ir": ir, "non_na": non_na})
        rows.sort(key=lambda r: -abs(r["ic"] if r["ic"] == r["ic"] else 0))
        for r in rows:
            ic_str = f"{r['ic']:+.3f}" if r["ic"] == r["ic"] else "NaN"
            ir_str = f"{r['ir']:+.3f}" if r["ir"] == r["ir"] else "NaN"
            style = "green" if (r["ic"] == r["ic"] and abs(r["ic"]) > 0.03) else ""
            table.add_row(r["name"], ic_str, ir_str, str(r["non_na"]), style=style)
        console.print(table)
        console.print("[dim]学术标准：|IC| > 0.03 算有效，IR > 0.5 算高质量[/dim]")

        if html is not None:
            from quant_scanner.reporter.factors_html import render_alpha_factors
            render_alpha_factors(
                ticker=ticker, horizon=horizon, period=period,
                rows=rows, output=html,
            )
            console.print(f"[bold green]HTML 报告: {html}[/bold green]")
        return

    if action == "deflate":
        # Harvey haircut / Deflated IC 多重检验校正（任务 #90）
        from quant_scanner.factors.deflated_ic import deflated_ic_test, haircut_summary
        import numpy as _np_d

        console.print(f"\n[bold cyan]{ticker} · Deflated IC（Harvey haircut，horizon={horizon}d）[/bold cyan]")
        console.print(f"[dim]N trials = {len(names)} 个 alpha | 检验：在 N 次测试下，|IC| 最大的还显著吗[/dim]\n")
        fwd = log_returns(df["close"]).shift(-horizon)
        report = deflated_ic_test(results, fwd, n_trials=len(names), rolling_window=60)
        if len(report) == 0:
            console.print("[yellow]无有效因子可评估[/yellow]")
            return
        summary = haircut_summary(report)

        # 表格：Top 20 by |IC|
        table = Table(title=f"{ticker} Deflated IC Top（按 |IC| 排序）")
        for col in ["Alpha", "IC", "|IR|", "E[max IR]", "DIC stat", "DIC p", "通过"]:
            table.add_column(col)
        top = report.head(20)
        for _, row in top.iterrows():
            sig_str = "[green]✓[/green]" if row["significant"] else "[red]✗[/red]"
            pcolor = "green" if row["dic_pvalue"] < 0.05 else ("yellow" if row["dic_pvalue"] < 0.1 else "dim")
            table.add_row(
                row["alpha"],
                f"{row['ic_mean']:+.4f}",
                f"{row['ir']:.3f}",
                f"{row['expected_max_ir']:.3f}",
                f"{row['dic_statistic']:+.2f}",
                f"[{pcolor}]{row['dic_pvalue']:.4f}[/{pcolor}]",
                sig_str,
            )
        console.print(table)

        # 摘要
        console.print(f"\n[bold]Haircut 摘要[/bold]")
        console.print(f"  总因子数: [bold]{summary['total']}[/bold]")
        console.print(f"  DIC 前 (|IC|>0.03): [green]{summary['significant_before']}[/green]")
        console.print(f"  DIC 后 (p<0.05): [bold green]{summary['significant_after']}[/bold green]")
        rate_pct = summary['haircut_rate'] * 100
        console.print(f"  Haircut rate: [yellow]{rate_pct:.1f}%[/yellow]（被多重检验砍掉的比例）")
        if summary['top_survivors']:
            console.print(f"  [green]存活因子（DIC 通过）[/green]: {', '.join(summary['top_survivors'][:5])}")
        if summary['biggest_casualties']:
            console.print(f"  [red]重大伤亡（|IC|>0.05 但 DIC 不过）[/red]: {', '.join(summary['biggest_casualties'][:5])}")
        console.print(f"\n[dim]参考：Harvey-Liu-Zhu (2016) RFS | DIC p<0.05 = 真信号，≥0.05 = 伪信号候选[/dim]")
        return

    if action == "purged-cv":
        # Purged K-Fold CV + OOS Deflated IC（任务 #94，AfML Ch 7）
        from quant_scanner.eval import purged_cv_deflated_ic, oos_summary
        # default purge/embargo 对齐 horizon
        purge_bars = max(horizon, 5)
        embargo_bars = max(horizon // 2, 2)

        console.print(f"\n[bold cyan]{ticker} · Purged K-Fold CV（OOS 评估，AfML Ch 7）[/bold cyan]")
        console.print(f"[dim]horizon={horizon}d | purge={purge_bars}bars | embargo={embargo_bars}bars | folds=5 | N trials={len(names)}[/dim]\n")
        fwd = log_returns(df["close"]).shift(-horizon)
        report = purged_cv_deflated_ic(
            results, fwd, n_splits=5,
            purge_bars=purge_bars, embargo_bars=embargo_bars,
            n_trials=len(names),
        )
        if len(report) == 0:
            console.print("[yellow]无有效因子可评估[/yellow]")
            return
        summary = oos_summary(report)

        # 表格：Top 20 by |OOS IR|
        table = Table(title=f"{ticker} Purged K-Fold OOS IC Top（按 |OOS IR| 排序）")
        for col in ["Alpha", "OOS IC", "OOS IR", "E[max IR]", "DIC p", "通过"]:
            table.add_column(col)
        top = report.head(20)
        for _, row in top.iterrows():
            sig_str = "[green]✓[/green]" if row["significant"] else "[red]✗[/red]"
            pcolor = "green" if row["dic_pvalue"] < 0.05 else ("yellow" if row["dic_pvalue"] < 0.1 else "dim")
            ic = row["oos_ic_mean"]
            ir = row["oos_ir"]
            ic_str = f"{ic:+.4f}" if ic == ic else "NaN"
            ir_str = f"{ir:+.3f}" if ir == ir else "NaN"
            table.add_row(
                row["alpha"],
                ic_str,
                ir_str,
                f"{row['expected_max_ir']:.3f}",
                f"[{pcolor}]{row['dic_pvalue']:.4f}[/{pcolor}]",
                sig_str,
            )
        console.print(table)

        # 摘要
        console.print(f"\n[bold]OOS 评估摘要[/bold]")
        console.print(f"  总因子数: [bold]{summary['total_alphas']}[/bold]")
        console.print(f"  OOS 有效 (|IC|>0.03): [green]{summary['oos_effective']}[/green]")
        console.print(f"  DIC 通过 (p<0.05): [bold green]{summary['dic_survivors']}[/bold green]")
        rate_pct = summary['oos_haircut_rate'] * 100
        console.print(f"  OOS haircut rate: [yellow]{rate_pct:.1f}%[/yellow]（OOS 仍被 DIC 砍掉的比例）")
        console.print(f"  OOS |IR| 中位数: {summary['median_oos_ir']:.3f}")
        if summary['top_survivors']:
            console.print(f"  [green]存活（OOS + DIC 双重通过）[/green]: {', '.join(summary['top_survivors'][:5])}")
        if summary['biggest_casualties']:
            console.print(f"  [red]重大伤亡（OOS IC 大但 DIC 不过）[/red]: {', '.join(summary['biggest_casualties'][:5])}")
        console.print(f"\n[dim]参考：López de Prado AfML Ch 7 | Purged K-Fold + Harvey haircut 双重过滤[/dim]")
        return

    if action == "liquidity":
        # 流动性因子（Amihud ILLIQ + Corwin-Schultz Spread，任务 #98）
        import numpy as _np
        from quant_scanner.factors.liquidity import (
            amihud_illiq, amihud_illiq_log, amihud_implied_turnover,
            corwin_schultz_spread_bps, liquidity_composite,
        )

        console.print(f"\n[bold cyan]{ticker} · 流动性因子（Amihud 2002 + Corwin-Schultz 2012）[/bold cyan]")
        console.print(f"[dim]Amihud ILLIQ = |日收益|/成交额 | CS Spread = 日内 high-low 反推买卖价差[/dim]\n")

        illiq = amihud_illiq(close=df["close"], volume=df["volume"], window=21)
        illiq_log = amihud_illiq_log(close=df["close"], volume=df["volume"], window=21)
        turnover = amihud_implied_turnover(close=df["close"], volume=df["volume"], window=21)
        spread = corwin_schultz_spread_bps(high=df["high"], low=df["low"], window=21)
        comp = liquidity_composite(illiq, spread)

        # 最近值
        last_illiq = float(illiq.iloc[-1]) if not _np.isnan(illiq.iloc[-1]) else float("nan")
        last_spread = float(spread.iloc[-1]) if not _np.isnan(spread.iloc[-1]) else float("nan")
        last_comp = float(comp.iloc[-1]) if not _np.isnan(comp.iloc[-1]) else float("nan")
        # 历史分位（自对比）
        illiq_pct = float((illiq <= last_illiq).mean() * 100) if last_illiq == last_illiq else float("nan")
        spread_pct = float((spread <= last_spread).mean() * 100) if last_spread == last_spread else float("nan")

        table = Table(title=f"{ticker} 流动性指标（最近一日）")
        for col in ["指标", "当前值", "历史分位", "解读"]:
            table.add_column(col)
        # Amihud ILLIQ
        illiq_lvl = "高（流动性差）" if illiq_pct > 80 else ("低（流动性好）" if illiq_pct < 20 else "中位")
        table.add_row(
            "Amihud ILLIQ", f"{last_illiq:.2e}", f"{illiq_pct:.0f}%", illiq_lvl,
        )
        table.add_row(
            "ILLIQ log10(×$1M)", f"{float(illiq_log.iloc[-1]):.2f}", "—",
            "正值=单位成交额引起的价格变动大",
        )
        # CS Spread
        spread_lvl = "宽（流动性差）" if last_spread > 100 else ("窄（流动性好）" if last_spread < 20 else "正常")
        table.add_row(
            "CS Spread", f"{last_spread:.1f} bps", f"{spread_pct:.0f}%", spread_lvl,
        )
        # 综合
        comp_lvl = ("[red]流动性紧张（潜在溢价）[/red]" if last_comp > 1
                    else ("[green]流动性充裕[/green]" if last_comp < -1 else "中性"))
        table.add_row(
            "综合 z-score", f"{last_comp:+.2f}", "—", comp_lvl,
        )
        console.print(table)

        # 学术解读
        console.print(f"\n[bold]学术解读[/bold]")
        if last_illiq == last_illiq and last_spread == last_spread:
            # 流动性溢价：ILLIQ 高 → 未来收益预期高
            if illiq_pct > 80:
                console.print(
                    f"  [yellow]⚠️ {ticker} 当前 ILLIQ 处于历史 {illiq_pct:.0f}% 分位（流动性紧张）[/yellow]"
                )
                console.print(f"  学术上：ILLIQ 高的股票有流动性溢价，未来预期收益更高")
                console.print(f"  但同时：买卖冲击成本大，不适合大资金进出")
            elif illiq_pct < 20:
                console.print(
                    f"  [green]{ticker} 当前 ILLIQ 处于历史 {illiq_pct:.0f}% 分位（流动性充裕）[/green]"
                )
                console.print(f"  买卖冲击小，适合任何规模资金")
            if last_spread > 100:
                console.print(
                    f"  [yellow]CS Spread {last_spread:.0f}bps > 100bps：隐性买卖成本高[/yellow]"
                )
                console.print(f"  实际交易每股损失约 ${last_spread/10000 * float(df['close'].iloc[-1]):.3f}")

        console.print(f"\n[dim]参考：Amihud (2002) JFE | Corwin-Schultz (2012) JFE[/dim]")
        return

    # action == "matrix"
    hlist = [int(x.strip()) for x in horizons.split(",") if x.strip()]
    console.print(f"\n[bold cyan]{ticker} · Alpha101 多周期 IC 矩阵（horizons={hlist}）[/bold cyan]")

    # 预计算各 horizon 的 forward returns
    fwds = {h: log_returns(df["close"]).shift(-h) for h in hlist}

    # rows: 每个 alpha 在各 horizon 下的 IC
    matrix_rows = []
    for name, series in results.items():
        if alpha is not None and name != f"alpha_{alpha}":
            continue
        ics = {h: factor_ic(series, fwds[h]) for h in hlist}
        # 找最佳 horizon（|IC| 最大）
        valid = {h: v for h, v in ics.items() if v == v}
        best_h = max(valid, key=lambda h: abs(valid[h])) if valid else None
        best_ic = valid.get(best_h, float("nan")) if best_h else float("nan")
        matrix_rows.append({
            "name": name,
            "ics": ics,
            "best_horizon": best_h,
            "best_ic": best_ic,
        })
    # 按 best |IC| 降序
    matrix_rows.sort(key=lambda r: -abs(r["best_ic"] if r["best_ic"] == r["best_ic"] else 0))

    table = Table(show_lines=False, title=f"{ticker} Alpha101 多周期 IC 矩阵")
    table.add_column("Alpha")
    for h in hlist:
        table.add_column(f"IC@{h}d", justify="right")
    table.add_column("最佳", justify="right")
    table.add_column("最佳 IC", justify="right")
    for r in matrix_rows:
        cells = [r["name"]]
        for h in hlist:
            v = r["ics"][h]
            s = f"{v:+.3f}" if v == v else "NaN"
            cells.append(s)
        cells.append(f"{r['best_horizon']}d" if r["best_horizon"] else "—")
        best_str = f"{r['best_ic']:+.3f}" if r["best_ic"] == r["best_ic"] else "NaN"
        cells.append(best_str)
        # 最佳 |IC| > 0.05 整行标绿
        style = "green" if (r["best_ic"] == r["best_ic"] and abs(r["best_ic"]) > 0.05) else ""
        table.add_row(*cells, style=style)
    console.print(table)
    console.print("[dim]解读：IC@1d 看短线，IC@20d 看中线，IC@60d 看长线。某 alpha 在某周期 |IC|>0.05 即该周期有效[/dim]")

    if html is not None:
        from quant_scanner.reporter.factors_html import render_alpha_matrix
        render_alpha_matrix(
            ticker=ticker, period=period, horizons=hlist,
            rows=matrix_rows, output=html,
        )
        console.print(f"[bold green]HTML 报告: {html}[/bold green]")

    if html is not None:
        from quant_scanner.reporter.factors_html import render_alpha_factors
        render_alpha_factors(
            ticker=ticker, horizon=horizon, period=period,
            rows=rows, output=html,
        )
        console.print(f"[bold green]HTML 报告: {html}[/bold green]")


@main.command(name="labels")
@click.argument("action", type=click.Choice(["tb", "stats", "meta"]))
@click.option("--ticker", default="NVDA", help="标的（默认 NVDA）")
@click.option("--period", default="2y", help="历史数据周期")
@click.option("--tp", type=float, default=2.0, help="止盈 = mult × ATR（默认 2.0）")
@click.option("--sl", type=float, default=2.0, help="止损 = mult × ATR（默认 2.0）")
@click.option("--vb", type=int, default=10, help="时间屏障 bar 数（默认 10）")
@click.option("--side", type=click.Choice(["long", "short"]), default="long", help="方向")
def labels(action: str, ticker: str, period: str,
           tp: float, sl: float, vb: int, side: str) -> None:
    """标签生成：tb（triple-barrier）/ stats（标签分布）/ meta（meta-labeling 二分类）"""
    setup_logging(False)
    from quant_scanner.labels import triple_barrier_labels

    ticker = ticker.upper()
    loader = DataLoader()
    df = loader.load(ticker, period=period)
    if df.empty:
        console.print(f"[red]无法获取 {ticker} 数据[/red]")
        sys.exit(1)

    side_val = 1 if side == "long" else -1
    lab = triple_barrier_labels(
        df, tp_atr_mult=tp, sl_atr_mult=sl,
        atr_window=20, vertical_barrier_bars=vb, side=side_val,
    )

    console.print(f"\n[bold cyan]{ticker} · Triple-Barrier 标签（tp={tp}×ATR, sl={sl}×ATR, vb={vb}bars, {side}）[/bold cyan]")
    console.print(f"[dim]样本数: {len(lab)} | 数据周期: {period}[/dim]\n")

    if action == "meta":
        # Meta-labeling：primary side × triple-barrier → 二分类 target
        from quant_scanner.labels.meta_labeling import MetaLabeler, meta_label_summary
        labeler = MetaLabeler(
            tp_atr_mult=tp, sl_atr_mult=sl,
            atr_window=20, vertical_barrier_bars=vb,
        )
        meta = labeler.prepare_meta_labels(df, primary_side=side_val)
        summary = meta_label_summary(meta)

        console.print(f"\n[bold cyan]{ticker} · Meta-Labeling（primary={side}, tp={tp}×ATR, sl={sl}×ATR, vb={vb}bars）[/bold cyan]")
        console.print(f"[dim]AfML Ch 4 — López de Prado 二级分类器（方向 × 信心）[/dim]\n")
        if summary["total"] == 0:
            console.print("[yellow]无样本[/yellow]")
            return
        correct = summary["primary_correct"]
        wrong = summary["primary_wrong"]
        console.print(f"[bold]primary 方向命中[/bold]")
        console.print(f"  正确 (target=1): [green]{correct}[/green] ({correct/summary['total']*100:.1f}%)")
        console.print(f"  错误 (target=0): [red]{wrong}[/red] ({wrong/summary['total']*100:.1f}%)")
        console.print(f"\n[bold]Precision[/bold]: {summary['precision']*100:.1f}%")
        console.print(f"\n[dim]barrier 分布：{summary['by_barrier']}[/dim]")
        console.print(f"\n[dim]用途：meta_target 作为二分类 target，训练 secondary model 输出 confidence；position = side × confidence[/dim]")
        return

    if action == "stats":
        # 标签分布
        total = len(lab)
        if total == 0:
            console.print("[yellow]无标签生成[/yellow]")
            return
        tp_n = (lab["barrier_hit"] == "tp").sum()
        sl_n = (lab["barrier_hit"] == "sl").sum()
        vb_n = (lab["barrier_hit"] == "vb").sum()
        win = (lab["label"] == 1).sum()
        loss = (lab["label"] == -1).sum()
        flat = (lab["label"] == 0).sum()
        avg_ret = lab["ret"].mean()

        console.print(f"[bold]屏障分布[/bold]")
        console.print(f"  止盈触发 (tp): [green]{tp_n}[/green] ({tp_n/total*100:.1f}%)")
        console.print(f"  止损触发 (sl): [red]{sl_n}[/red] ({sl_n/total*100:.1f}%)")
        console.print(f"  时间到 (vb): [yellow]{vb_n}[/yellow] ({vb_n/total*100:.1f}%)")
        console.print(f"\n[bold]标签分布[/bold]")
        console.print(f"  +1 (win): [green]{win}[/green] ({win/total*100:.1f}%)")
        console.print(f"  -1 (loss): [red]{loss}[/red] ({loss/total*100:.1f}%)")
        console.print(f"   0 (flat): [dim]{flat}[/dim] ({flat/total*100:.1f}%)")
        console.print(f"\n[bold]平均收益[/bold]: {avg_ret*100:+.2f}%")
        console.print(f"\n[dim]参考：López de Prado AfML Ch 3 | Triple-Barrier 自适应波动率[/dim]")
        return

    # action == "tb"：打印前 20 个标签
    table = Table(title=f"{ticker} Triple-Barrier 标签（前 20）")
    for col in ["t_in", "t_out", "entry", "exit", "label", "barrier", "ret"]:
        table.add_column(col)
    for _, row in lab.head(20).iterrows():
        label_str = {1: "[green]+1[/green]", -1: "[red]-1[/red]", 0: "[dim]0[/dim]"}[row["label"]]
        barrier_color = {"tp": "green", "sl": "red", "vb": "yellow"}.get(row["barrier_hit"], "white")
        ret_str = f"{row['ret']*100:+.2f}%"
        table.add_row(
            str(row["t_in"].date()),
            str(row["t_out"].date()),
            f"{row['entry_price']:.2f}",
            f"{row['exit_price']:.2f}",
            label_str,
            f"[{barrier_color}]{row['barrier_hit']}[/{barrier_color}]",
            ret_str,
        )
    console.print(table)
    console.print(f"\n[dim]用 labels stats 看分布统计[/dim]")


@main.command(name="ml")
@click.argument("action", type=click.Choice(["secondary", "cv", "compare-models"]))
@click.option("--ticker", default="NVDA", help="标的（默认 NVDA）")
@click.option("--period", default="2y", help="历史数据周期")
@click.option("--primary-alpha", default="alpha_42",
              help="Primary alpha 名（作方向预测源，默认 alpha_42）")
@click.option("--tp", type=float, default=2.0, help="止盈 = mult × ATR")
@click.option("--sl", type=float, default=2.0, help="止损 = mult × ATR")
@click.option("--vb", type=int, default=10, help="时间屏障 bar 数")
@click.option("--test-size", type=float, default=0.3, help="OOS 比例（secondary 模式）")
@click.option("--n-splits", type=int, default=5, help="K-fold 数（cv 模式）")
@click.option("--top-features", type=int, default=10,
              help="只保留 |IC| top N 个 feature（防过拟合，默认 10）")
@click.option("--model", type=click.Choice(["lr", "gbm"]), default="gbm",
              help="Secondary 分类器：lr=LogisticRegression / gbm=GradientBoosting（默认 gbm）")
def ml(action: str, ticker: str, period: str, primary_alpha: str,
       tp: float, sl: float, vb: int, test_size: float, n_splits: int,
       top_features: int, model: str) -> None:
    """ML 流水线：secondary（端到端 meta-labeling）/ cv（Purged K-Fold 评估）/ compare-models（LR vs GBM vs MLP 对比，Gu-Kelly-Xiu 2020）"""
    setup_logging(False)
    from quant_scanner.factors.alpha101 import Alpha101
    from quant_scanner.factors.operators import factor_ic, log_returns
    from quant_scanner.ml import (
        run_meta_labeling_pipeline, cross_validate_secondary, pipeline_summary,
    )
    from quant_scanner.ml.secondary_model import SecondaryModel

    ticker = ticker.upper()
    loader = DataLoader()
    df = loader.load(ticker, period=period)
    if df.empty:
        console.print(f"[red]无法获取 {ticker} 数据[/red]")
        sys.exit(1)

    alpha_engine = Alpha101()
    all_alphas = alpha_engine.compute_all(df)
    if primary_alpha not in all_alphas:
        console.print(f"[red]{primary_alpha} 不在 Alpha101 列表[/red]")
        sys.exit(1)

    primary = all_alphas[primary_alpha]

    # 选 top features：按 |IC| 排序取 top N（防过拟合）
    fwd = log_returns(df["close"]).shift(-5)
    ic_scores = {name: factor_ic(s, fwd) for name, s in all_alphas.items()}
    sorted_names = sorted(
        [n for n in ic_scores if n != primary_alpha and ic_scores[n] == ic_scores[n]],
        key=lambda n: -abs(ic_scores[n]),
    )
    keep = sorted_names[:top_features]
    features = {k: v for k, v in all_alphas.items() if k in keep}

    # 选模型
    if model == "gbm":
        from sklearn.ensemble import GradientBoostingClassifier
        clf = GradientBoostingClassifier(
            n_estimators=100, max_depth=3, random_state=42,
        )
    else:
        from sklearn.linear_model import LogisticRegression
        clf = LogisticRegression(max_iter=1000, solver="liblinear", random_state=42)

    console.print(f"\n[bold cyan]{ticker} · AfML Meta-Labeling 闭环[/bold cyan]")
    console.print(f"[dim]Primary: {primary_alpha} | Features: {len(features)} 个 top alpha（按 |IC|）| Model: {model} | tp={tp}×ATR sl={sl}×ATR vb={vb}bars[/dim]\n")

    if action == "secondary":
        result = run_meta_labeling_pipeline(
            df, primary_alpha=primary, feature_alphas=features,
            tp_atr_mult=tp, sl_atr_mult=sl, vertical_barrier_bars=vb,
            test_size=test_size, clf=clf,
        )
        s = pipeline_summary(result)

        console.print(f"[bold]Pipeline 结果[/bold]")
        console.print(f"  总样本: {s['total_samples']} | 训练: {s['train_size']} | 测试: {s['test_size']}")
        if s['split_date']:
            console.print(f"  切分时点: [dim]{s['split_date']}[/dim]")
        console.print(f"\n[bold]精度对比（OOS 测试集）[/bold]")
        pp = s['primary_precision']
        sp = s['secondary_oos_precision']
        lift = s['lift']
        pp_str = f"{pp*100:.1f}%" if pp == pp else "NaN"
        sp_str = f"{sp*100:.1f}%" if sp == sp else "NaN"
        lift_str = f"{lift*100:+.1f}pp" if lift == lift else "NaN"
        console.print(f"  Primary baseline 命中率: [yellow]{pp_str}[/yellow]")
        console.print(f"  Secondary 过滤后命中率: [green]{sp_str}[/green]")
        console.print(f"  Lift（提升）: [bold]{lift_str}[/bold]")
        console.print(f"  过滤率: {s['filter_rate']*100:.1f}%（被 secondary 砍掉的 OOS 样本）")
        console.print(f"\n[bold]模型信息[/bold]")
        console.print(f"  特征数: {s['n_features']} | threshold: {s['threshold']}")
        if s['feature_names']:
            console.print(f"  Top 10 特征: {', '.join(s['feature_names'])}")

        console.print(f"\n[dim]解读：lift > 0 = secondary 学会了'何时信 primary'；lift < 0 = 过拟合或 primary 无信号[/dim]")
        console.print(f"[dim]参考：López de Prado AfML Ch 4 | position = side × confidence × max_position[/dim]")
        return

    if action == "compare-models":
        # Gu-Kelly-Xiu (2020 JFE): 对比 LR / GBM / MLP 三种 ML 模型的 OOS 表现
        from quant_scanner.ml.deep_factor import run_model_comparison_pipeline

        cmp = run_model_comparison_pipeline(
            df, primary_alpha=primary, feature_alphas=features,
            tp_atr_mult=tp, sl_atr_mult=sl, vertical_barrier_bars=vb,
            n_splits=n_splits, top_features=top_features,
        )
        summary = cmp.summary()

        console.print(f"[bold]Gu-Kelly-Xiu (2020) 多模型对比[/bold]")
        console.print(f"  样本数: {summary['n_total']}")
        console.print(f"  Baseline 命中率（primary 原始）: [yellow]{summary['baseline_precision']*100:.1f}%[/yellow]\n")

        table = Table(title=f"{ticker} · LR vs GBM vs MLP · Purged K-Fold（{n_splits} folds）")
        for col in ["Model", "Mean Precision", "Filter%", "Lift vs Baseline", "Folds", "Fit Fails"]:
            table.add_column(col)
        for name, info in summary["models"].items():
            prec = info["mean_precision"]
            lift = info["lift_vs_baseline"]
            lift_color = "green" if (lift == lift and lift > 0) else "red"
            table.add_row(
                name,
                f"{prec*100:.1f}%" if prec == prec else "NaN",
                f"{info['mean_filter_rate']*100:.0f}%" if info['mean_filter_rate'] == info['mean_filter_rate'] else "NaN",
                f"[{lift_color}]{lift*100:+.1f}pp[/{lift_color}]" if lift == lift else "NaN",
                str(info["n_folds_run"]),
                str(info["fit_failures"]),
            )
        console.print(table)

        best = summary["best_model"]
        best_prec = summary["best_precision"]
        if best:
            console.print(f"\n[bold green]★ 最佳模型: {best}[/bold green] （precision={best_prec*100:.1f}%）")
        else:
            console.print(f"\n[dim]无模型跑通（样本不足或单类）[/dim]")

        console.print(f"\n[dim]解读：树模型（GBM）通常最优（Gu-Kelly-Xiu 2020 发现 OOS R² 最高）；")
        console.print(f"MLP 适合大样本 + 非线性交互；LR 是线性基线。lift > 0 = 模型有效[/dim]")
        console.print(f"[dim]参考：Gu, Kelly, Xiu (2020) JFE 134(2):385-424 | 100+ alpha × 10+ ML 方法基准测试[/dim]")
        return

    # action == "cv"
    cv = cross_validate_secondary(
        df, primary_alpha=primary, feature_alphas=features,
        n_splits=n_splits,
        purge_bars=max(vb, 5), embargo_bars=max(vb // 2, 2),
        tp_atr_mult=tp, sl_atr_mult=sl, vertical_barrier_bars=vb,
        clf=clf,
    )

    # 表格
    table = Table(title=f"{ticker} Secondary Model Purged K-Fold（{n_splits} folds）")
    for col in ["Fold", "Train", "Test", "Primary P", "Secondary P", "Lift", "Filter%"]:
        table.add_column(col)
    for fr in cv["fold_results"]:
        pp = fr["primary_oos_precision"]
        sp = fr["secondary_oos_precision"]
        lift = fr["lift"]
        lift_color = "green" if (lift == lift and lift > 0) else "red"
        table.add_row(
            str(fr["fold"]),
            str(fr["train_size"]),
            str(fr["test_size"]),
            f"{pp*100:.1f}%",
            f"{sp*100:.1f}%" if sp == sp else "NaN",
            f"[{lift_color}]{lift*100:+.1f}pp[/{lift_color}]" if lift == lift else "NaN",
            f"{fr['filter_rate']*100:.0f}%",
        )
    console.print(table)

    mp = cv["mean_primary_precision"]
    ms = cv["mean_secondary_precision"]
    ml_ = cv["mean_lift"]
    mf = cv["mean_filter_rate"]
    console.print(f"\n[bold]汇总[/bold]")
    console.print(f"  平均 Primary 命中率: [yellow]{mp*100:.1f}%[/yellow]")
    console.print(f"  平均 Secondary 命中率: [green]{ms*100:.1f}%[/green]" if ms == ms else f"  平均 Secondary 命中率: NaN")
    if ml_ == ml_:
        color = "green" if ml_ > 0 else "red"
        console.print(f"  平均 Lift: [{color}]{ml_*100:+.2f}pp[/{color}]")
    console.print(f"  平均过滤率: {mf*100:.1f}%")
    console.print(f"\n[dim]解读：mean_lift > 0 = Secondary model 真实有效；mean_lift < 0 = Primary/feature 无信号或过拟合[/dim]")
    console.print(f"[dim]参考：AfML Ch 4 + Ch 7 | Purged K-Fold × Meta-Labeling 双重严格[/dim]")


if __name__ == "__main__":
    main()
