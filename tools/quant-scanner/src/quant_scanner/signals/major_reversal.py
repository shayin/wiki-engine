"""Major Reversal Patterns — Murphy 主要反转形态

来源：John Murphy《金融市场技术分析》Ch5。
识别趋势末端的反转形态。第一版：双顶（M 头）/ 双底（W 底）—— 最常见、可量化。
头肩 / 三重 / 圆弧 / V 型 / 岛形留扩展（结构识别更复杂，见 HANDOFF P1.2）。

Murphy 6 要点：①前置趋势必须存在 ②颈线突破才确认 ③量能确认 ④形态越大越可靠。
突破有效性 3 条件：收盘突破 + 幅度 ≥ 3%（本实现用 1.5% 适配日线噪声）+ 量能 ≥ 150% 均量。

value 带符号：正 = 看多反转（双底），负 = 看空反转（双顶）。|value| ≥ 0.5 = 形态确认。
"""
from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import pandas as pd

from .base import BaseSignal, SignalResult
from ..utils.swing import find_swings, prior_trend

log = logging.getLogger(__name__)


class MajorReversalSignal(BaseSignal):
    """Murphy 双顶/双底反转形态。"""

    name = "major_reversal"
    threshold = 0.5  # |value| ≥ 0.5 = 形态确认

    # 前置趋势
    min_prior_trend_days: int = 60
    min_prior_trend_pct: float = 0.15  # 前置趋势幅度 ≥ 15%

    # swing 检测（左右 window 内极值）
    swing_window: int = 5

    # 双顶/底形态参数
    top_tolerance: float = 0.03    # 两顶/底价差 ≤ 3%
    min_dip_pct: float = 0.05      # 中间回调/反弹 ≥ 5%

    # 头肩形态参数
    shoulder_tolerance: float = 0.07   # 左右肩价差 ≤ 7%（比双顶宽容，因 H&S 三点对称要求较低）
    head_prominence: float = 0.02      # 头部相对肩部高出 ≥ 2%（避免三点等高被误判为 H&S）
    min_neckline_depth: float = 0.03   # 头到颈线深度 ≥ 3%

    # 突破有效性
    break_pct: float = 0.015       # 收盘跌破/突破颈线 ≥ 1.5%
    volume_ratio: float = 1.5      # 突破量能 ≥ 150% 均量
    volume_ma: int = 50

    # 三重顶/底参数
    triple_tolerance: float = 0.04    # 三个顶/底相互价差 ≤ 4%

    # 圆弧形态参数（Murphy：缓慢转向，1-6 月）
    rounding_min_bars: int = 40        # 圆弧至少 40 天
    rounding_max_bars: int = 150       # 最长 150 天
    rounding_curve_fit: float = 0.65   # 二次曲线 R² ≥ 0.65 才算圆弧

    # V 型反转参数（Murphy：急速反转，常伴缺口）
    v_reversal_min_pct: float = 0.20   # 前段跌幅 ≥ 20%
    v_reversal_max_bars: int = 25      # V 型左+右 ≤ 25 天（急剧）
    v_reversal_min_bars: int = 8       # 至少 8 天

    # 岛形反转参数（Murphy：缺口隔离）
    island_min_gap_pct: float = 0.01   # 缺口 ≥ 1%
    island_max_bars: int = 15          # 岛屿 ≤ 15 天

    # -------------------------- 形态检测 --------------------------

    def _detect_double_top(self, highs, lows, last_close):
        """双顶：最后两个 swing high 等高 + 中间回调 low + 当前跌破该 low。"""
        if len(highs) < 2 or len(lows) < 1:
            return None
        h2_idx, h2 = highs[-1]
        h1_idx, h1 = highs[-2]
        if h1_idx >= h2_idx:
            return None
        if abs(h1 - h2) / max(h1, h2) > self.top_tolerance:
            return None
        top = max(h1, h2)
        mid_lows = [(i, p) for i, p in lows if h1_idx < i < h2_idx]
        if not mid_lows:
            return None
        _, neckline = min(mid_lows, key=lambda x: x[1])
        dip = (top - neckline) / top
        if dip < self.min_dip_pct:
            return None
        break_amt = (neckline - last_close) / neckline if neckline > 0 else 0.0
        confirmed = break_amt >= self.break_pct
        target = neckline - (top - neckline)
        return {
            "type": "DOUBLE_TOP", "top": float(top), "neckline": float(neckline),
            "dip_pct": float(dip), "break_pct": float(max(break_amt, 0)),
            "confirmed": bool(confirmed), "target": float(target),
        }

    def _detect_double_bottom(self, highs, lows, last_close):
        """双底：最后两个 swing low 等低 + 中间反弹 high + 当前突破该 high。"""
        if len(lows) < 2 or len(highs) < 1:
            return None
        l2_idx, l2 = lows[-1]
        l1_idx, l1 = lows[-2]
        if l1_idx >= l2_idx:
            return None
        if abs(l1 - l2) / min(l1, l2) > self.top_tolerance:
            return None
        bottom = min(l1, l2)
        mid_highs = [(i, p) for i, p in highs if l1_idx < i < l2_idx]
        if not mid_highs:
            return None
        _, neckline = max(mid_highs, key=lambda x: x[1])
        rally = (neckline - bottom) / bottom
        if rally < self.min_dip_pct:
            return None
        break_amt = (last_close - neckline) / neckline if neckline > 0 else 0.0
        confirmed = break_amt >= self.break_pct
        target = neckline + (neckline - bottom)
        return {
            "type": "DOUBLE_BOTTOM", "bottom": float(bottom), "neckline": float(neckline),
            "rally_pct": float(rally), "break_pct": float(max(break_amt, 0)),
            "confirmed": bool(confirmed), "target": float(target),
        }

    # -------------------------- 头肩形态 --------------------------

    def _detect_head_and_shoulders_top(self, highs, lows, last_close):
        """头肩顶（Murphy Ch5）：

        结构：H1（左肩）< H2（头）> H3（右肩），H1≈H3
        颈线：L1（H1-H2 之间）、L2（H2-H3 之间）两个 swing low 连线
        确认：close 跌破颈线

        Returns dict 或 None
        """
        if len(highs) < 3 or len(lows) < 2:
            return None
        h1_idx, h1 = highs[-3]
        h2_idx, h2 = highs[-2]
        h3_idx, h3 = highs[-1]
        if not (h1_idx < h2_idx < h3_idx):
            return None
        # 头部必须是最高
        if not (h2 > h1 and h2 > h3):
            return None
        # 左右肩大致等高
        if abs(h1 - h3) / min(h1, h3) > self.shoulder_tolerance:
            return None
        # 头部突出 ≥ 2%
        if (h2 - max(h1, h3)) / max(h1, h3) < self.head_prominence:
            return None
        # 找 L1（H1 与 H2 之间）、L2（H2 与 H3 之间）
        l1_cands = [(i, p) for i, p in lows if h1_idx < i < h2_idx]
        l2_cands = [(i, p) for i, p in lows if h2_idx < i < h3_idx]
        if not l1_cands or not l2_cands:
            return None
        l1_idx, l1 = min(l1_cands, key=lambda x: x[1])
        l2_idx, l2 = min(l2_cands, key=lambda x: x[1])
        # 头部到颈线深度
        neckline = (l1 + l2) / 2  # 简化：取均值
        depth = (h2 - neckline) / h2
        if depth < self.min_neckline_depth:
            return None
        # 确认：当前 close 跌破颈线
        break_amt = (neckline - last_close) / neckline if neckline > 0 else 0.0
        confirmed = break_amt >= self.break_pct
        target = neckline - (h2 - neckline)  # Murphy 量度目标
        return {
            "type": "HEAD_SHOULDERS_TOP", "head": float(h2),
            "left_shoulder": float(h1), "right_shoulder": float(h3),
            "neckline": float(neckline), "neckline_points": [float(l1), float(l2)],
            "depth_pct": float(depth), "break_pct": float(max(break_amt, 0)),
            "confirmed": bool(confirmed), "target": float(target),
        }

    def _detect_head_and_shoulders_bottom(self, lows, highs, last_close):
        """头肩底（Inverse H&S）：

        结构：L1（左肩）> L2（头）< L3（右肩），L1≈L3
        颈线：H1（L1-L2 之间）、H2（L2-L3 之间）两个 swing high 连线
        确认：close 突破颈线
        """
        if len(lows) < 3 or len(highs) < 2:
            return None
        l1_idx, l1 = lows[-3]
        l2_idx, l2 = lows[-2]
        l3_idx, l3 = lows[-1]
        if not (l1_idx < l2_idx < l3_idx):
            return None
        if not (l2 < l1 and l2 < l3):
            return None
        if abs(l1 - l3) / min(l1, l3) > self.shoulder_tolerance:
            return None
        if (min(l1, l3) - l2) / l2 < self.head_prominence:
            return None
        h1_cands = [(i, p) for i, p in highs if l1_idx < i < l2_idx]
        h2_cands = [(i, p) for i, p in highs if l2_idx < i < l3_idx]
        if not h1_cands or not h2_cands:
            return None
        _, h1 = max(h1_cands, key=lambda x: x[1])
        _, h2 = max(h2_cands, key=lambda x: x[1])
        neckline = (h1 + h2) / 2
        depth = (neckline - l2) / l2 if l2 > 0 else 0
        if depth < self.min_neckline_depth:
            return None
        break_amt = (last_close - neckline) / neckline if neckline > 0 else 0.0
        confirmed = break_amt >= self.break_pct
        target = neckline + (neckline - l2)
        return {
            "type": "HEAD_SHOULDERS_BOTTOM", "head": float(l2),
            "left_shoulder": float(l1), "right_shoulder": float(l3),
            "neckline": float(neckline), "neckline_points": [float(h1), float(h2)],
            "depth_pct": float(depth), "break_pct": float(max(break_amt, 0)),
            "confirmed": bool(confirmed), "target": float(target),
        }

    # -------------------------- 三重顶/底 --------------------------

    def _detect_triple_top(self, highs, lows, last_close):
        """三重顶：最后三个 swing high 等高 + 颈线跌破。

        结构：H1≈H2≈H3（三触同阻力），中间两次回调的最低低点 = 颈线。
        """
        if len(highs) < 3 or len(lows) < 2:
            return None
        h1_idx, h1 = highs[-3]
        h2_idx, h2 = highs[-2]
        h3_idx, h3 = highs[-1]
        if not (h1_idx < h2_idx < h3_idx):
            return None
        # 三个顶相互价差 ≤ triple_tolerance
        tops = [h1, h2, h3]
        if (max(tops) - min(tops)) / max(tops) > self.triple_tolerance:
            return None
        top = sum(tops) / 3
        # 两次回调：H1-H2 间、H2-H3 间
        mid_lows = [(i, p) for i, p in lows if h1_idx < i < h3_idx]
        if len(mid_lows) < 2:
            return None
        _, neckline = min(mid_lows, key=lambda x: x[1])
        if (top - neckline) / top < self.min_dip_pct:
            return None
        break_amt = (neckline - last_close) / neckline if neckline > 0 else 0.0
        confirmed = break_amt >= self.break_pct
        target = neckline - (top - neckline)
        return {
            "type": "TRIPLE_TOP", "top": float(top), "neckline": float(neckline),
            "tops": [float(t) for t in tops],
            "dip_pct": float((top - neckline) / top),
            "break_pct": float(max(break_amt, 0)),
            "confirmed": bool(confirmed), "target": float(target),
        }

    def _detect_triple_bottom(self, lows, highs, last_close):
        """三重底：最后三个 swing low 等低 + 颈线突破。"""
        if len(lows) < 3 or len(highs) < 2:
            return None
        l1_idx, l1 = lows[-3]
        l2_idx, l2 = lows[-2]
        l3_idx, l3 = lows[-1]
        if not (l1_idx < l2_idx < l3_idx):
            return None
        bottoms = [l1, l2, l3]
        if (max(bottoms) - min(bottoms)) / min(bottoms) > self.triple_tolerance:
            return None
        bottom = sum(bottoms) / 3
        mid_highs = [(i, p) for i, p in highs if l1_idx < i < l3_idx]
        if len(mid_highs) < 2:
            return None
        _, neckline = max(mid_highs, key=lambda x: x[1])
        if (neckline - bottom) / bottom < self.min_dip_pct:
            return None
        break_amt = (last_close - neckline) / neckline if neckline > 0 else 0.0
        confirmed = break_amt >= self.break_pct
        target = neckline + (neckline - bottom)
        return {
            "type": "TRIPLE_BOTTOM", "bottom": float(bottom), "neckline": float(neckline),
            "bottoms": [float(b) for b in bottoms],
            "rally_pct": float((neckline - bottom) / bottom),
            "break_pct": float(max(break_amt, 0)),
            "confirmed": bool(confirmed), "target": float(target),
        }

    # -------------------------- 圆弧顶/底 --------------------------

    def _detect_rounding(self, close: pd.Series, last_close: float, trend_dir: str):
        """圆弧顶/底（Murphy Ch5）：缓慢转向。

        用最近 N 天 close 对时间索引做二次回归，根据二次项符号判定圆弧顶（concave down）/底（concave up）。
        R² ≥ rounding_curve_fit 视为圆弧拟合良好。
        """
        n = len(close)
        if n < self.rounding_min_bars:
            return None
        # 取最近窗口
        window = min(self.rounding_max_bars, n)
        segment = close.iloc[-window:].reset_index(drop=True)
        x = np.arange(len(segment))
        y = segment.values
        if len(y) < 10:
            return None
        # 二次多项式拟合
        try:
            coeffs = np.polyfit(x, y, 2)
        except (np.linalg.LinAlgError, ValueError):
            return None
        a, b, c = coeffs
        # R²
        y_pred = a * x**2 + b * x + c
        ss_res = np.sum((y - y_pred) ** 2)
        ss_tot = np.sum((y - np.mean(y)) ** 2)
        r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0
        if r2 < self.rounding_curve_fit:
            return None
        # a<0 = 圆弧顶（concave down），a>0 = 圆弧底（concave up）
        if a < 0 and trend_dir == "up":
            ptype = "ROUNDING_TOP"
            peak = float(np.max(y_pred))
            sign = -1.0
            # 确认：last_close 离峰值下跌 ≥ break_pct
            break_amt = (peak - last_close) / peak if peak > 0 else 0
            confirmed = break_amt >= self.break_pct
            target = last_close - (peak - last_close)  # 简单投射
        elif a > 0 and trend_dir == "down":
            ptype = "ROUNDING_BOTTOM"
            trough = float(np.min(y_pred))
            sign = 1.0
            break_amt = (last_close - trough) / trough if trough > 0 else 0
            confirmed = break_amt >= self.break_pct
            target = last_close + (last_close - trough)
        else:
            return None
        return {
            "type": ptype, "curve_a": float(a), "curve_b": float(b),
            "r_squared": float(r2), "bars": int(window),
            "extremum": float(peak if a < 0 else trough),
            "break_pct": float(max(break_amt, 0)),
            "confirmed": bool(confirmed),
            "target": float(target) if confirmed else None,
            "_sign": sign,
        }

    # -------------------------- V 型反转 --------------------------

    def _detect_v_reversal(self, close: pd.Series, last_close: float, trend_dir: str):
        """V 型反转（Murphy Ch5）：急速下跌 + 急速反弹（或反向）。

        在最近 v_reversal_max_bars 内查找极值点，前段跌幅 ≥ 20%，后段反弹 ≥ 50% 跌幅。
        """
        n = len(close)
        if n < self.v_reversal_min_bars:
            return None
        window = self.v_reversal_max_bars
        segment = close.iloc[-window:] if n >= window else close
        seg = segment.reset_index(drop=True)
        # 极值点
        if trend_dir == "down":
            # V 底：先跌后涨，找最低点
            trough_idx = int(np.argmin(seg.values))
            trough = float(seg.iloc[trough_idx])
            pre = seg.iloc[:trough_idx + 1]
            post = seg.iloc[trough_idx:]
            if len(pre) < 4 or len(post) < 4:
                return None
            pre_high = float(pre.max())
            drop = (pre_high - trough) / pre_high if pre_high > 0 else 0
            if drop < self.v_reversal_min_pct:
                return None
            post_high = float(post.max())
            recovery = (post_high - trough) / trough if trough > 0 else 0
            if recovery < drop * 0.5:
                return None
            ptype = "V_BOTTOM"
            sign = 1.0
            # 确认：last_close 越过中点
            mid = (pre_high + trough) / 2
            confirmed = last_close > mid
            target = trough + (pre_high - trough)
            extremum = trough
        elif trend_dir == "up":
            # V 顶（倒 V）：先涨后跌
            peak_idx = int(np.argmax(seg.values))
            peak = float(seg.iloc[peak_idx])
            pre = seg.iloc[:peak_idx + 1]
            post = seg.iloc[peak_idx:]
            if len(pre) < 4 or len(post) < 4:
                return None
            pre_low = float(pre.min())
            rally = (peak - pre_low) / pre_low if pre_low > 0 else 0
            if rally < self.v_reversal_min_pct:
                return None
            post_low = float(post.min())
            drop = (peak - post_low) / peak if peak > 0 else 0
            if drop < rally * 0.5:
                return None
            ptype = "V_TOP"
            sign = -1.0
            mid = (pre_low + peak) / 2
            confirmed = last_close < mid
            target = peak - (peak - pre_low)
            extremum = peak
        else:
            return None
        return {
            "type": ptype, "extremum": float(extremum),
            "pre_pct": float(rally if trend_dir == "up" else drop),
            "post_pct": float(drop if trend_dir == "up" else recovery),
            "confirmed": bool(confirmed),
            "target": float(target) if confirmed else None,
            "_sign": sign,
        }

    # -------------------------- 岛形反转 --------------------------

    def _detect_island_reversal(self, df: pd.DataFrame, trend_dir: str):
        """岛形反转（Murphy Ch5）：两个反向缺口隔离出岛屿。

        上涨趋势 → 顶部竭尽缺口 + 反转下跌缺口 = 岛形顶
        下跌趋势 → 底部竭尽缺口 + 反转上涨缺口 = 岛形底
        """
        n = len(df)
        if n < self.island_max_bars + 5:
            return None
        recent = df.iloc[-(self.island_max_bars + 5):].reset_index(drop=True)
        high = recent["high"].values
        low = recent["low"].values
        # 找所有缺口（今日 low > 昨日 high = 上跳；今日 high < 昨日 low = 下跳）
        up_gaps = []
        down_gaps = []
        for i in range(1, len(recent)):
            if low[i] > high[i-1] * (1 + self.island_min_gap_pct):
                up_gaps.append((i, low[i], high[i-1]))
            elif high[i] < low[i-1] * (1 - self.island_min_gap_pct):
                down_gaps.append((i, high[i], low[i-1]))
        # 岛形顶：先有上跳（创岛屿），再有下跳（离开岛屿）
        if trend_dir == "up" and len(up_gaps) >= 1 and len(down_gaps) >= 1:
            last_up = up_gaps[-1]
            after_downs = [g for g in down_gaps if g[0] > last_up[0]]
            if after_downs:
                first_down = after_downs[0]
                island_bars = first_down[0] - last_up[0]
                if 1 <= island_bars <= self.island_max_bars:
                    return {
                        "type": "ISLAND_TOP",
                        "entry_gap_bar": int(last_up[0]),
                        "exit_gap_bar": int(first_down[0]),
                        "island_bars": int(island_bars),
                        "confirmed": True,
                        "target": None,  # 岛形目标不固定
                        "_sign": -1.0,
                    }
        # 岛形底
        if trend_dir == "down" and len(down_gaps) >= 1 and len(up_gaps) >= 1:
            last_down = down_gaps[-1]
            after_ups = [g for g in up_gaps if g[0] > last_down[0]]
            if after_ups:
                first_up = after_ups[0]
                island_bars = first_up[0] - last_down[0]
                if 1 <= island_bars <= self.island_max_bars:
                    return {
                        "type": "ISLAND_BOTTOM",
                        "entry_gap_bar": int(last_down[0]),
                        "exit_gap_bar": int(first_up[0]),
                        "island_bars": int(island_bars),
                        "confirmed": True,
                        "target": None,
                        "_sign": 1.0,
                    }
        return None

    # -------------------------- 主评估 --------------------------

    def evaluate(self, ticker: str, df: pd.DataFrame) -> SignalResult:
        reasons: list[str] = []
        details: dict = {"patterns": []}

        min_len = self.min_prior_trend_days + self.swing_window * 8
        if len(df) < min_len:
            return SignalResult(
                ticker, self.name, 0.0, False,
                details={"error": f"数据不足（需 ≥{min_len}）"},
                reasons=[f"数据不足（需 ≥{min_len}，当前 {len(df)}）"],
            )

        close = df["close"]
        volume = df.get("volume")
        last_close = float(close.iloc[-1])

        # 1. 前置趋势（无前置趋势 → 形态无效，Murphy 第 1 要点）
        trend_dir, trend_pct = prior_trend(
            close,
            min_days=self.min_prior_trend_days,
            min_pct=self.min_prior_trend_pct,
            exclude_tail=self.swing_window * 4,
        )
        details["prior_trend"] = {"dir": trend_dir, "pct": float(trend_pct)}
        if trend_dir == "none":
            reasons.append(f"无明确前置趋势（{trend_pct * 100:.1f}% < {self.min_prior_trend_pct * 100:.0f}%）→ 反转形态无效")
            return SignalResult(ticker, self.name, 0.0, False, details=details, reasons=reasons)
        reasons.append(f"前置趋势 {trend_dir} {trend_pct * 100:.1f}% ✓")

        # 2. swing + 形态检测（按优先级：H&S > 三重 > 双顶/底 > V 型 > 圆弧 > 岛形）
        highs, lows = find_swings(close, window=self.swing_window)
        details["n_swing_highs"] = len(highs)
        details["n_swing_lows"] = len(lows)

        pattern = None
        if trend_dir == "up":
            # 顶部形态链
            pattern = self._detect_head_and_shoulders_top(highs, lows, last_close)
            if pattern is None:
                pattern = self._detect_triple_top(highs, lows, last_close)
            if pattern is None:
                pattern = self._detect_double_top(highs, lows, last_close)
            if pattern is None:
                pattern = self._detect_v_reversal(close, last_close, trend_dir)
            if pattern is None:
                pattern = self._detect_rounding(close, last_close, trend_dir)
            if pattern is None:
                pattern = self._detect_island_reversal(df, trend_dir)
        else:
            # 底部形态链
            pattern = self._detect_head_and_shoulders_bottom(lows, highs, last_close)
            if pattern is None:
                pattern = self._detect_triple_bottom(lows, highs, last_close)
            if pattern is None:
                pattern = self._detect_double_bottom(highs, lows, last_close)
            if pattern is None:
                pattern = self._detect_v_reversal(close, last_close, trend_dir)
            if pattern is None:
                pattern = self._detect_rounding(close, last_close, trend_dir)
            if pattern is None:
                pattern = self._detect_island_reversal(df, trend_dir)

        if pattern is None:
            reasons.append(f"未检测到反转形态（H&S/三重/双/圆弧/V/岛形 swing 不足或价差不达标）")
            return SignalResult(ticker, self.name, 0.0, False, details=details, reasons=reasons)

        # 3. 量能确认（Murphy 第 3 要点）
        vol_ok = True
        if volume is not None and len(volume) >= self.volume_ma:
            vol_ma = volume.rolling(self.volume_ma).mean().iloc[-1]
            last_vol = volume.iloc[-1]
            if not pd.isna(vol_ma) and vol_ma > 0:
                vr = last_vol / vol_ma
                details["break_volume_ratio"] = float(vr)
                vol_ok = vr >= self.volume_ratio
                reasons.append(f"突破量能 {vr:.2f}x 均量 {'✓' if vol_ok else '✗'}（要求 ≥ {self.volume_ratio}x）")

        # 4. score（带符号）—— 复合形态按 type 后缀判定方向，无颈线形态（圆弧/V/岛形）按 _sign
        confirmed = pattern["confirmed"]
        base = 0.7 if confirmed else 0.35
        if confirmed and not vol_ok:
            base *= 0.7  # 缺量能打折
        if "_sign" in pattern:
            sign = pattern["_sign"]
        else:
            sign = -1.0 if pattern["type"].endswith("TOP") else 1.0
        score = sign * base

        # 形态可靠性加权（Murphy：形态越大越可靠）
        if pattern["type"] in ("TRIPLE_TOP", "TRIPLE_BOTTOM"):
            base *= 1.1  # 三重比双顶更可靠
        elif pattern["type"] in ("ROUNDING_TOP", "ROUNDING_BOTTOM"):
            base *= 1.05  # 圆弧可靠但慢
        elif pattern["type"] in ("V_TOP", "V_BOTTOM"):
            base *= 0.85  # V 型波动剧烈，止损难度高
        score = sign * base

        details["patterns"].append(pattern)
        details["pattern_type"] = pattern["type"]
        if "neckline" in pattern:
            details["neckline"] = pattern["neckline"]
        details["target"] = pattern.get("target")
        details["confirmed"] = confirmed

        tgt = pattern.get("target")
        if "neckline" in pattern:
            reasons.append(
                f"{pattern['type']}：颈线 {pattern['neckline']:.2f}，"
                f"{'✓ 已确认突破' if confirmed else '形成中（未破颈线）'}"
                f"{f'，目标 {tgt:.2f}' if tgt is not None else ''}"
            )
        elif tgt is not None:
            reasons.append(
                f"{pattern['type']}：{'✓ 已确认' if confirmed else '形成中'}，"
                f"目标 {tgt:.2f}"
            )
        else:
            extra = ""
            if pattern["type"].startswith("ISLAND"):
                extra = f"，岛屿 {pattern.get('island_bars', '?')} 根"
            reasons.append(
                f"{pattern['type']}：{'✓ 已确认' if confirmed else '形成中'}{extra}"
            )

        passed = confirmed and abs(score) >= self.threshold
        return SignalResult(
            ticker, self.name, float(score), bool(passed),
            details=details, reasons=reasons,
        ).clamp()
