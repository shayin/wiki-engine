"""swing 工具 — 局部极值检测 + 前置趋势判断

来源：Murphy《金融市场技术分析》Ch5 + Ch3。
多个信号（major_reversal / continuation / oscillator_timing）共用。
"""
from __future__ import annotations

import pandas as pd


def find_swings(close: pd.Series, window: int = 5) -> tuple[list[tuple[int, float]], list[tuple[int, float]]]:
    """找 swing highs / lows：严格局部极值（左右 window 内唯一最高/最低）。

    Args:
        close: 收盘价序列
        window: 左右各 N 根 K 线内必须唯一极值

    Returns:
        (highs, lows) — 每个 entry 是 (index_pos, price)
    """
    w = window
    highs: list[tuple[int, float]] = []
    lows: list[tuple[int, float]] = []
    if len(close) < 2 * w + 1:
        return highs, lows
    for i in range(w, len(close) - w):
        left = close.iloc[i - w: i]
        right = close.iloc[i + 1: i + w + 1]
        ci = close.iloc[i]
        if ci > left.max() and ci > right.max():
            highs.append((i, float(ci)))
        if ci < left.min() and ci < right.min():
            lows.append((i, float(ci)))
    return highs, lows


def prior_trend(
    close: pd.Series,
    min_days: int = 60,
    min_pct: float = 0.15,
    exclude_tail: int = 20,
) -> tuple[str, float]:
    """判断前置趋势方向（排除最后 exclude_tail 天的形态区）。

    Args:
        close: 收盘价序列
        min_days: 前置趋势最少天数
        min_pct: 前置趋势幅度阈值
        exclude_tail: 排除最后 N 天（形态区，避免污染趋势判定）

    Returns:
        ("up" | "down" | "none", pct) — pct 是 max/min - 1
    """
    if len(close) < min_days + exclude_tail:
        return "none", 0.0
    seg = close.iloc[-min_days - exclude_tail: -exclude_tail]
    if len(seg) < 10:
        return "none", 0.0
    hi = float(seg.max())
    lo = float(seg.min())
    pct = hi / lo - 1 if lo > 0 else 0.0
    if pct < min_pct:
        return "none", pct
    mid = len(seg) // 2
    hi_idx = int(seg.values.argmax())
    lo_idx = int(seg.values.argmin())
    if hi_idx > mid:
        return "up", pct
    if lo_idx > mid:
        return "down", pct
    return "none", pct
