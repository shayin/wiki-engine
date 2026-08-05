"""Oscillator Timing — Murphy 震荡指标择时（RSI 背离版）

来源：John Murphy《金融市场技术分析》Ch10。
第一版：RSI(14) + 经典顶/底背离 + 超买超卖。
MACD / Stochastic / 隐藏背离 / 失败摆动留扩展（见 HANDOFF P1.4）。

Murphy 三原则：
1. 背离是警报不是信号，必须等价格本身确认
2. 强趋势市场（ADX>30）震荡指标失效（持续超买/超卖）—— 本版不内嵌 ADX 过滤，需配合 trend_regime 使用
3. 适用于区间市场 / 趋势末端

value 正 = 看多（底背离/超卖），负 = 看空（顶背离/超买）。passed = 检测到背离。
"""
from __future__ import annotations

import logging
from typing import Optional

import pandas as pd

from .base import BaseSignal, SignalResult
from ..utils.swing import find_swings

log = logging.getLogger(__name__)


class OscillatorTimingSignal(BaseSignal):
    """RSI 背离 + 超买超卖择时。"""

    name = "oscillator_timing"
    threshold = 0.5  # |value| ≥ 0.5 = 出现背离

    rsi_period: int = 14
    rsi_overbought: float = 70.0
    rsi_oversold: float = 30.0
    rsi_extreme_high: float = 80.0
    rsi_extreme_low: float = 20.0
    swing_window: int = 5
    min_lookback: int = 60
    # RSI 衰减阈值（≥ N 点算背离；1 点过宽，噪声大）
    rsi_divergence_threshold: float = 3.0

    # MACD 参数（Murphy Ch10）
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    macd_divergence_threshold: float = 0.05  # MACD 直方图背离阈值（相对价格归一化）

    # -------------------------- RSI --------------------------

    def _rsi(self, close: pd.Series) -> pd.Series:
        """Wilder RSI（14 周期）。

        前 rsi_period-1 个值是 NaN（数据不足），不填 50（避免污染早期 swing 检测）。
        """
        delta = close.diff()
        gain = delta.clip(lower=0)
        loss = (-delta).clip(lower=0)
        avg_gain = gain.ewm(alpha=1 / self.rsi_period, min_periods=self.rsi_period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1 / self.rsi_period, min_periods=self.rsi_period, adjust=False).mean()
        avg_loss = avg_loss.replace(0, 1e-10)
        rs = avg_gain / avg_loss
        return 100 - 100 / (1 + rs)  # 前 rsi_period-1 个值保留 NaN

    def _macd(self, close: pd.Series) -> tuple[pd.Series, pd.Series, pd.Series]:
        """MACD 三件套：返回 (macd_line, signal_line, histogram)。"""
        ema_fast = close.ewm(span=self.macd_fast, adjust=False).mean()
        ema_slow = close.ewm(span=self.macd_slow, adjust=False).mean()
        macd_line = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=self.macd_signal, adjust=False).mean()
        hist = macd_line - signal_line
        return macd_line, signal_line, hist

    def _detect_hidden_divergence(self, highs, lows, oscillator: pd.Series, osc_name: str, osc_threshold: float):
        """隐藏背离（Murphy Ch10）：

        与经典背离相反 —— 趋势延续信号。
        - 隐藏看跌：价格更低的低（lower high）+ 振荡器更高的高（higher high）
          → 上涨趋势中回调，振荡器反而创新高，动能弱化但趋势继续
        - 隐藏看涨：价格更高的低（higher low）+ 振荡器更低的低（lower low）
          → 下跌趋势中反弹，振荡器反而创新低，卖压消化，趋势继续

        Returns: ("BULLISH_HIDDEN" | "BEARISH_HIDDEN" | None, details_dict)
        """
        valid_highs = [(i, p) for i, p in highs if i >= len(oscillator) - len(oscillator.dropna())]
        valid_lows = [(i, p) for i, p in lows if i >= len(oscillator) - len(oscillator.dropna())]
        if not pd.isna(oscillator.iloc[0]):
            valid_highs = [(i, p) for i, p in highs if not pd.isna(oscillator.iloc[i]) if i < len(oscillator)]
            valid_lows = [(i, p) for i, p in lows if not pd.isna(oscillator.iloc[i]) if i < len(oscillator)]

        # 隐藏看跌：价格 LH + 振荡器 HH
        if len(valid_highs) >= 2:
            h1_idx, h1 = valid_highs[-2]
            h2_idx, h2 = valid_highs[-1]
            o1 = float(oscillator.iloc[h1_idx])
            o2 = float(oscillator.iloc[h2_idx])
            if h2 < h1 and o2 > o1 + osc_threshold:
                return "BEARISH_HIDDEN", {
                    f"{osc_name}_h1": o1, f"{osc_name}_h2": o2,
                    "price_h1": h1, "price_h2": h2,
                }

        # 隐藏看涨：价格 HL + 振荡器 LL
        if len(valid_lows) >= 2:
            l1_idx, l1 = valid_lows[-2]
            l2_idx, l2 = valid_lows[-1]
            o1 = float(oscillator.iloc[l1_idx])
            o2 = float(oscillator.iloc[l2_idx])
            if l2 > l1 and o2 < o1 - osc_threshold:
                return "BULLISH_HIDDEN", {
                    f"{osc_name}_l1": o1, f"{osc_name}_l2": o2,
                    "price_l1": l1, "price_l2": l2,
                }

        return None, {}

    def _detect_failure_swing(self, oscillator: pd.Series) -> tuple[Optional[str], dict]:
        """失败摆动（Murphy Ch10）：

        振荡器自身的反转形态，独立于价格（不需要价格确认）。
        - 看涨失败摆动：RSI 跌破前低点（A）→ 反弹突破前高点（C）→ 回调不破前低点（D < C 的低点）
        - 看跌失败摆动：RSI 突破前高点（A）→ 回落跌破前低点（C）→ 反弹不破前高点（D < C 的高点）

        简化实现：取振荡器最近 4 个 swing 点判断 A-B-C-D 结构。
        """
        osc_highs, osc_lows = find_swings(oscillator.dropna(), window=3)
        if len(osc_highs) < 2 or len(osc_lows) < 2:
            return None, {}

        last_low_idx, last_low = osc_lows[-1]
        prev_low_idx, prev_low = osc_lows[-2]
        last_high_idx, last_high = osc_highs[-1]
        prev_high_idx, prev_high = osc_highs[-2]

        # 看涨失败摆动：最近低 > 前低（D > B），且最近高 > 前高（C > A），且 A 高于 B
        if (last_low > prev_low and last_high > prev_high and prev_high > prev_low
                and last_low_idx > last_high_idx):  # 顺序：A-B-C-D
            return "BULLISH_FAILURE", {
                "point_a_low": float(prev_low), "point_c_high": float(last_high),
                "point_d_low": float(last_low),
            }

        # 看跌失败摆动：最近高 < 前高（D < B），且最近低 < 前低（C < A），且 A 低于 B
        if (last_high < prev_high and last_low < prev_low and prev_low < prev_high
                and last_high_idx > last_low_idx):
            return "BEARISH_FAILURE", {
                "point_a_high": float(prev_high), "point_c_low": float(last_low),
                "point_d_high": float(last_high),
            }

        return None, {}

    # -------------------------- 主评估 --------------------------

    def evaluate(self, ticker: str, df: pd.DataFrame) -> SignalResult:
        reasons: list[str] = []
        details: dict = {}

        if len(df) < self.min_lookback:
            return SignalResult(
                ticker, self.name, 0.0, False,
                details={"error": f"数据不足（需 ≥{self.min_lookback}）"},
                reasons=[f"数据不足（需 ≥{self.min_lookback}，当前 {len(df)}）"],
            )

        close = df["close"]
        rsi = self._rsi(close)
        last_rsi = float(rsi.iloc[-1]) if not pd.isna(rsi.iloc[-1]) else 50.0
        details["rsi"] = last_rsi

        # MACD 三件套
        macd_line, macd_signal, macd_hist = self._macd(close)
        last_macd = float(macd_line.iloc[-1]) if not pd.isna(macd_line.iloc[-1]) else 0.0
        last_hist = float(macd_hist.iloc[-1]) if not pd.isna(macd_hist.iloc[-1]) else 0.0
        details["macd_line"] = last_macd
        details["macd_histogram"] = last_hist

        highs, lows = find_swings(close, window=self.swing_window)

        # ---------- 1. RSI 经典背离 ----------
        classic_div = None  # "BULLISH" | "BEARISH" | None

        valid_highs = [(i, p) for i, p in highs if i >= self.rsi_period]
        if len(valid_highs) >= 2:
            h2_idx, h2 = valid_highs[-1]
            h1_idx, h1 = valid_highs[-2]
            rsi_h1 = float(rsi.iloc[h1_idx])
            rsi_h2 = float(rsi.iloc[h2_idx])
            if h2 > h1 and rsi_h2 < rsi_h1 - self.rsi_divergence_threshold:
                classic_div = "BEARISH"
                details.update({
                    "divergence": "BEARISH_DIVERGENCE",
                    "price_high_1": h1, "price_high_2": h2,
                    "rsi_high_1": rsi_h1, "rsi_high_2": rsi_h2,
                })
                reasons.append(
                    f"⚠️ 顶背离：价格 {h1:.1f}→{h2:.1f} 创新高，但 RSI {rsi_h1:.0f}→{rsi_h2:.0f} 衰竭"
                )

        valid_lows = [(i, p) for i, p in lows if i >= self.rsi_period]
        if classic_div is None and len(valid_lows) >= 2:
            l2_idx, l2 = valid_lows[-1]
            l1_idx, l1 = valid_lows[-2]
            rsi_l1 = float(rsi.iloc[l1_idx])
            rsi_l2 = float(rsi.iloc[l2_idx])
            if l2 < l1 and rsi_l2 > rsi_l1 + self.rsi_divergence_threshold:
                classic_div = "BULLISH"
                details.update({
                    "divergence": "BULLISH_DIVERGENCE",
                    "price_low_1": l1, "price_low_2": l2,
                    "rsi_low_1": rsi_l1, "rsi_low_2": rsi_l2,
                })
                reasons.append(
                    f"⚠️ 底背离：价格 {l1:.1f}→{l2:.1f} 创新低，但 RSI {rsi_l1:.0f}→{rsi_l2:.0f} 走强"
                )

        # ---------- 2. MACD 经典背离（RSI 未触发时做交叉验证） ----------
        macd_div = None
        # MACD 归一化阈值（hist / close），避免高价股阈值失真
        if len(close) > 0 and not pd.isna(close.iloc[-1]):
            macd_norm = self.macd_divergence_threshold * float(close.iloc[-1])
        else:
            macd_norm = self.macd_divergence_threshold * 100.0

        valid_highs_macd = [(i, p) for i, p in highs if i >= self.macd_slow]
        if len(valid_highs_macd) >= 2:
            h2_idx, h2 = valid_highs_macd[-1]
            h1_idx, h1 = valid_highs_macd[-2]
            m1 = macd_line.iloc[h1_idx]
            m2 = macd_line.iloc[h2_idx]
            if not pd.isna(m1) and not pd.isna(m2):
                if h2 > h1 and float(m2) < float(m1) - macd_norm:
                    macd_div = "BEARISH"
                    details["macd_divergence"] = "BEARISH"
                    details["macd_high_1"] = float(m1)
                    details["macd_high_2"] = float(m2)
                    if classic_div is None:
                        reasons.append(
                            f"⚠️ MACD 顶背离：价格 {h1:.1f}→{h2:.1f} 新高，MACD {m1:.2f}→{m2:.2f} 衰竭"
                        )

        valid_lows_macd = [(i, p) for i, p in lows if i >= self.macd_slow]
        if macd_div is None and len(valid_lows_macd) >= 2:
            l2_idx, l2 = valid_lows_macd[-1]
            l1_idx, l1 = valid_lows_macd[-2]
            m1 = macd_line.iloc[l1_idx]
            m2 = macd_line.iloc[l2_idx]
            if not pd.isna(m1) and not pd.isna(m2):
                if l2 < l1 and float(m2) > float(m1) + macd_norm:
                    macd_div = "BULLISH"
                    details["macd_divergence"] = "BULLISH"
                    details["macd_low_1"] = float(m1)
                    details["macd_low_2"] = float(m2)
                    if classic_div is None:
                        reasons.append(
                            f"⚠️ MACD 底背离：价格 {l1:.1f}→{l2:.1f} 新低，MACD {m1:.2f}→{m2:.2f} 走强"
                        )

        # ---------- 3. 隐藏背离（趋势延续） ----------
        hidden_div, hidden_details = self._detect_hidden_divergence(
            highs, lows, rsi, "rsi", self.rsi_divergence_threshold,
        )
        if hidden_div is not None:
            details["hidden_divergence"] = hidden_div
            details.update(hidden_details)
            if hidden_div == "BULLISH_HIDDEN":
                reasons.append("⚠️ RSI 隐藏看涨背离（价格 HL + RSI LL）→ 下跌趋势延续")
            else:
                reasons.append("⚠️ RSI 隐藏看跌背离（价格 LH + RSI HH）→ 上涨趋势延续")

        # ---------- 4. 失败摆动 ----------
        failure, failure_details = self._detect_failure_swing(rsi)
        if failure is not None:
            details["failure_swing"] = failure
            details.update(failure_details)
            if failure == "BULLISH_FAILURE":
                reasons.append("⚠️ RSI 看涨失败摆动（A-B-C-D 结构，独立于价格）")
            else:
                reasons.append("⚠️ RSI 看跌失败摆动（A-B-C-D 结构，独立于价格）")

        # ---------- 综合评分 ----------
        # 优先级：经典背离 > 隐藏背离 > 失败摆动 > 超买超卖
        score = 0.0
        passed = False

        # 经典背离（RSI 或 MACD 命中即可）
        bearish_classic = classic_div == "BEARISH" or macd_div == "BEARISH"
        bullish_classic = classic_div == "BULLISH" or macd_div == "BULLISH"

        if bearish_classic:
            # MACD + RSI 同时确认 → 加强
            score = -0.75 if (classic_div == "BEARISH" and macd_div == "BEARISH") else -0.6
            passed = True
        elif bullish_classic:
            score = 0.75 if (classic_div == "BULLISH" and macd_div == "BULLISH") else 0.6
            passed = True
        elif hidden_div == "BEARISH_HIDDEN":
            score = -0.5
            passed = True
        elif hidden_div == "BULLISH_HIDDEN":
            score = 0.5
            passed = True
        elif failure == "BEARISH_FAILURE":
            score = -0.45
            passed = True
        elif failure == "BULLISH_FAILURE":
            score = 0.45
            passed = True
        elif last_rsi >= self.rsi_extreme_high:
            score = -0.3
            reasons.append(f"RSI {last_rsi:.0f} 极端超买（≥{self.rsi_extreme_high:.0f}）")
        elif last_rsi <= self.rsi_extreme_low:
            score = 0.3
            reasons.append(f"RSI {last_rsi:.0f} 极端超卖（≤{self.rsi_extreme_low:.0f}）")
        elif last_rsi >= self.rsi_overbought:
            score = -0.15
            reasons.append(f"RSI {last_rsi:.0f} 超买（≥{self.rsi_overbought:.0f}）")
        elif last_rsi <= self.rsi_oversold:
            score = 0.15
            reasons.append(f"RSI {last_rsi:.0f} 超卖（≤{self.rsi_oversold:.0f}）")
        else:
            # MACD histogram 方向作为辅助
            if last_hist > 0:
                score = 0.05
                reasons.append(f"RSI {last_rsi:.0f} 中性，MACD 直方图 > 0（弱多）")
            elif last_hist < 0:
                score = -0.05
                reasons.append(f"RSI {last_rsi:.0f} 中性，MACD 直方图 < 0（弱空）")
            else:
                reasons.append(f"RSI {last_rsi:.0f} 中性，无背离")

        # 强制提醒（Murphy：背离非信号 + 强趋势失效）
        if bearish_classic or bullish_classic:
            reasons.append("⚠️ 背离是警报非信号，须等价格跌破/突破颈线确认")

        return SignalResult(
            ticker, self.name, float(score), bool(passed),
            details=details, reasons=reasons,
        ).clamp()
