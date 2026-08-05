"""trade_sim.py — 入场信号 + 退出规则的完整交易模拟

stats 模块（compute_forward_labels）只算固定 horizon 前瞻，不模拟中途退出。
本模块回答"完整策略（入场 + 退出规则）的实际 EV"——对 screen_trend_plan 给出的
止损/目标计划做真实命中率验证。

对比退出规则：
- hold: 纯持有 N 天（baseline，对齐 stats 前瞻）
- stop_target: 固定止损(entry - k×ATR) + 目标(R:1)，验证 screen_trend_plan 命中率

模拟细节：
- entry = 信号 t+1 日 open × (1+slippage)（与 stats 一致）
- 逐日检查 open gap / 日内 high/low 是否触及 stop/target
- open gap 优先（跳空开盘按开盘价成交），日内触及按 stop/target 价
- 超 max_hold 或数据末尾 → time / censored
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd

from quant_scanner.stats.events import SignalEvent

log = logging.getLogger(__name__)

ExitRule = Literal["hold", "stop_target", "sell_signal"]
ExitReason = Literal["target", "target_gap", "stop", "stop_gap", "sell_signal", "time", "censored", "no_data"]


@dataclass
class TradeResult:
    """单笔交易模拟结果。"""

    event_id: str
    ticker: str
    entry_date: pd.Timestamp | None
    entry_price: float | None  # 含买入 slippage
    exit_date: pd.Timestamp | None
    exit_price: float | None  # 含卖出 slippage
    hold_days: int
    return_pct: float | None  # 已扣双边 commission + slippage 的净收益
    exit_reason: ExitReason
    atr: float | None
    stop_price: float | None
    target_price: float | None


def _atr_at(df: pd.DataFrame, loc: int, n: int = 14) -> float | None:
    """PIT：用 df.iloc[:loc+1] 算 ATR 末值（入场时已知，不泄露未来）。"""
    sub = df.iloc[: loc + 1]
    if len(sub) < n + 1:
        return None
    h, l, c = sub["high"], sub["low"], sub["close"]
    prev_c = c.shift(1)
    tr = pd.concat([(h - l), (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1)
    val = tr.rolling(n).mean().iloc[-1]
    return float(val) if pd.notna(val) else None


def _net_return(entry_executed: float, exit_executed: float, commission_pct: float) -> float:
    """扣双边 commission 的净收益（slippage 已在 executed 价里）。"""
    buy_notional = entry_executed * (1 + commission_pct)
    sell_notional = exit_executed * (1 - commission_pct)
    return sell_notional / buy_notional - 1.0


def simulate_trade(
    event: SignalEvent,
    df: pd.DataFrame,
    exit_rule: ExitRule = "stop_target",
    max_hold: int = 60,
    atr_mult: float = 2.0,
    target_R: float = 2.0,
    slippage_pct: float = 0.001,
    commission_pct: float = 0.001,
    exit_signal=None,
) -> TradeResult:
    """模拟单笔交易。

    Args:
        event: 入场事件（entry_date = t+1 信号次日）
        df: 该 ticker OHLCV
        exit_rule: "hold"=纯持有 max_hold 天；"stop_target"=止损(entry-atr_mult×ATR)+目标(R:1)
        max_hold: 最大持有交易日
        atr_mult: 止损 = entry - atr_mult×ATR（默认 2）
        target_R: 盈亏比（默认 2:1，对齐 screen_trend_plan）
        slippage_pct / commission_pct: 交易成本
    """
    no_data = TradeResult(
        event.event_id, event.ticker, event.entry_date, None, None, None,
        0, None, "no_data", None, None, None,
    )
    if event.entry_date is None or df is None or df.empty:
        return no_data

    idx = pd.to_datetime(df.index)
    try:
        entry_loc = int(idx.get_loc(event.entry_date))
    except KeyError:
        mask = idx >= event.entry_date
        if not mask.any():
            return no_data
        entry_loc = int(np.argmax(mask.values))

    if entry_loc >= len(idx):
        return no_data

    entry_executed = float(df["open"].iloc[entry_loc]) * (1 + slippage_pct)

    # ---- hold 规则：纯持有 max_hold 天 ----
    if exit_rule == "hold":
        exit_loc = entry_loc + max_hold
        if exit_loc >= len(idx):
            return TradeResult(
                event.event_id, event.ticker, event.entry_date, entry_executed,
                None, None, len(idx) - entry_loc - 1, None, "censored", None, None, None,
            )
        exit_executed = float(df["close"].iloc[exit_loc]) * (1 - slippage_pct)
        return TradeResult(
            event.event_id, event.ticker, event.entry_date, entry_executed,
            idx[exit_loc], exit_executed, max_hold,
            _net_return(entry_executed, exit_executed, commission_pct),
            "time", None, None, None,
        )

    # ---- sell_signal 规则：逐日 PIT 评估退出信号，passed 时收盘退出 ----
    if exit_rule == "sell_signal":
        end = min(entry_loc + 1 + max_hold, len(idx))
        if exit_signal is not None:
            for i in range(entry_loc + 1, end):
                try:
                    sr = exit_signal.evaluate(event.ticker, df.iloc[: i + 1])
                except Exception:
                    continue
                if sr.passed:
                    exit_executed = float(df["close"].iloc[i]) * (1 - slippage_pct)
                    return TradeResult(
                        event.event_id, event.ticker, event.entry_date, entry_executed,
                        idx[i], exit_executed, i - entry_loc,
                        _net_return(entry_executed, exit_executed, commission_pct),
                        "sell_signal", None, None, None,
                    )
        # 持有期内未触发 → 超时 time / 数据末尾 censored
        exit_loc = entry_loc + max_hold
        if exit_loc >= len(idx):
            exit_loc = len(idx) - 1
            reason: ExitReason = "censored"
        else:
            reason = "time"
        exit_executed = float(df["close"].iloc[exit_loc]) * (1 - slippage_pct)
        return TradeResult(
            event.event_id, event.ticker, event.entry_date, entry_executed,
            idx[exit_loc], exit_executed, exit_loc - entry_loc,
            _net_return(entry_executed, exit_executed, commission_pct),
            reason, None, None, None,
        )

    # ---- stop_target 规则 ----
    atr = _atr_at(df, entry_loc)
    if atr is None or atr <= 0:
        # ATR 不足，退化为 hold
        exit_loc = min(entry_loc + max_hold, len(idx) - 1)
        exit_executed = float(df["close"].iloc[exit_loc]) * (1 - slippage_pct)
        return TradeResult(
            event.event_id, event.ticker, event.entry_date, entry_executed,
            idx[exit_loc], exit_executed, exit_loc - entry_loc,
            _net_return(entry_executed, exit_executed, commission_pct),
            "time", None, None, None,
        )

    risk = atr_mult * atr
    stop = entry_executed - risk
    target = entry_executed + target_R * risk  # 2:1 对齐 screen_trend_plan

    end = min(entry_loc + 1 + max_hold, len(idx))
    for i in range(entry_loc + 1, end):
        o = float(df["open"].iloc[i])
        h = float(df["high"].iloc[i])
        l = float(df["low"].iloc[i])
        # open gap 优先（跳空开盘按开盘价成交）
        if o <= stop:
            ex = o * (1 - slippage_pct)
            reason: ExitReason = "stop_gap"
        elif o >= target:
            ex = o * (1 - slippage_pct)
            reason = "target_gap"
        elif l <= stop:
            ex = stop * (1 - slippage_pct)
            reason = "stop"
        elif h >= target:
            ex = target * (1 - slippage_pct)
            reason = "target"
        else:
            continue
        return TradeResult(
            event.event_id, event.ticker, event.entry_date, entry_executed,
            idx[i], ex, i - entry_loc,
            _net_return(entry_executed, ex, commission_pct),
            reason, atr, stop, target,
        )

    # 超时未触及 → time exit（数据末尾则 censored）
    exit_loc = entry_loc + max_hold
    if exit_loc >= len(idx):
        exit_loc = len(idx) - 1
        reason = "censored"
    else:
        reason = "time"
    exit_executed = float(df["close"].iloc[exit_loc]) * (1 - slippage_pct)
    return TradeResult(
        event.event_id, event.ticker, event.entry_date, entry_executed,
        idx[exit_loc], exit_executed, exit_loc - entry_loc,
        _net_return(entry_executed, exit_executed, commission_pct),
        reason, atr, stop, target,
    )


def simulate_batch(
    events: list[SignalEvent],
    price_lookup: dict[str, pd.DataFrame],
    exit_rule: ExitRule = "stop_target",
    **kwargs,
) -> list[TradeResult]:
    """批量模拟。price_lookup 的 key 必须与 event.ticker 一致。"""
    results: list[TradeResult] = []
    for ev in events:
        df = price_lookup.get(ev.ticker)
        if df is None or df.empty:
            results.append(TradeResult(
                ev.event_id, ev.ticker, ev.entry_date, None, None, None,
                0, None, "no_data", None, None, None,
            ))
            continue
        results.append(simulate_trade(ev, df, exit_rule=exit_rule, **kwargs))
    return results


def summarize(trades: list[TradeResult], label: str = "") -> dict:
    """聚合统计：胜率 / EV / 盈亏比 / 退出原因分布 / 平均持有天数。"""
    completed = [t for t in trades if t.return_pct is not None and t.exit_reason in
                 ("target", "target_gap", "stop", "stop_gap", "sell_signal", "time")]
    censored = [t for t in trades if t.exit_reason == "censored"]
    no_data = [t for t in trades if t.exit_reason == "no_data"]

    rets = np.array([t.return_pct for t in completed])
    n = len(rets)
    base = {
        "label": label,
        "n_total": len(trades),
        "n_completed": n,
        "n_censored": len(censored),
        "n_no_data": len(no_data),
    }
    if n == 0:
        return {**base, "note": "无完成交易"}

    wins = rets[rets > 0]
    losses = rets[rets < 0]
    reasons: dict[str, int] = {}
    for t in completed:
        reasons[t.exit_reason] = reasons.get(t.exit_reason, 0) + 1

    return {
        **base,
        "win_rate": float(len(wins) / n),
        "ev": float(rets.mean()),
        "median": float(np.median(rets)),
        "avg_win": float(wins.mean()) if len(wins) else 0.0,
        "avg_loss": float(losses.mean()) if len(losses) else 0.0,
        "profit_factor": float(wins.sum() / abs(losses.sum())) if losses.sum() != 0 else float("inf"),
        "avg_hold_days": float(np.mean([t.hold_days for t in completed])),
        "exit_reasons": reasons,
    }
