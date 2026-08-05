"""前瞻标签 + 统计聚合 + Wilson 区间 + 分层 + holdout

辩论共识第 4/5/6/7/8 条：
- 前瞻窗口 5/20/60（统一，避免数据窥探）
- 从 t+1 open 起算，应用 engine 同款 slippage + commission
- horizon 不足或跨 holdout 边界 → censored 不计分母
- 指标：n / 胜率 / EV（默认排序键降序）/ 中位数 / 平均盈/亏 / profit factor / Wilson 区间
- 低样本分层：<20 灰 / 20-50 探索 / 50-200 低置信 / ≥200 默认
- holdout 默认 12 个月，事件后切分，跨界 censored
- 第一期 long-only 主排名，short/unknown 诊断桶
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Iterable, Literal

import numpy as np
import pandas as pd

from quant_scanner.stats.events import SignalEvent

log = logging.getLogger(__name__)

HORIZON_DAYS = (5, 20, 60)
HORIZON_STATUS = Literal["ok", "censored", "no_data"]

DEFAULT_MIN_TRADE_DAYS = 126  # 训练段/holdout 段各自最小交易日


# =====================================================================
# Wilson 置信区间
# =====================================================================


def wilson_interval(wins: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson 95% 置信区间（n=0 时返回 (0, 1)）。"""
    if n == 0:
        return 0.0, 1.0
    p = wins / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    margin = (z / denom) * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return max(0.0, center - margin), min(1.0, center + margin)


# =====================================================================
# 前瞻标签
# =====================================================================


@dataclass
class ForwardLabel:
    """单事件 × 单窗口的前瞻标签。"""

    event_id: str
    horizon: int
    entry_price: float | None  # t+1 open × (1 + slippage)
    exit_price: float | None  # t+N close × (1 - slippage)
    forward_return: float | None  # 已扣 commission + slippage 的净收益
    status: HORIZON_STATUS = "ok"
    mae: float | None = None  # 最大不利波动
    mfe: float | None = None  # 最大有利波动


def compute_forward_labels(
    events: list[SignalEvent],
    price_lookup: dict[str, pd.DataFrame],
    horizons: tuple[int, ...] = HORIZON_DAYS,
    slippage_pct: float = 0.001,
    commission_pct: float = 0.001,
    holdout_start: pd.Timestamp | None = None,
) -> list[ForwardLabel]:
    """为所有事件计算前瞻标签。

    Args:
        events: 采集的事件
        price_lookup: {ticker: OHLCV df}
        horizons: 窗口列表（5/20/60）
        slippage_pct: 滑点
        commission_pct: 单边佣金
        holdout_start: holdout 段起始日；事件前瞻跨此边界则 censored

    Returns:
        ForwardLabel 列表
    """
    labels: list[ForwardLabel] = []

    for ev in events:
        df = price_lookup.get(ev.ticker)
        if df is None or df.empty:
            for h in horizons:
                labels.append(ForwardLabel(
                    event_id=ev.event_id, horizon=h,
                    entry_price=None, exit_price=None, forward_return=None,
                    status="no_data",
                ))
            continue

        idx = pd.to_datetime(df.index)
        # entry_date = t+1
        if ev.entry_date is None:
            for h in horizons:
                labels.append(ForwardLabel(
                    event_id=ev.event_id, horizon=h,
                    entry_price=None, exit_price=None, forward_return=None,
                    status="no_data",
                ))
            continue

        entry_date = ev.entry_date
        # 找 entry_date 当日的 open
        try:
            entry_loc = idx.get_loc(entry_date)
        except KeyError:
            # 找最近 >= entry_date 的交易日
            mask = idx >= entry_date
            if not mask.any():
                for h in horizons:
                    labels.append(ForwardLabel(
                        event_id=ev.event_id, horizon=h,
                        entry_price=None, exit_price=None, forward_return=None,
                        status="no_data",
                    ))
                continue
            entry_loc = int(np.argmax(mask.values))

        if entry_loc >= len(idx):
            for h in horizons:
                labels.append(ForwardLabel(
                    event_id=ev.event_id, horizon=h,
                    entry_price=None, exit_price=None, forward_return=None,
                    status="no_data",
                ))
            continue

        entry_open = float(df["open"].iloc[entry_loc])
        entry_executed = entry_open * (1 + slippage_pct)

        for h in horizons:
            exit_loc = entry_loc + h
            if exit_loc >= len(idx):
                labels.append(ForwardLabel(
                    event_id=ev.event_id, horizon=h,
                    entry_price=entry_executed, exit_price=None, forward_return=None,
                    status="censored",
                ))
                continue

            exit_date = idx[exit_loc]
            # holdout 边界检查
            if holdout_start is not None and exit_date >= holdout_start and entry_date < holdout_start:
                labels.append(ForwardLabel(
                    event_id=ev.event_id, horizon=h,
                    entry_price=entry_executed, exit_price=None, forward_return=None,
                    status="censored",
                ))
                continue

            exit_close = float(df["close"].iloc[exit_loc])
            exit_executed = exit_close * (1 - slippage_pct)

            # 净收益（含双边 commission）
            buy_notional = entry_executed * (1 + commission_pct)
            sell_notional = exit_executed * (1 - commission_pct)
            forward_return = (sell_notional / buy_notional) - 1.0

            # MAE/MFE：在 entry_loc+1 ~ exit_loc 之间
            window = df.iloc[entry_loc : exit_loc + 1]
            lows = window["low"].values
            highs = window["high"].values
            mae = float(min(lows.min() / entry_executed - 1.0, 0.0))
            mfe = float(max(highs.max() / entry_executed - 1.0, 0.0))

            labels.append(ForwardLabel(
                event_id=ev.event_id, horizon=h,
                entry_price=entry_executed, exit_price=exit_executed,
                forward_return=forward_return, status="ok",
                mae=mae, mfe=mfe,
            ))

    return labels


# =====================================================================
# 聚合统计
# =====================================================================


@dataclass
class SliceStats:
    """单个切片（signal × event_type × direction）的统计。"""

    signal_name: str
    event_type: str
    direction: str
    n: int = 0
    n_censored: int = 0
    win_rate: float = 0.0
    win_rate_low: float = 0.0  # Wilson 95% 下界
    win_rate_high: float = 0.0  # Wilson 95% 上界
    ev: float = 0.0  # 平均净收益（默认排序键）
    median_return: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    profit_factor: float = 0.0
    avg_mae: float = 0.0
    avg_mfe: float = 0.0
    confidence_tier: str = "n/a"  # grey / exploratory / low / default


def _confidence_tier(n: int, thresholds: tuple[int, int, int] = (20, 50, 200)) -> str:
    grey, expl, low = thresholds
    if n < grey:
        return "grey"
    if n < expl:
        return "exploratory"
    if n < low:
        return "low"
    return "default"


# 默认阈值（共识第 5 条）+ 全局覆盖（review Medium 2：--min-sample 真实生效）
_TIER_THRESHOLDS: tuple[int, int, int] = (20, 50, 200)


def set_tier_thresholds(min_sample: int | None = None) -> None:
    """设置全局置信度分层阈值。

    min_sample > 0 时，把它作为 grey 阈值（其他按 3x / 10x 比例缩放）。
    None 时恢复默认 (20, 50, 200)。
    """
    global _TIER_THRESHOLDS
    if min_sample is None or min_sample <= 0:
        _TIER_THRESHOLDS = (20, 50, 200)
    else:
        # 用 min_sample 作为 grey 阈值，其他等比放大
        _TIER_THRESHOLDS = (min_sample, min_sample * 3, min_sample * 10)


def aggregate(
    events: list[SignalEvent],
    labels: list[ForwardLabel],
    horizon: int = 20,
    by: Literal["signal", "event_type"] = "event_type",
) -> list[SliceStats]:
    """按 signal 或 signal × event_type × direction 聚合统计。

    Args:
        events: 事件列表
        labels: 前瞻标签
        horizon: 选择哪个窗口的标签参与统计
        by: "signal" → 顶层 signal 总表；"event_type" → 二级表

    Returns:
        SliceStats 列表
    """
    if horizon not in HORIZON_DAYS:
        log.warning("horizon=%s 不在默认 %s 内；仍尝试聚合", horizon, HORIZON_DAYS)

    # 索引事件
    event_by_id = {e.event_id: e for e in events}

    # 按 (signal, event_type, direction) 分组
    groups: dict[tuple[str, str, str], list[float]] = {}
    group_censored: dict[tuple[str, str, str], int] = {}
    group_mae: dict[tuple[str, str, str], list[float]] = {}
    group_mfe: dict[tuple[str, str, str], list[float]] = {}

    for label in labels:
        if label.horizon != horizon:
            continue
        ev = event_by_id.get(label.event_id)
        if ev is None:
            continue
        if by == "signal":
            key = (ev.signal_name, ev.signal_name, "all")
        else:
            key = (ev.signal_name, ev.event_type, ev.direction)

        if label.status != "ok" or label.forward_return is None:
            group_censored[key] = group_censored.get(key, 0) + 1
            continue

        groups.setdefault(key, []).append(label.forward_return)
        if label.mae is not None:
            group_mae.setdefault(key, []).append(label.mae)
        if label.mfe is not None:
            group_mfe.setdefault(key, []).append(label.mfe)

    results: list[SliceStats] = []
    for key, returns in sorted(groups.items()):
        signal_name, event_type, direction = key
        n = len(returns)
        wins = sum(1 for r in returns if r > 0)
        win_rate = wins / n if n > 0 else 0.0
        lo, hi = wilson_interval(wins, n)
        arr = np.array(returns)
        gains = arr[arr > 0]
        losses = arr[arr < 0]
        avg_win = float(gains.mean()) if len(gains) > 0 else 0.0
        avg_loss = float(losses.mean()) if len(losses) > 0 else 0.0
        pf = float(gains.sum() / abs(losses.sum())) if losses.sum() != 0 else float("inf")
        maes = group_mae.get(key, [])
        mfes = group_mfe.get(key, [])

        stats = SliceStats(
            signal_name=signal_name,
            event_type=event_type,
            direction=direction,
            n=n,
            n_censored=group_censored.get(key, 0),
            win_rate=win_rate,
            win_rate_low=lo,
            win_rate_high=hi,
            ev=float(arr.mean()),
            median_return=float(np.median(arr)),
            avg_win=avg_win,
            avg_loss=avg_loss,
            profit_factor=pf,
            avg_mae=float(np.mean(maes)) if maes else 0.0,
            avg_mfe=float(np.mean(mfes)) if mfes else 0.0,
            confidence_tier=_confidence_tier(n, _TIER_THRESHOLDS),
        )
        results.append(stats)

    # 默认按 EV 降序（共识第 4 条）
    results.sort(key=lambda s: -s.ev)
    return results


# =====================================================================
# Holdout 切分
# =====================================================================


@dataclass
class HoldoutSplit:
    """holdout 切分结果。"""

    train_events: list[SignalEvent] = field(default_factory=list)
    holdout_events: list[SignalEvent] = field(default_factory=list)
    holdout_start: pd.Timestamp | None = None
    train_satisfied: bool = False
    holdout_satisfied: bool = False
    min_trade_days: int = DEFAULT_MIN_TRADE_DAYS


def split_holdout(
    events: list[SignalEvent],
    all_trading_dates: pd.DatetimeIndex,
    holdout_months: int = 12,
    min_trade_days: int = DEFAULT_MIN_TRADE_DAYS,
) -> HoldoutSplit:
    """按事件 confirmed_date 切分训练/holdout（共识第 6 条）。

    Args:
        events: 全部事件
        all_trading_dates: 全交易日并集
        holdout_months: holdout 月数，0 = 关闭
        min_trade_days: 每段最小交易日

    Returns:
        HoldoutSplit
    """
    if holdout_months <= 0 or len(all_trading_dates) == 0:
        return HoldoutSplit(
            train_events=list(events),
            holdout_events=[],
            holdout_start=None,
            train_satisfied=len(all_trading_dates) >= min_trade_days,
            holdout_satisfied=False,
        )

    sorted_dates = all_trading_dates.sort_values()
    holdout_start = sorted_dates[-min_trade_days] if len(sorted_dates) >= min_trade_days else sorted_dates[0]
    # 用最后 holdout_months 个月的近似交易日（按 21 日/月）
    approx_holdout_days = holdout_months * 21
    if len(sorted_dates) > approx_holdout_days:
        holdout_start = sorted_dates[-approx_holdout_days]
    else:
        holdout_start = sorted_dates[0]

    train_events: list[SignalEvent] = []
    holdout_events: list[SignalEvent] = []
    for ev in events:
        # 共识第 6 条（review High 2 修正）：按 entry_date 归属分段
        # entry_date 为 None（无后续交易日）的事件归属训练段
        seg_date = ev.entry_date if ev.entry_date is not None else ev.confirmed_date
        if seg_date is None:
            train_events.append(ev)
            continue
        if seg_date >= holdout_start:
            holdout_events.append(ev)
        else:
            train_events.append(ev)

    train_dates = sorted_dates[sorted_dates < holdout_start]
    holdout_dates = sorted_dates[sorted_dates >= holdout_start]

    return HoldoutSplit(
        train_events=train_events,
        holdout_events=holdout_events,
        holdout_start=holdout_start,
        train_satisfied=len(train_dates) >= min_trade_days,
        holdout_satisfied=len(holdout_dates) >= min_trade_days,
    )
