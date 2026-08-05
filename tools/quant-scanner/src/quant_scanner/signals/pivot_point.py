"""中枢点（Pivot Point）精确识别 — Minervini SEPA 最后的最小收缩处

来源：《股票魔法师 Ⅱ》第 6 章 — "号召行动"的价格水平，最佳买点。

中枢点 4 个必备特征：
1. **价格收缩**：最后一次收缩幅度 ≤ 5%（理想 ≤ 3%）
2. **量能枯竭**：基底最后 5 天内，至少 1 天成交量 ≤ 50 日均量 × 50%
3. **极值量**：至少 1 天成交量接近整个基底的最低量（在最低量的 120% 以内）
4. **位置**：中枢点价格在基底最高点附近（≤ 5% 距离）

操作规则（信号触发）：
- **买入**：当日收盘价 > 中枢点上沿（基底最高点）+ 当日量能 ≥ 50 日均量 × 150%
- **不追涨**：未突破不触发，回踩或等下一个 VCP
"""
from __future__ import annotations

import pandas as pd

from .base import BaseSignal, SignalResult


class PivotPointSignal(BaseSignal):
    """Minervini 中枢点突破信号"""
    name = "pivot_point"
    threshold = 0.70  # 4 个特征至少 3 个通过 + 突破触发

    # 中枢点形态阈值
    base_lookback: int = 60             # 基底观察窗口（≈ 12 周）
    last_shrink_max_pct: float = 0.05   # 最后收缩幅度 ≤ 5%
    pivot_vol_dry_ratio: float = 0.50   # 量能干涸：vol ≤ 50日均量 × 50%
    pivot_vol_extreme_ratio: float = 1.20  # 极值量：vol ≤ 基底最低量 × 120%
    pivot_max_dist_from_base_high: float = 0.05  # 距基底高点 ≤ 5%

    # 突破触发
    breakout_vol_ratio: float = 1.50    # 突破当日量能 ≥ 50日均量 × 150%

    def evaluate(self, ticker: str, df: pd.DataFrame) -> SignalResult:
        reasons: list[str] = []
        details: dict = {}

        min_required = max(self.base_lookback, 80)
        if len(df) < min_required:
            return SignalResult(
                ticker=ticker, signal_name=self.name, value=0.0, passed=False,
                details={"error": f"数据不足（需 {min_required} 行）"},
                reasons=[f"数据不足（需 ≥ {min_required} 行）"],
            )

        recent = df.tail(self.base_lookback).copy()
        close = recent["close"]
        high = recent["high"]
        low = recent["low"]
        volume = recent["volume"]

        last_close = close.iloc[-1]
        base_high = high.max()
        base_low = low.min()
        base_high_idx = high.idxmax()

        # 50 日均量（用全部 df 数据）
        vol_ma50 = df["volume"].rolling(50).mean().iloc[-1]
        last_vol = volume.iloc[-1]

        # ============ 1. 最后收缩幅度（中枢点上沿 = base_high，下沿 = 最近低点）============
        recent_low = low.tail(10).min()
        last_shrink = (base_high - recent_low) / base_high if base_high > 0 else 1.0
        c1 = last_shrink <= self.last_shrink_max_pct
        reasons.append(
            f"1. 最后收缩 {last_shrink*100:.1f}% {'✓' if c1 else '✗'}"
            f"（要求 ≤ {self.last_shrink_max_pct*100:.0f}%）"
        )

        # ============ 2. 量能枯竭：最后 5 天内至少 1 天 vol ≤ 50日均量 × 50% ============
        last5_vol = volume.tail(5)
        dry_threshold = vol_ma50 * self.pivot_vol_dry_ratio
        dry_days = (last5_vol <= dry_threshold).sum()
        c2 = bool(dry_days >= 1)
        reasons.append(
            f"2. 量能干涸 {'✓' if c2 else '✗'}（最后 5 天 {dry_days} 天 ≤ 50日均量×50%"
            f"={dry_threshold:.0f}）"
        )

        # ============ 3. 极值量：至少 1 天 vol ≤ 基底最低量 × 120% ============
        base_min_vol = volume.min()
        extreme_threshold = base_min_vol * self.pivot_vol_extreme_ratio
        extreme_days = (last5_vol <= extreme_threshold).sum()
        c3 = bool(extreme_days >= 1)
        reasons.append(
            f"3. 极值量 {'✓' if c3 else '✗'}（最后 5 天 {extreme_days} 天 ≤ 基底最低量×120%"
            f"={extreme_threshold:.0f}）"
        )

        # ============ 4. 位置：当前价距基底高点 ≤ 5% ============
        dist_to_high = (last_close - base_high) / base_high
        c4 = abs(dist_to_high) <= self.pivot_max_dist_from_base_high
        reasons.append(
            f"4. 位置 {'✓' if c4 else '✗'}（距基底高点 {dist_to_high*100:+.1f}%，"
            f"要求 |x| ≤ {self.pivot_max_dist_from_base_high*100:.0f}%）"
        )

        # ============ 5. 突破触发：当日收盘 > 基底高点 + 量能 ≥ 50日均量×150% ============
        breakout_price = last_close > base_high * 0.999  # 容差 0.1%
        breakout_vol = last_vol >= vol_ma50 * self.breakout_vol_ratio
        c5_breakout = bool(breakout_price and breakout_vol)
        reasons.append(
            f"5. 突破触发 {'✓' if c5_breakout else '✗'}"
            f"（收盘 {last_close:.2f} {'>' if breakout_price else '≤'} 基底高 {base_high:.2f}"
            f"，量比 {last_vol/vol_ma50:.2f}{' ≥ ' if breakout_vol else ' < '}1.50）"
        )

        # ============ 评分 ============
        # 4 个形态特征各 20% + 突破触发 20%
        conditions = [c1, c2, c3, c4]
        form_score = sum(conditions) / 4.0  # 4 项形态分
        # 突破触发作为单独 20%（未突破时形态分最高 0.8，符合"形态完成但等突破"语义）
        score = 0.20 * form_score * 4 + 0.20 * (1.0 if c5_breakout else 0.0)
        # 简化为：4 个 0.2 + 突破 0.2 = 最高 1.0
        score = 0.20 * c1 + 0.20 * c2 + 0.20 * c3 + 0.20 * c4 + 0.20 * c5_breakout
        passed = score >= self.threshold and c5_breakout  # 必须有突破触发才算 passed

        pivot_upper = base_high
        pivot_lower = recent_low
        details.update({
            "base_high": float(base_high),
            "base_low": float(base_low),
            "base_high_idx": str(base_high_idx),
            "pivot_upper": float(pivot_upper),
            "pivot_lower": float(pivot_lower),
            "stop_loss_price": float(pivot_lower * 0.97),  # 中枢点下沿 -3%
            "last_shrink_pct": float(last_shrink * 100),
            "dry_days_count": int(dry_days),
            "extreme_days_count": int(extreme_days),
            "dist_to_high_pct": float(dist_to_high * 100),
            "breakout_price": bool(breakout_price),
            "breakout_vol": bool(breakout_vol),
            "vol_ratio_last_vs_ma50": float(last_vol / vol_ma50) if vol_ma50 > 0 else 0,
            "conditions": {
                "c1_last_shrink": bool(c1),
                "c2_vol_dry": bool(c2),
                "c3_vol_extreme": bool(c3),
                "c4_position": bool(c4),
                "c5_breakout": bool(c5_breakout),
            },
            "n_passed": int(sum(conditions) + (1 if c5_breakout else 0)),
            "action": "BUY" if passed else ("READY" if form_score >= 0.75 else "WAIT"),
        })

        return SignalResult(
            ticker=ticker, signal_name=self.name, value=float(score), passed=bool(passed),
            details=details, reasons=reasons,
        ).clamp()
