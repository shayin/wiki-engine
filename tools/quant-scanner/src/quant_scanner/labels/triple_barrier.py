"""Triple-Barrier Labeling — López de Prado AfML 第 3 章

来源：
- Marcos López de Prado (2018) *Advances in Financial Machine Learning*, Ch 3
- 原始论文：López de Prado (2013) "Flow-Driven Risk in QM"

**核心思想**：
传统固定 horizon 标签（"未来 5 日收益正负"）在波动率变化时不稳定。
Triple-Barrier 用 **止盈 + 止损 + 时间** 三道屏障决定标签：

1. **上屏障（止盈）**：entry × (1 + tp_width)
2. **下屏障（止损）**：entry × (1 - sl_width)
3. **垂直屏障（时间）**：entry_index + vertical_barrier_bars

每天检查高/低价是否穿越屏障，**第一个被触碰的屏障决定标签**：
- 上屏障先碰 → +1（做多正确）
- 下屏障先碰 → -1（做空正确 / 做多错误）
- 垂直屏障到 → 收盘位置决定（高于入场 +1，低于 -1，否则 0）

**波动率自适应**：
屏障宽度可用 ATR（平均真实波幅）的倍数设置 → 自动适应当前波动率：
- 高波动 → 屏障变宽（防假突破）
- 低波动 → 屏障变窄（快速确认）

**Meta-Labeling 关系**：
Triple-barrier 输出的 label 是 meta-labeling 的输入（任务 #96）。
二分器（+1/-1 → 1/0）让二级分类器输出 confidence（建议仓位 size）。

**实践意义**：
- 是 AfML 全套 ML 流水线（特征→标签→模型→CV）的基础
- 替代 `factors/forward_returns.py` 的简单 fixed-horizon 标签
- Triple-Barrier 标签更稳定，训练出的 ML 模型泛化更好

**典型用法**：
```python
from quant_scanner.labels import triple_barrier_labels

labels = triple_barrier_labels(
    df,
    tp_width=2.0,        # 止盈 = 2 × ATR
    sl_width=2.0,        # 止损 = 2 × ATR
    atr_window=20,
    vertical_barrier_bars=10,
    side=+1,             # 已知做多方向
)
# labels 是 DataFrame，含 t_in, t_out, label, ret, barrier_hit
```
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd


@dataclass
class TripleBarrierResult:
    """单个 triple-barrier 标签结果"""
    t_in: pd.Timestamp          # 入场时间
    t_out: pd.Timestamp         # 出场时间（碰到屏障的时刻）
    entry_price: float          # 入场价
    exit_price: float           # 出场价
    label: int                  # +1 / 0 / -1
    barrier_hit: str            # 'tp' / 'sl' / 'vb'
    ret: float                  # 出场相对入场收益（带 side 符号）
    side: int                   # +1 做多 / -1 做空


def _atr(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    window: int = 20,
) -> pd.Series:
    """Average True Range（Wilder smoothing）"""
    prev_close = close.shift(1)
    tr = pd.concat([
        (high - low).abs(),
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    # Wilder smoothing ≈ EMA with alpha=1/window
    return tr.ewm(alpha=1.0 / window, adjust=False).mean()


def add_atr_barriers(
    df: pd.DataFrame,
    tp_atr_mult: float = 2.0,
    sl_atr_mult: float = 2.0,
    atr_window: int = 20,
    side: int | pd.Series = 1,
) -> pd.DataFrame:
    """根据 ATR 计算每个时点的动态止盈/止损价

    Args:
        df: OHLCV DataFrame（需含 high/low/close 列）
        tp_atr_mult: 止盈 = entry ± mult × ATR（带 side 符号）
        sl_atr_mult: 止损 = entry ∓ mult × ATR（带 side 符号）
        atr_window: ATR 滑动窗口
        side: +1 做多 / -1 做空 / pd.Series 时序方向

    Returns:
        原 df 加列：'atr', 'tp_price', 'sl_price'
    """
    required = {"high", "low", "close"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"df 缺少列: {missing}")

    atr = _atr(df["high"], df["low"], df["close"], window=atr_window)

    # side 可以是标量或 Series
    if isinstance(side, pd.Series):
        side_s = side.reindex(df.index).fillna(1).astype(int)
        sign = side_s.where(side_s >= 0, -1).astype(float)
    else:
        sign = 1.0 if side >= 0 else -1.0

    # 做多：tp 上方(+), sl 下方(-)；做空：tp 下方(-), sl 上方(+)
    tp_price = df["close"] + sign * tp_atr_mult * atr
    sl_price = df["close"] - sign * sl_atr_mult * atr

    out = df.copy()
    out["atr"] = atr
    out["tp_price"] = tp_price
    out["sl_price"] = sl_price
    return out


def _label_one_path(
    idx_in: int,
    df: pd.DataFrame,
    side: int,
    vertical_barrier_bars: int | None,
) -> TripleBarrierResult:
    """从 idx_in 开始模拟持仓，返回第一个被触碰的屏障"""
    entry_price = df["close"].iloc[idx_in]
    tp_price = df["tp_price"].iloc[idx_in]
    sl_price = df["sl_price"].iloc[idx_in]
    t_in = df.index[idx_in]

    # 垂直屏障索引
    if vertical_barrier_bars is not None:
        idx_vb = min(idx_in + vertical_barrier_bars, len(df) - 1)
    else:
        idx_vb = len(df) - 1

    # 遍历持仓期间，检查每日高/低
    for i in range(idx_in + 1, idx_vb + 1):
        hi = df["high"].iloc[i]
        lo = df["low"].iloc[i]

        # 判断 tp/sl 是否被当日触及
        if side >= 0:
            tp_touched = hi >= tp_price
            sl_touched = lo <= sl_price
        else:
            tp_touched = lo <= tp_price
            sl_touched = hi >= sl_price

        # 同时碰到两个屏障 → 保守起见优先止损
        if sl_touched:
            exit_price = sl_price
            return TripleBarrierResult(
                t_in=t_in, t_out=df.index[i],
                entry_price=float(entry_price), exit_price=float(exit_price),
                label=-1, barrier_hit="sl",
                ret=float(side * (exit_price - entry_price) / entry_price),
                side=side,
            )
        if tp_touched:
            exit_price = tp_price
            return TripleBarrierResult(
                t_in=t_in, t_out=df.index[i],
                entry_price=float(entry_price), exit_price=float(exit_price),
                label=+1, barrier_hit="tp",
                ret=float(side * (exit_price - entry_price) / entry_price),
                side=side,
            )

    # 时间到（垂直屏障）
    exit_price = df["close"].iloc[idx_vb]
    # vb 的 label：按收盘相对入场价的位置决定（带 side）
    if side >= 0:
        label = +1 if exit_price > entry_price else (-1 if exit_price < entry_price else 0)
    else:
        label = +1 if exit_price < entry_price else (-1 if exit_price > entry_price else 0)
    return TripleBarrierResult(
        t_in=t_in, t_out=df.index[idx_vb],
        entry_price=float(entry_price), exit_price=float(exit_price),
        label=label, barrier_hit="vb",
        ret=float(side * (exit_price - entry_price) / entry_price),
        side=side,
    )


class TripleBarrierLabeler:
    """Triple-Barrier 标签生成器

    用法：
    ```python
    labeler = TripleBarrierLabeler(
        tp_atr_mult=2.0, sl_atr_mult=2.0,
        atr_window=20, vertical_barrier_bars=10,
    )
    labels = labeler.fit_transform(df, side=+1)
    ```
    """

    def __init__(
        self,
        tp_atr_mult: float = 2.0,
        sl_atr_mult: float = 2.0,
        atr_window: int = 20,
        vertical_barrier_bars: int | None = 10,
    ) -> None:
        self.tp_atr_mult = tp_atr_mult
        self.sl_atr_mult = sl_atr_mult
        self.atr_window = atr_window
        self.vertical_barrier_bars = vertical_barrier_bars

    def fit_transform(
        self,
        df: pd.DataFrame,
        side: int | pd.Series = 1,
        entry_indices: list[int] | None = None,
    ) -> pd.DataFrame:
        """对 df 生成 triple-barrier 标签

        Args:
            df: OHLCV DataFrame
            side: +1 做多 / -1 做空 / pd.Series 时序方向（用于 meta-labeling）
            entry_indices: 指定入场点索引列表；None 则每个时点都入场

        Returns:
            DataFrame，含列：
            - t_in, t_out, entry_price, exit_price
            - label (+1/0/-1), barrier_hit (tp/sl/vb), ret, side
        """
        # 一次性按 side 计算屏障（支持 Series 形式的时序 side）
        df_with_barriers = add_atr_barriers(
            df,
            tp_atr_mult=self.tp_atr_mult,
            sl_atr_mult=self.sl_atr_mult,
            atr_window=self.atr_window,
            side=side,
        )

        n = len(df_with_barriers)
        if entry_indices is None:
            vb = self.vertical_barrier_bars or 1
            # 序列短于 vb 时，至少留 1 个入场点
            vb = min(vb, max(n - 1, 1))
            entry_indices = list(range(max(n - vb, 1)))

        if isinstance(side, pd.Series):
            side_series = side.reindex(df_with_barriers.index).fillna(1).astype(int)
        else:
            sign_int = 1 if side >= 0 else -1
            side_series = pd.Series([sign_int] * n, index=df_with_barriers.index)

        results: list[TripleBarrierResult] = []
        for idx_in in entry_indices:
            if idx_in < 0 or idx_in >= n - 1:
                continue
            current_side = int(side_series.iloc[idx_in])
            r = _label_one_path(
                idx_in=idx_in,
                df=df_with_barriers,
                side=current_side,
                vertical_barrier_bars=self.vertical_barrier_bars,
            )
            results.append(r)

        return pd.DataFrame([{
            "t_in": r.t_in,
            "t_out": r.t_out,
            "entry_price": r.entry_price,
            "exit_price": r.exit_price,
            "label": r.label,
            "barrier_hit": r.barrier_hit,
            "ret": r.ret,
            "side": r.side,
        } for r in results])


def triple_barrier_labels(
    df: pd.DataFrame,
    tp_atr_mult: float = 2.0,
    sl_atr_mult: float = 2.0,
    atr_window: int = 20,
    vertical_barrier_bars: int = 10,
    side: int | pd.Series = 1,
    entry_indices: list[int] | None = None,
) -> pd.DataFrame:
    """快捷函数：生成 triple-barrier 标签

    Args:
        df: OHLCV DataFrame
        tp_atr_mult: 止盈 = mult × ATR
        sl_atr_mult: 止损 = mult × ATR
        atr_window: ATR 窗口
        vertical_barrier_bars: 时间屏障 bar 数
        side: +1 做多 / -1 做空 / pd.Series 时序方向
        entry_indices: 入场点索引列表

    Returns:
        标签 DataFrame，含 label/barrier_hit/ret/side
    """
    labeler = TripleBarrierLabeler(
        tp_atr_mult=tp_atr_mult,
        sl_atr_mult=sl_atr_mult,
        atr_window=atr_window,
        vertical_barrier_bars=vertical_barrier_bars,
    )
    return labeler.fit_transform(df, side=side, entry_indices=entry_indices)
