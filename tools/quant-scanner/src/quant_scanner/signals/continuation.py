"""Continuation Patterns — Murphy 持续形态（旗形）

来源：John Murphy《金融市场技术分析》Ch6。
识别趋势中段的休息形态。第一版：旗形（Bull/Bear Flag）—— 最经典、可量化。
三角形（对称/上升/下降）/ 楔形 / 矩形留扩展（见 HANDOFF P1.3）。

Murphy 持续形态 4 原则：
1. 形态必须在中段（不是末端），方向沿主趋势
2. 持续时间短（旗形 1-3 周，> 4 周可能是反转）
3. 量能收缩（健康持续形态缩量）
4. 突破放量确认

旗形结构（bull flag）：L_start（旗杆起点低）→ H_pole（旗杆顶，急涨）→ L_dip（浅回调）→ 突破 H_pole
旗杆涨幅 ≥ 15%，回调 ≤ 旗杆的 50%，突破创新高。

value 带符号：正 = 看多持续（bull flag），负 = 看空持续（bear flag）。
"""
from __future__ import annotations

import logging
from typing import Optional

import pandas as pd

from .base import BaseSignal, SignalResult
from ..utils.swing import find_swings, prior_trend

log = logging.getLogger(__name__)


class ContinuationSignal(BaseSignal):
    """Murphy 旗形持续形态。"""

    name = "continuation"
    threshold = 0.5  # |value| ≥ 0.5 = 形态确认

    # 前置趋势 / 旗杆
    min_flagpole_pct: float = 0.15   # 旗杆涨/跌幅 ≥ 15%
    max_flag_pullback: float = 0.50  # 旗形回调 ≤ 旗杆的 50%
    flag_min_bars: int = 5           # 旗形区（H_pole 到 L_dip）≥ 5 根
    flag_max_bars: int = 20          # 旗形区 ≤ 20 根（> 4 周 = Murphy 反转警告线）

    swing_window: int = 5
    # 前置趋势（与 major_reversal 一致，避免 dead cat bounce 被识别成 bull flag）
    min_prior_trend_days: int = 60
    min_prior_trend_pct: float = 0.15  # 前置趋势幅度 ≥ 15%

    # 突破有效性
    break_pct: float = 0.0           # 突破创新高/低即可（旗形突破创新极值）
    volume_ratio: float = 1.5
    volume_ma: int = 50

    # 三角形/楔形/矩形参数
    triangle_min_swings: int = 3     # upper / lower 各至少 3 个 swing
    triangle_max_bars: int = 100     # 整理区最长 100 天（Murphy 三角形 1-3 月）
    triangle_min_bars: int = 15      # 最短 15 天
    slope_flat_threshold: float = 0.03  # 斜率绝对值 ≤ 3% 视为水平
    triangle_break_pct: float = 0.01    # 突破边界线 ≥ 1%

    # -------------------------- 旗形检测 --------------------------

    def _detect_bull_flag(self, highs, lows, last_close):
        """bull flag：L_start → H_pole（旗杆）→ L_dip（浅回调）→ 突破 H_pole。"""
        if len(lows) < 2 or len(highs) < 1:
            return None
        l_dip_idx, l_dip = lows[-1]
        l_start_idx, l_start = lows[-2]
        # H_pole 在 l_start 和 l_dip 之间
        poles = [(i, p) for i, p in highs if l_start_idx < i < l_dip_idx]
        if not poles:
            return None
        h_pole_idx, h_pole = max(poles, key=lambda x: x[1])
        # 旗杆涨幅
        flagpole = h_pole / l_start - 1 if l_start > 0 else 0
        if flagpole < self.min_flagpole_pct:
            return None
        # 旗形区长度
        flag_bars = l_dip_idx - h_pole_idx
        if not (self.flag_min_bars <= flag_bars <= self.flag_max_bars):
            return None
        # 回调幅度（浅）
        pullback = (h_pole - l_dip) / h_pole if h_pole > 0 else 0
        if pullback > flagpole * self.max_flag_pullback:
            return None
        # 突破：创新高（突破旗杆顶）
        confirmed = last_close > h_pole
        target = h_pole + (h_pole - l_start)  # 旗杆长度从突破点投射（Murphy）
        return {
            "type": "BULL_FLAG", "flagpole_pct": float(flagpole),
            "pole_top": float(h_pole), "dip_low": float(l_dip),
            "pullback_pct": float(pullback), "flag_bars": int(flag_bars),
            "confirmed": bool(confirmed), "target": float(target) if confirmed else None,
        }

    def _detect_bear_flag(self, highs, lows, last_close):
        """bear flag：H_start → L_pole（旗杆底，急跌）→ H_dip（浅反弹）→ 跌破 L_pole。"""
        if len(highs) < 2 or len(lows) < 1:
            return None
        h_dip_idx, h_dip = highs[-1]
        h_start_idx, h_start = highs[-2]
        poles = [(i, p) for i, p in lows if h_start_idx < i < h_dip_idx]
        if not poles:
            return None
        l_pole_idx, l_pole = min(poles, key=lambda x: x[1])
        flagpole = (h_start - l_pole) / h_start if h_start > 0 else 0
        if flagpole < self.min_flagpole_pct:
            return None
        flag_bars = h_dip_idx - l_pole_idx
        if not (self.flag_min_bars <= flag_bars <= self.flag_max_bars):
            return None
        rally = (h_dip - l_pole) / l_pole if l_pole > 0 else 0
        if rally > flagpole * self.max_flag_pullback:
            return None
        confirmed = last_close < l_pole
        target = l_pole - (h_start - l_pole)
        return {
            "type": "BEAR_FLAG", "flagpole_pct": float(flagpole),
            "pole_bottom": float(l_pole), "dip_high": float(h_dip),
            "rally_pct": float(rally), "flag_bars": int(flag_bars),
            "confirmed": bool(confirmed), "target": float(target) if confirmed else None,
        }

    # -------------------------- 三角形/楔形/矩形 --------------------------

    @staticmethod
    def _line_slope(points: list[tuple[int, float]]) -> tuple[float, int, int]:
        """计算首末点斜率（百分比，归一化）。返回 (slope, first_idx, last_idx)。

        slope > 0 = 上行；slope < 0 = 下行；≈ 0 = 水平
        """
        if len(points) < 2:
            return 0.0, 0, 0
        first_idx, first_price = points[0]
        last_idx, last_price = points[-1]
        if first_price <= 0:
            return 0.0, first_idx, last_idx
        return (last_price - first_price) / first_price, first_idx, last_idx

    def _detect_triangle_wedge_rectangle(self, highs, lows, last_close, trend_dir):
        """三角形/楔形/矩形识别（Murphy Ch6）。

        取最近 N 个 swing 拟合 upper / lower 线斜率，分类为 6 种形态：
        - 对称三角形（上斜率 < 0 + 下斜率 > 0）
        - 上升三角形（上斜率 ≈ 0 + 下斜率 > 0）
        - 下降三角形（上斜率 < 0 + 下斜率 ≈ 0）
        - 矩形（上下都 ≈ 0）
        - 上升楔形（上 > 0 + 下 > 0 + 上斜率 < 下斜率）→ 看跌反转
        - 下降楔形（上 < 0 + 下 < 0 + 上斜率 > 下斜率）→ 看涨反转

        突破：close 突破 upper 或跌破 lower。
        """
        if len(highs) < self.triangle_min_swings or len(lows) < self.triangle_min_swings:
            return None

        # 取最近 N 个 swing
        upper = highs[-self.triangle_min_swings:]
        lower = lows[-self.triangle_min_swings:]

        # 整理区长度
        first_idx = min(upper[0][0], lower[0][0])
        last_swing_idx = max(upper[-1][0], lower[-1][0])
        bars = last_swing_idx - first_idx
        if not (self.triangle_min_bars <= bars <= self.triangle_max_bars):
            return None

        upper_slope, _, _ = self._line_slope(upper)
        lower_slope, _, _ = self._line_slope(lower)
        upper_last_price = upper[-1][1]
        lower_last_price = lower[-1][1]
        upper_first_price = upper[0][1]
        lower_first_price = lower[0][1]

        flat = self.slope_flat_threshold

        # 形态分类
        if abs(upper_slope) < flat and abs(lower_slope) < flat:
            ptype = "RECTANGLE"
        elif abs(upper_slope) < flat and lower_slope > flat:
            ptype = "ASCENDING_TRIANGLE"
        elif upper_slope < -flat and abs(lower_slope) < flat:
            ptype = "DESCENDING_TRIANGLE"
        elif upper_slope < -flat and lower_slope > flat:
            ptype = "SYMMETRIC_TRIANGLE"
        elif upper_slope > flat and lower_slope > flat and upper_slope < lower_slope:
            ptype = "RISING_WEDGE"  # 看跌反转
        elif upper_slope < -flat and lower_slope < -flat and upper_slope > lower_slope:
            ptype = "FALLING_WEDGE"  # 看涨反转
        else:
            return None  # 不构成清晰形态

        # 突破判定
        upper_break = (last_close - upper_last_price) / upper_last_price if upper_last_price > 0 else 0
        lower_break = (lower_last_price - last_close) / lower_last_price if lower_last_price > 0 else 0
        break_dir = None
        if upper_break >= self.triangle_break_pct:
            break_dir = "up"
        elif lower_break >= self.triangle_break_pct:
            break_dir = "down"

        # 看涨/看跌方向（与突破方向一致；楔形方向反转）
        if ptype == "RISING_WEDGE":
            default_dir = "down"  # 楔形向下突破为常态
        elif ptype == "FALLING_WEDGE":
            default_dir = "up"
        elif ptype == "DESCENDING_TRIANGLE":
            default_dir = "down"
        elif ptype == "ASCENDING_TRIANGLE":
            default_dir = "up"
        else:
            # 对称三角/矩形：方向跟随突破或主趋势
            default_dir = trend_dir if trend_dir in ("up", "down") else "up"

        # 沿默认方向突破 = 强信号；逆向 = 弱信号
        confirmed = break_dir is not None
        aligned = break_dir == default_dir if confirmed else False

        # 目标价：矩形/三角形的突破投射 = 形态高度
        height = max(upper_last_price, upper_first_price) - min(lower_last_price, lower_first_price)
        if confirmed and break_dir == "up":
            target = upper_last_price + height
        elif confirmed and break_dir == "down":
            target = lower_last_price - height
        else:
            target = None

        return {
            "type": ptype,
            "upper_slope": float(upper_slope),
            "lower_slope": float(lower_slope),
            "upper_line": [float(upper_first_price), float(upper_last_price)],
            "lower_line": [float(lower_first_price), float(lower_last_price)],
            "bars": int(bars),
            "break_dir": break_dir,
            "aligned_with_default": bool(aligned) if confirmed else False,
            "default_dir": default_dir,
            "confirmed": bool(confirmed),
            "target": float(target) if target is not None else None,
        }

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

        # 前置趋势检测（P0：避免 dead cat bounce 被识别成 bull flag）
        trend_dir, trend_pct = prior_trend(
            close,
            min_days=self.min_prior_trend_days,
            min_pct=self.min_prior_trend_pct,
            exclude_tail=self.swing_window * 4,
        )
        details["prior_trend"] = trend_dir
        details["prior_trend_pct"] = float(trend_pct)
        reasons.append(f"前置趋势：{trend_dir}（{trend_pct * 100:.1f}%）")

        highs, lows = find_swings(close, window=self.swing_window)
        details["n_swing_highs"] = len(highs)
        details["n_swing_lows"] = len(lows)

        # 形态方向必须沿主趋势（Murphy 持续形态第 1 原则）
        # 优先检测三角形/楔形/矩形，未命中再降级旗形
        # 注：三角/楔形/矩形允许 trend=none（矩形/对称三角方向跟随突破），
        #     旗形仍要求明确 trend（避免 dead cat bounce 误判）
        pattern = None
        if trend_dir in ("up", "down"):
            pattern = self._detect_triangle_wedge_rectangle(highs, lows, last_close, trend_dir)
            if pattern is None:
                if trend_dir == "up":
                    pattern = self._detect_bull_flag(highs, lows, last_close)
                else:
                    pattern = self._detect_bear_flag(highs, lows, last_close)
            if pattern is None:
                if trend_dir == "up":
                    reasons.append("上涨趋势中未检测到 bull flag / 三角 / 楔形 / 矩形")
                else:
                    reasons.append("下跌趋势中未检测到 bear flag / 三角 / 楔形 / 矩形")
        else:
            # 无明确趋势，仍尝试三角/楔形/矩形（方向跟随突破）
            pattern = self._detect_triangle_wedge_rectangle(highs, lows, last_close, "none")
            if pattern is None:
                reasons.append(
                    f"无显著前置趋势（< {self.min_prior_trend_pct * 100:.0f}%），"
                    f"且未检测到三角/楔形/矩形（dead cat bounce 风险）"
                )

        if pattern is None:
            return SignalResult(ticker, self.name, 0.0, False, details=details, reasons=reasons)

        # 量能确认（Murphy：突破需放量）
        vol_ok = True
        if volume is not None and len(volume) >= self.volume_ma:
            vol_ma = volume.rolling(self.volume_ma).mean().iloc[-1]
            last_vol = volume.iloc[-1]
            if not pd.isna(vol_ma) and vol_ma > 0:
                vr = last_vol / vol_ma
                details["break_volume_ratio"] = float(vr)
                vol_ok = vr >= self.volume_ratio
                reasons.append(f"突破量能 {vr:.2f}x 均量 {'✓' if vol_ok else '✗'}（要求 ≥ {self.volume_ratio}x）")

        # score（带符号）
        confirmed = pattern["confirmed"]
        base = 0.7 if confirmed else 0.35
        if confirmed and not vol_ok:
            base *= 0.7

        # 符号判定：旗形按 type；三角/楔形/矩形按 break_dir/default_dir
        if pattern["type"] in ("BULL_FLAG", "BEAR_FLAG"):
            sign = 1.0 if pattern["type"] == "BULL_FLAG" else -1.0
        else:
            # 三角/楔形/矩形：突破方向决定 sign；未突破按 default_dir
            effective_dir = pattern.get("break_dir") or pattern.get("default_dir", "up")
            sign = 1.0 if effective_dir == "up" else -1.0
            # 逆向突破（与 default_dir 相反）→ 弱信号
            if confirmed and not pattern.get("aligned_with_default", True):
                base *= 0.6
        score = sign * base

        details["patterns"].append(pattern)
        details["pattern_type"] = pattern["type"]
        details["confirmed"] = confirmed
        if "flagpole_pct" in pattern:
            details["flagpole_pct"] = pattern["flagpole_pct"]
        if pattern.get("target"):
            details["target"] = pattern["target"]

        tgt = pattern.get("target")
        tgt_str = f"，目标 {tgt:.2f}" if tgt is not None else ""

        # reason 格式按形态分类
        if pattern["type"] in ("BULL_FLAG", "BEAR_FLAG"):
            pullback = pattern.get("pullback_pct", pattern.get("rally_pct", 0))
            reasons.append(
                f"{pattern['type']}：旗杆 {pattern['flagpole_pct'] * 100:.1f}%，"
                f"回调 {pullback * 100:.1f}%，"
                f"{'✓ 已突破' if confirmed else '形成中（未突破旗杆极值）'}{tgt_str}"
            )
        else:
            brk = pattern.get("break_dir")
            brk_str = f"向{('上' if brk == 'up' else '下')}突破" if brk else "形成中（未突破边界）"
            reasons.append(
                f"{pattern['type']}：上斜率 {pattern['upper_slope']*100:+.1f}%，"
                f"下斜率 {pattern['lower_slope']*100:+.1f}%，"
                f"{brk_str}{tgt_str}"
            )

        passed = confirmed and abs(score) >= self.threshold
        return SignalResult(
            ticker, self.name, float(score), bool(passed),
            details=details, reasons=reasons,
        ).clamp()
