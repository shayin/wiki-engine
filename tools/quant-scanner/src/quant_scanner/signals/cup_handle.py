"""杯柄形态（Cup with Handle）— O'Neil 精确定义版

来源：《笑傲股市》第 1 章 + 第 9 章 — O'Neil 7 大基础形态中最常见、最高胜率的形态。

形态严格定义（O'Neil 原书 + IBD 50 年实践）：
1. **前提**：前期已有一波显著上涨（≥ 30%，典型 100%+），形成"杯子的左沿"
2. **杯部分**：
   - 杯深度：12%-35%（大盘股偏浅 12-20%，小盘股可至 35%）
   - 杯底形状：U 型圆底（V 型不算）
   - 杯右沿高度 ≈ 杯左沿（差异 ≤ 10%）
3. **柄部分**：
   - 位于杯的上半部（柄低点 > 杯中点）
   - 柄深度 ≤ 15%（典型 8-12%）
   - 柄持续时间 1-2 周（5-15 个交易日）
   - 柄成交量萎缩（≤ 50 日均量）
4. **突破**：
   - 突破杯右沿（或柄上沿）
   - 突破日成交量 ≥ 50 日均量 × 150%

打分逻辑：5 个维度，cup_shape + handle_position + handle_depth + handle_vol + breakout
"""
from __future__ import annotations

import logging
from typing import Optional

import pandas as pd

from .base import BaseSignal, SignalResult

log = logging.getLogger(__name__)


class CupHandleSignal(BaseSignal):
    name = "cup_handle"
    threshold = 0.70

    # 杯参数
    min_prior_runup: float = 0.30        # 前期涨幅 ≥ 30%
    min_cup_depth: float = 0.08          # 杯深下限（< 8% 不算形态）
    max_cup_depth: float = 0.35          # 杯深上限（> 35% 太深）
    ideal_cup_depth: float = 0.20        # 理想杯深 20%
    cup_shallow_threshold: float = 0.15  # 浅杯阈值（大盘股标准）
    rim_tolerance: float = 0.10          # 左右沿高度差异容忍 10%

    # 柄参数
    handle_max_depth: float = 0.15       # 柄深度 ≤ 15%
    handle_min_bars: int = 5             # 柄最少 5 个交易日
    handle_max_bars: int = 20            # 柄最多 20 个交易日
    handle_position_ratio: float = 0.50  # 柄必须位于杯上半部
    handle_vol_threshold: float = 1.0    # 柄均量 ≤ 50 日均量

    # 突破参数
    breakout_vol_mult: float = 1.50      # 突破日量 ≥ 50 日均量 × 1.5
    lookback_bars: int = 252             # 一年窗口

    def evaluate(self, ticker: str, df: pd.DataFrame) -> SignalResult:
        reasons: list[str] = []
        details: dict = {}

        if len(df) < 60:
            return SignalResult(
                ticker=ticker, signal_name=self.name, value=0.0, passed=False,
                details={"error": "数据不足"}, reasons=["数据不足 < 60 bar"],
            )

        try:
            close = df["close"]
            high = df["high"]
            low = df["low"]
            volume = df["volume"]

            # 取近一年窗口
            window_n = min(len(close), self.lookback_bars)
            window = close.tail(window_n)

            # 左沿必须在窗口前 70%（突破应在最后 30% 内发生）
            # 排除最后的突破段，找前期高点
            left_region_end = int(len(window) * 0.70)
            left_region = window.iloc[:left_region_end]
            if len(left_region) < 30:
                reasons.append("左沿区段数据不足 30 bar")
                return SignalResult(
                    ticker=ticker, signal_name=self.name, value=0.0, passed=False,
                    details=details, reasons=reasons,
                ).clamp()

            left_rim_idx = left_region.idxmax()
            left_rim_price = float(left_region.max())
            left_rim_pos = window.index.get_loc(left_rim_idx)

            # 前期涨幅：从窗口起点到左沿
            prior_price = float(window.iloc[0])
            prior_runup = (left_rim_price - prior_price) / prior_price if prior_price > 0 else 0
            details["prior_runup_pct"] = float(prior_runup * 100)

            if prior_runup < self.min_prior_runup:
                reasons.append(f"前期涨幅 {prior_runup*100:.1f}% < {self.min_prior_runup*100:.0f}% ✗")
                return SignalResult(
                    ticker=ticker, signal_name=self.name, value=0.0, passed=False,
                    details=details, reasons=reasons,
                ).clamp()

            # 左沿之后的部分
            after_left = window.iloc[left_rim_pos:]
            if len(after_left) < 30:
                reasons.append("左沿后数据不足 30 bar")
                return SignalResult(
                    ticker=ticker, signal_name=self.name, value=0.0, passed=False,
                    details=details, reasons=reasons,
                ).clamp()

            # 找杯底：左沿之后的最低点
            cup_bottom_pos = int(after_left.idxmin() == after_left.index.min())  # dummy
            cup_bottom_idx = after_left.idxmin()
            cup_bottom_price = float(after_left.min())
            cup_bottom_loc = after_left.index.get_loc(cup_bottom_idx)

            cup_depth = (left_rim_price - cup_bottom_price) / left_rim_price
            details["cup_depth_pct"] = float(cup_depth * 100)

            # 维度 1：杯深度评分
            if cup_depth < self.min_cup_depth:
                cup_shape_score = 0.0
                reasons.append(f"杯深 {cup_depth*100:.1f}% 过浅 ✗")
            elif cup_depth > self.max_cup_depth:
                cup_shape_score = 0.0
                reasons.append(f"杯深 {cup_depth*100:.1f}% 过深 ✗")
            else:
                # 越接近理想杯深 20% 越满分
                cup_shape_score = max(0.5, 1.0 - abs(cup_depth - self.ideal_cup_depth) / 0.15)
                # U 型检查（简化）：杯底附近 5 日内波动小 = 圆底
                bottom_window = after_left.iloc[max(0, cup_bottom_loc - 3):cup_bottom_loc + 4]
                if len(bottom_window) >= 5:
                    bottom_volatility = (bottom_window.max() - bottom_window.min()) / bottom_window.min()
                    if bottom_volatility < 0.05:  # 底部波动 < 5% = U 型
                        cup_shape_score = min(1.0, cup_shape_score + 0.1)
                        reasons.append(f"杯深 {cup_depth*100:.1f}% ✓ U 型圆底 ✓")
                    else:
                        reasons.append(f"杯深 {cup_depth*100:.1f}% ✓（底部波动 {bottom_volatility*100:.1f}% 偏 V）")
                else:
                    reasons.append(f"杯深 {cup_depth*100:.1f}% ✓")

            # 杯右沿 = 杯底后回升的第一个局部高点（之后会出现柄回撤）
            # 算法：从底向前扫描，找第一个 ≥ 95% 左沿的高点，再确认其后有回撤
            after_bottom = after_left.iloc[cup_bottom_loc:]
            if len(after_bottom) < 5:
                reasons.append("杯底后数据不足")
                return SignalResult(
                    ticker=ticker, signal_name=self.name, value=0.0, passed=False,
                    details=details, reasons=reasons,
                ).clamp()

            right_rim_loc = self._find_first_peak_with_pullback(after_bottom, left_rim_price)
            if right_rim_loc is None:
                # 没找到带回撤的高点，直接用最大值
                right_rim_loc = int(after_bottom.values.argmax())
            right_rim_price = float(after_bottom.iloc[right_rim_loc])

            rim_diff = abs(right_rim_price - left_rim_price) / left_rim_price
            details["rim_diff_pct"] = float(rim_diff * 100)
            if rim_diff > self.rim_tolerance:
                cup_shape_score *= 0.5
                reasons.append(f"杯左右沿差 {rim_diff*100:.1f}% > {self.rim_tolerance*100:.0f}% ✗")

            # 维度 2 + 3 + 4：柄部分
            handle_region = after_bottom.iloc[right_rim_loc:]
            handle_score, handle_pos_score, handle_vol_score, handle_details = self._evaluate_handle(
                handle_region, volume, left_rim_price, cup_bottom_price
            )
            details.update(handle_details)

            # 维度 5：突破
            breakout_score, breakout_details = self._evaluate_breakout(
                handle_region, volume, right_rim_price
            )
            details.update(breakout_details)

            # 综合：杯 30% + 柄位置 20% + 柄深度 20% + 柄量能 15% + 突破 15%
            score = (
                0.30 * cup_shape_score
                + 0.20 * handle_pos_score
                + 0.20 * handle_score
                + 0.15 * handle_vol_score
                + 0.15 * breakout_score
            )

            details["cup_left_rim"] = float(left_rim_price)
            details["cup_right_rim"] = float(right_rim_price)
            details["cup_bottom"] = float(cup_bottom_price)
            details["scores"] = {
                "cup_shape": float(cup_shape_score),
                "handle_position": float(handle_pos_score),
                "handle_depth": float(handle_score),
                "handle_vol": float(handle_vol_score),
                "breakout": float(breakout_score),
            }

            passed = score >= self.threshold
            reasons.append(
                f"综合评分 {score:.2f}（杯 {cup_shape_score:.1f}/柄位 {handle_pos_score:.1f}"
                f"/柄深 {handle_score:.1f}/柄量 {handle_vol_score:.1f}/突破 {breakout_score:.1f}）"
                f"{'✓' if passed else '✗'}"
            )

            return SignalResult(
                ticker=ticker, signal_name=self.name, value=float(score), passed=bool(passed),
                details=details, reasons=reasons,
            ).clamp()

        except Exception as e:
            log.warning(f"杯柄评估失败 {ticker}: {e}")
            return SignalResult(
                ticker=ticker, signal_name=self.name, value=0.0, passed=False,
                details={"error": str(e)}, reasons=[f"评估错误: {e}"],
            )

    def _find_first_peak_with_pullback(
        self, series: pd.Series, left_rim_price: float, min_pullback: float = 0.03
    ) -> Optional[int]:
        """找杯底后第一个显著高点（其后续有 ≥ 3% 回撤 = 柄开始）

        Args:
            series: 杯底之后的 close 序列
            left_rim_price: 杯左沿价格
            min_pullback: 高点后最小回撤幅度

        Returns:
            右沿在 series 中的位置，找不到返回 None
        """
        if len(series) < 5:
            return None

        # 找所有接近左沿 95% 的位置（候选右沿）
        threshold = left_rim_price * 0.95  # 至少回到左沿的 95%
        running_max = -float("inf")
        running_max_loc = 0

        for i in range(len(series)):
            v = float(series.iloc[i])
            if v > running_max:
                running_max = v
                running_max_loc = i

            # 当价格接近左沿水平后，检查后续是否回撤
            if running_max >= threshold and i > running_max_loc:
                # 检查后续 5-15 bar 内是否回撤 ≥ 3%
                future = series.iloc[i:i + 15]
                if len(future) >= 3:
                    pullback = (running_max - float(future.min())) / running_max
                    if pullback >= min_pullback:
                        return running_max_loc

        return None

    def _evaluate_handle(
        self,
        handle_region: pd.Series,
        volume: pd.Series,
        left_rim_price: float,
        cup_bottom_price: float,
    ) -> tuple[float, float, float, dict]:
        """评估柄部分

        Returns:
            (handle_depth_score, handle_position_score, handle_vol_score, details)
        """
        details: dict = {}
        cup_midpoint = (left_rim_price + cup_bottom_price) / 2

        if len(handle_region) < self.handle_min_bars:
            details["handle_bars"] = len(handle_region)
            return 0.0, 0.0, 0.0, details

        # 限制柄长度
        handle_n = min(len(handle_region), self.handle_max_bars)
        handle = handle_region.iloc[:handle_n]

        handle_high = float(handle.max())
        handle_low = float(handle.min())
        handle_top = handle.iloc[0]  # 柄起点 ≈ 杯右沿
        handle_depth = (handle_top - handle_low) / handle_top if handle_top > 0 else 0
        handle_low_vs_left = handle_low / left_rim_price  # 柄低点相对左沿

        details["handle_bars"] = int(handle_n)
        details["handle_depth_pct"] = float(handle_depth * 100)
        details["handle_low_vs_left_rim"] = float(handle_low_vs_left)

        # 柄深度评分
        if handle_depth <= 0:
            handle_depth_score = 0.5  # 无柄
        elif handle_depth > self.handle_max_depth:
            handle_depth_score = max(0.0, 0.5 - (handle_depth - self.handle_max_depth) * 5)
        else:
            handle_depth_score = 1.0  # 理想深度区间

        # 柄位置评分：柄低点 > 杯中点 = 满分
        if handle_low > cup_midpoint:
            handle_position_score = 1.0
        else:
            # 柄在下半部 = 不合格
            handle_position_score = max(0.0, (handle_low - cup_bottom_price) / (cup_midpoint - cup_bottom_price + 1e-9))

        # 柄成交量评分：萎缩 = 满分
        handle_vol_idx = handle.index
        try:
            vol_ma50 = volume.rolling(50).mean()
            handle_vol = volume.loc[handle_vol_idx]
            avg_handle_vol = float(handle_vol.mean())
            recent_vol_ma = float(vol_ma50.loc[handle_vol_idx].mean())
            if recent_vol_ma > 0:
                vol_ratio = avg_handle_vol / recent_vol_ma
                details["handle_vol_ratio"] = float(vol_ratio)
                if vol_ratio <= self.handle_vol_threshold:
                    handle_vol_score = 1.0
                else:
                    handle_vol_score = max(0.0, 1.0 - (vol_ratio - 1.0) * 2)
            else:
                handle_vol_score = 0.5
        except Exception:
            handle_vol_score = 0.5

        return handle_depth_score, handle_position_score, handle_vol_score, details

    def _evaluate_breakout(
        self,
        handle_region: pd.Series,
        volume: pd.Series,
        right_rim_price: float,
    ) -> tuple[float, dict]:
        """评估突破：检查最近 5 日是否突破杯右沿"""
        details: dict = {}
        if len(handle_region) < 1:
            return 0.0, details

        # 取最近 5 日
        last5 = handle_region.tail(5)
        last5_vol = volume.loc[last5.index]
        vol_ma50 = volume.rolling(50).mean().loc[last5.index]

        # 最近 5 日是否突破右沿
        breakout_bars = (last5 > right_rim_price * 1.0001).sum()
        breakout_today = last5.iloc[-1] > right_rim_price * 1.0001

        # 突破日量能
        today_vol = float(last5_vol.iloc[-1])
        avg_vol = float(vol_ma50.iloc[-1]) if not pd.isna(vol_ma50.iloc[-1]) else 0
        vol_ratio = today_vol / avg_vol if avg_vol > 0 else 0

        details["breakout_bars_5d"] = int(breakout_bars)
        details["breakout_today"] = bool(breakout_today)
        details["breakout_vol_ratio"] = float(vol_ratio)

        if breakout_bars == 0:
            return 0.0, details

        # 突破 + 放量 = 满分
        if vol_ratio >= self.breakout_vol_mult:
            score = 1.0
        elif vol_ratio >= 1.0:
            score = 0.7
        else:
            score = 0.3

        return score, details
