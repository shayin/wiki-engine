"""stats CLI 子命令：quant-scanner stats --by signal --period 2y

辩论共识第 11 条：
- 默认排序 EV 降序
- 报告披露 t+1 open / long-only / holdout 状态 / Wilson 区间 / 低置信度灰显
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

import click
import pandas as pd
from rich.console import Console

from quant_scanner.data.loader import DataLoader
from quant_scanner.signals.trend_template import TrendTemplateSignal
from quant_scanner.signals.vcp import VCPSignal
from quant_scanner.signals.pivot_point import PivotPointSignal
from quant_scanner.signals.sell_signals import BaseCountingSignal
from quant_scanner.signals.can_slim import CANSLIMSignal
from quant_scanner.signals.market_direction import MarketDirectionSignal
from quant_scanner.signals.new_high_supply import NewHighSupplySignal
from quant_scanner.signals.dow_phases import DowPhasesSignal
from quant_scanner.signals.support_resistance import SupportResistanceSignal
from quant_scanner.signals.major_reversal import MajorReversalSignal
from quant_scanner.signals.continuation import ContinuationSignal
from quant_scanner.signals.oscillator_timing import OscillatorTimingSignal
from quant_scanner.signals.rs_rating import RSRatingSignal
from quant_scanner.signals.cup_handle import CupHandleSignal
from quant_scanner.stats.analyzer import (
    compute_forward_labels,
    aggregate,
    split_holdout,
    set_tier_thresholds,
    HORIZON_DAYS,
)
from quant_scanner.stats.events import (
    SignalEventCollector,
    filter_long_only,
    filter_diagnostic,
)
from quant_scanner.stats.reporter import StatsReporter, DiagnosticRow, HoldoutTable
from quant_scanner.stats.runner import SignalEventRunner

console = Console()
log = logging.getLogger(__name__)


def _resolve_window(period: str, start: str | None, end: str | None) -> tuple[str, str]:
    """把 period/start/end 解析成 (start, end) 字符串。"""
    if start is not None and end is not None:
        return start, end
    end_ts = pd.Timestamp.now().tz_localize(None)
    if period.endswith("y"):
        start_ts = end_ts - pd.DateOffset(years=int(period[:-1]))
    elif period.endswith("m"):
        start_ts = end_ts - pd.DateOffset(months=int(period[:-1]))
    else:
        start_ts = end_ts - pd.DateOffset(years=2)
    return start or start_ts.strftime("%Y-%m-%d"), end or end_ts.strftime("%Y-%m-%d")


def _default_signals() -> list:
    """返回全部 16 个 signal 的实例（与 scan 子命令一致）。"""
    return [
        TrendTemplateSignal(),
        VCPSignal(),
        PivotPointSignal(),
        BaseCountingSignal(),
        CupHandleSignal(),
        MarketDirectionSignal(),
        MajorReversalSignal(),
        ContinuationSignal(),
        OscillatorTimingSignal(),
        RSRatingSignal(),
        CANSLIMSignal(),
        NewHighSupplySignal(),
        DowPhasesSignal(),
        SupportResistanceSignal(),
    ]


# 纯价格 signal（无外部数据依赖，跑得快）
PRICE_ONLY_SIGNALS: tuple[str, ...] = (
    "trend_template", "vcp", "pivot_point", "base_counting",
    "cup_handle", "trend_regime", "major_reversal", "continuation",
    "oscillator_timing", "support_resistance", "dow_phases",
)

# 需要 EDGAR / yfinance 基本面数据的 signal（慢，每次评估触发外部 API）
EXTERNAL_DATA_SIGNALS: tuple[str, ...] = (
    "can_slim", "rs_rating", "new_high_supply",
)


def _select_signals(price_only: bool, include_external: bool, exclude: tuple[str, ...]) -> list:
    """按用户选项选择 signal 子集。"""
    all_signals = {
        s.name: s for s in _default_signals()
    }
    if price_only:
        names = [n for n in PRICE_ONLY_SIGNALS if n not in exclude]
    elif include_external:
        names = [n for n in all_signals if n not in exclude]
    else:
        names = [n for n in all_signals if n not in exclude]
    return [all_signals[n] for n in names if n in all_signals]


@click.command()
@click.argument("tickers", nargs=-1)
@click.option("--period", default="2y", help="回溯周期（如 2y / 5y）")
@click.option("--start", default=None, help="起始日 YYYY-MM-DD（覆盖 period）")
@click.option("--end", default=None, help="结束日 YYYY-MM-DD")
@click.option("--horizon", type=int, default=20, help="前瞻窗口（5/20/60，默认 20）")
@click.option("--cooldown-days", type=int, default=20, help="同事件去重冷却期（交易日）")
@click.option("--holdout-months", type=int, default=12, help="holdout 月数（0 关闭）")
@click.option("--min-sample", type=int, default=None, help="低样本分层阈值覆盖（不建议改）")
@click.option("--include-short/--long-only", default=False, help="（已禁用）保留兼容；short/unknown 永远走诊断桶")
@click.option("--output", "-o", type=click.Path(path_type=Path), help="HTML 报告输出路径")
@click.option("--price-only", is_flag=True, help="只用纯价格 signal（默认），跳过外部数据型（can_slim/rs_rating/new_high_supply）")
@click.option("--include-external", is_flag=True, help="包含外部数据型 signal（EDGAR/yfinance，每次评估会查 API，很慢）")
@click.option("--exclude", multiple=True, help="排除指定 signal（按 name，可多次）")
@click.option("--verbose", "-v", is_flag=True)
def stats(
    tickers: tuple[str, ...],
    period: str,
    start: str | None,
    end: str | None,
    horizon: int,
    cooldown_days: int,
    holdout_months: int,
    min_sample: int | None,
    include_short: bool,
    output: Path | None,
    price_only: bool,
    include_external: bool,
    exclude: tuple[str, ...],
    verbose: bool,
) -> None:
    """胜率统计：采集事件 + 前瞻标签 + 切片聚合 + HTML 报告"""
    if verbose:
        logging.basicConfig(level=logging.DEBUG, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%H:%M:%S")
    else:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%H:%M:%S")

    if not tickers:
        click.echo("需要至少一个 ticker", err=True)
        raise click.exceptions.Exit(1)

    # 计算 start/end
    if start is None or end is None:
        end_ts = pd.Timestamp.now().tz_localize(None)
        if period.endswith("y"):
            years = int(period[:-1])
            start_ts = end_ts - pd.DateOffset(years=years)
        elif period.endswith("m"):
            months = int(period[:-1])
            start_ts = end_ts - pd.DateOffset(months=months)
        else:
            start_ts = end_ts - pd.DateOffset(years=2)
        start = start or start_ts.strftime("%Y-%m-%d")
        end = end or end_ts.strftime("%Y-%m-%d")

    console.print(f"[bold green]开始 stats 采集 {tickers} {start}~{end} horizon={horizon}[/bold green]")

    # review Medium 2：--min-sample 真实生效
    set_tier_thresholds(min_sample)

    # 1. 采集事件
    if price_only and include_external:
        click.echo("[--price-only 和 --include-external 互斥，默认走 --price-only]", err=True)
        price_only = True
        include_external = False
    if not price_only and not include_external:
        # 默认 price-only（避免外部 API 慢）
        price_only = True
    signals = _select_signals(price_only=price_only, include_external=include_external, exclude=exclude)
    console.print(f"[cyan]启用 {len(signals)} 个 signal: {[s.name for s in signals]}[/cyan]")
    loader = DataLoader()
    runner = SignalEventRunner(
        signals=signals,
        universe=list(tickers),
        loader=loader,
        collector=SignalEventCollector(cooldown_days=cooldown_days),
    )
    all_events = runner.run(start=start, end=end, cooldown_days=cooldown_days)
    console.print(f"[cyan]采集到 {len(all_events)} 个事件[/cyan]")

    # 2. 拉取价格数据（用于前瞻标签）
    price_lookup: dict[str, pd.DataFrame] = {}
    for tk in tickers:
        df = loader.load(tk, start=start, end=end, cache_key=f"{tk}_{start}_{end}")
        if df is not None and not df.empty:
            price_lookup[tk] = df.sort_index()

    # 3. 全交易日并集
    all_dates: pd.DatetimeIndex | None = None
    for df in price_lookup.values():
        idx = pd.to_datetime(df.index)
        all_dates = idx if all_dates is None else all_dates.union(idx)
    if all_dates is None:
        all_dates = pd.DatetimeIndex([])
    all_dates = all_dates.sort_values()

    # 4. holdout 切分（事件后切分）
    split = split_holdout(all_events, all_dates, holdout_months=holdout_months)
    console.print(f"[cyan]训练事件 {len(split.train_events)} / holdout 事件 {len(split.holdout_events)}[/cyan]")
    console.print(f"[cyan]holdout 起始: {split.holdout_start}[/cyan]")

    # 5. 前瞻标签（含 holdout 边界 censored）
    holdout_start = split.holdout_start
    train_labels = compute_forward_labels(
        split.train_events, price_lookup,
        horizons=(horizon,),
        holdout_start=holdout_start,
    )
    holdout_labels = compute_forward_labels(
        split.holdout_events, price_lookup,
        horizons=(horizon,),
        holdout_start=None,  # holdout 段内不再 censored
    )

    # 6. 第一期 long-only 合同：主排名只含 long
    # review High 5：移除 --include-short 真实生效，short/unknown 永远走诊断桶
    if include_short:
        console.print("[yellow]--include-short 已禁用（与 long-only 合同冲突），short/unknown 仍走诊断桶[/yellow]")
    train_events_for_agg = filter_long_only(split.train_events)
    holdout_events_for_agg = filter_long_only(split.holdout_events)

    diagnostic_events = filter_diagnostic(split.train_events)

    # 7. 聚合
    top_train = aggregate(train_events_for_agg, train_labels, horizon=horizon, by="signal")
    second_train = aggregate(train_events_for_agg, train_labels, horizon=horizon, by="event_type")

    # holdout 聚合（review High 1：必须并列展示）
    holdout_events_for_top = holdout_events_for_agg  # 已按 long-only 过滤
    top_holdout = aggregate(holdout_events_for_top, holdout_labels, horizon=horizon, by="signal")
    second_holdout = aggregate(holdout_events_for_top, holdout_labels, horizon=horizon, by="event_type")
    holdout_validated = (
        split.train_satisfied
        and split.holdout_satisfied
        and len(holdout_events_for_top) > 0
        and any(l.status == "ok" for l in holdout_labels)
    )
    holdout_table = HoldoutTable(
        top_level=top_holdout,
        second_level=second_holdout,
        is_validated=holdout_validated,
    )

    # 诊断桶统计
    diag_map: dict[tuple[str, str, str], int] = {}
    for ev in diagnostic_events:
        key = (ev.signal_name, ev.event_type, ev.direction)
        diag_map[key] = diag_map.get(key, 0) + 1
    diagnostic_rows = [
        DiagnosticRow(signal_name=k[0], event_type=k[1], direction=k[2], n=v)
        for k, v in sorted(diag_map.items(), key=lambda x: -x[1])
    ]

    # censored 数量
    total_censored = sum(1 for l in train_labels + holdout_labels if l.status == "censored")

    # 8. HTML 报告
    reporter = StatsReporter()
    if output is None:
        stamp = datetime.now().strftime("%Y-%m-%d")
        output = reporter.default_output_path(stamp)
    reporter.render(
        top_level=top_train,
        second_level=second_train,
        diagnostic=diagnostic_rows,
        split=split,
        total_events=len(all_events),
        total_censored=total_censored,
        horizon=horizon,
        universe_size=len(tickers),
        output=output,
        holdout_table=holdout_table,
    )
    console.print(f"[bold green]HTML 报告已生成: {output}[/bold green]")
    console.print(f"[dim]披露：stats 时点 t+1 open（保守）；long-only={not include_short}；holdout_months={holdout_months}[/dim]")


@click.command(name="sector-stats")
@click.argument("etfs", nargs=-1)
@click.option("--period", default="5y", help="回溯周期（板块回测建议 ≥3y 才有统计意义，默认 5y）")
@click.option("--start", default=None, help="起始日 YYYY-MM-DD（覆盖 period）")
@click.option("--end", default=None, help="结束日 YYYY-MM-DD")
@click.option("--horizon", type=int, default=20, help="前瞻窗口（5/20/60，默认 20）")
@click.option("--cooldown-days", type=int, default=20, help="同 ETF × 同事件类型相邻事件最小间隔（交易日）")
@click.option("--holdout-months", type=int, default=12, help="holdout 月数（0 关闭）")
@click.option("--min-sample", type=int, default=None, help="低样本分层阈值覆盖")
@click.option("--output", "-o", type=click.Path(path_type=Path), help="HTML 报告输出路径")
@click.option("--verbose", "-v", is_flag=True)
def sector_stats(
    etfs: tuple[str, ...],
    period: str,
    start: str | None,
    end: str | None,
    horizon: int,
    cooldown_days: int,
    holdout_months: int,
    min_sample: int | None,
    output: Path | None,
    verbose: bool,
) -> None:
    """板块启动信号历史回测：突破+放量+RS升 及 6 种条件组合的胜率/EV 对比

    采集每个 ETF 历史上每个交易日的板块启动信号（PIT），按 6 种 event_type 拆解：
    LAUNCH_STRICT / LAUNCH_LOOSE / BREAKOUT / BREAKOUT_VOL / VOLUME_SURGE / RS_RISING

    复用 stats 基建（前瞻标签 + Wilson 区间 + holdout），回答：
    - 三重确认整体胜率？
    - 严格 vs 宽松突破哪个有效？
    - RS 上升是否关键加成？（BREAKOUT vs LAUNCH_STRICT）
    """
    if verbose:
        logging.basicConfig(level=logging.DEBUG, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%H:%M:%S")
    else:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%H:%M:%S")

    # 延迟 import（跟随 scanner.cli 的 sector/screen/market 风格，避免顶部加载）
    from quant_scanner.features.sector import (
        SECTOR_ETFS, BENCHMARK, collect_sector_launch_events,
    )

    etf_list = [e.upper() for e in etfs] if etfs else list(SECTOR_ETFS.keys())
    start, end = _resolve_window(period, start, end)
    console.print(f"[bold green]板块启动信号回测 {len(etf_list)} 个 ETF {start}~{end} horizon={horizon}[/bold green]")
    console.print(f"[cyan]ETF: {etf_list}[/cyan]")

    set_tier_thresholds(min_sample)
    loader = DataLoader()

    # 拉 SPY 基准
    spy = loader.load(BENCHMARK, start=start, end=end, cache_key=f"{BENCHMARK}_{start}_{end}")
    if spy is None or spy.empty:
        console.print(f"[red]无法获取 {BENCHMARK} 数据，中止[/red]")
        raise click.exceptions.Exit(1)
    spy = spy.sort_index()

    # 逐 ETF 采集事件
    all_events: list = []
    price_lookup: dict[str, pd.DataFrame] = {}
    for etf in etf_list:
        df = loader.load(etf, start=start, end=end, cache_key=f"{etf}_{start}_{end}")
        if df is None or df.empty:
            console.print(f"[yellow]跳过 {etf}：无数据[/yellow]")
            continue
        df = df.sort_index()
        price_lookup[etf] = df
        evs = collect_sector_launch_events(etf, df, spy, cooldown_days=cooldown_days)
        all_events.extend(evs)
        console.print(f"[dim]{etf}: 采集 {len(evs)} 个事件[/dim]")

    console.print(f"[cyan]共采集 {len(all_events)} 个事件（{len(price_lookup)} 个 ETF 有数据）[/cyan]")
    if not all_events:
        console.print("[red]无事件，无法统计（尝试拉长 --period）[/red]")
        raise click.exceptions.Exit(1)

    # 全交易日并集
    all_dates: pd.DatetimeIndex | None = None
    for df in price_lookup.values():
        idx_d = pd.to_datetime(df.index)
        all_dates = idx_d if all_dates is None else all_dates.union(idx_d)
    if all_dates is None:
        all_dates = pd.DatetimeIndex([])
    all_dates = all_dates.sort_values()

    # holdout 切分
    split = split_holdout(all_events, all_dates, holdout_months=holdout_months)
    console.print(f"[cyan]训练事件 {len(split.train_events)} / holdout 事件 {len(split.holdout_events)}[/cyan]")

    # 前瞻标签
    train_labels = compute_forward_labels(
        split.train_events, price_lookup, horizons=(horizon,), holdout_start=split.holdout_start,
    )
    holdout_labels = compute_forward_labels(
        split.holdout_events, price_lookup, horizons=(horizon,), holdout_start=None,
    )

    # 板块事件全是 long，filter_long_only 透传，诊断桶为空
    train_long = filter_long_only(split.train_events)
    holdout_long = filter_long_only(split.holdout_events)

    # 聚合：核心是 by="event_type"（6 种条件对比）
    top_train = aggregate(train_long, train_labels, horizon=horizon, by="signal")
    second_train = aggregate(train_long, train_labels, horizon=horizon, by="event_type")
    top_holdout = aggregate(holdout_long, holdout_labels, horizon=horizon, by="signal")
    second_holdout = aggregate(holdout_long, holdout_labels, horizon=horizon, by="event_type")
    holdout_validated = (
        split.train_satisfied and split.holdout_satisfied
        and len(holdout_long) > 0
        and any(l.status == "ok" for l in holdout_labels)
    )
    holdout_table = HoldoutTable(
        top_level=top_holdout, second_level=second_holdout, is_validated=holdout_validated,
    )

    total_censored = sum(1 for l in train_labels + holdout_labels if l.status == "censored")

    reporter = StatsReporter()
    if output is None:
        stamp = datetime.now().strftime("%Y-%m-%d")
        output = reporter.default_output_path(f"{stamp}-sector")
    reporter.render(
        top_level=top_train,
        second_level=second_train,
        diagnostic=[],  # 板块事件全 long，无诊断桶
        split=split,
        total_events=len(all_events),
        total_censored=total_censored,
        horizon=horizon,
        universe_size=len(price_lookup),
        output=output,
        holdout_table=holdout_table,
    )
    console.print(f"[bold green]HTML 报告已生成: {output}[/bold green]")
    console.print(f"[dim]披露：ETF 前瞻 t+1 open；6 种 event_type 对比条件贡献；holdout_months={holdout_months}[/dim]")
