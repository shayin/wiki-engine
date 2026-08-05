"""趋势制度过滤（Trend Regime Filter）— Murphy 精确版

来源：《金融市场技术分析》第 11-13 章 — John Murphy 的趋势判定核心。

为何需要趋势制度过滤：
- 信号在不同趋势制度下胜率差异巨大（强趋势 60%+ vs 无趋势 35%）
- 用作"信号开关"：强趋势 = 全信号开启，弱趋势 = 仅形态信号，无趋势 = 关闭

精确规则（Murphy 原书）：
1. **ADX（Wilder）**：
   - ADX ≥ 25：强趋势
   - ADX 20-25：弱趋势（发展中）
   - ADX ≤ 20：无趋势（震荡市）
   - ADX 上升 = 趋势强化；ADX 下降 = 趋势老化
2. **均线方向**：
   - 价格 vs MA20/MA50/MA200 三排列
   - 斜率（20 日均值变化率）> 0 = 上升
3. **道氏阶段（Dow Theory 4 phases）**：
   - 阶段 1：底部积累（MA200 走平 + 价格震荡）
   - 阶段 2：上涨趋势（MA50>MA200 + 量价齐升）
   - 阶段 3：顶部派发（MA200 走平 + 价格高位震荡）
   - 阶段 4：下跌趋势（MA50<MA200 + 价格破前低）

打分（-1 到 +1）：
- 强趋势上涨（阶段 2 + ADX≥25）= +1.0
- 弱趋势上涨 = +0.5
- 无趋势 = 0.0
- 弱趋势下跌 = -0.5
- 强趋势下跌 = -1.0
"""
from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import pandas as pd

from .base import BaseSignal, SignalResult

log = logging.getLogger(__name__)


class TrendRegimeSignal(BaseSignal):
    name = "trend_regime"
    threshold = 0.50  # ≥ 0.5 = 可交易的多头制度

    # ADX 参数（Wilder）
    adx_period: int = 14
    adx_strong: float = 25.0       # 强趋势阈值
    adx_weak: float = 20.0         # 弱趋势阈值
    adx_lookback: int = 5          # 检测 ADX 斜率回看天数

    # 均线参数
    ma_short: int = 20
    ma_mid: int = 50
    ma_long: int = 200
    ma_slope_window: int = 20      # 斜率计算窗口

    # 道氏阶段
    ma_flat_tolerance: float = 0.01  # MA200 走平容忍度（1% 斜率）

    def evaluate(self, ticker: str, df: pd.DataFrame) -> SignalResult:
        reasons: list[str] = []
        details: dict = {}

        if len(df) < self.ma_long + 20:
            return SignalResult(
                ticker=ticker, signal_name=self.name, value=0.0, passed=False,
                details={"error": "数据不足"}, reasons=[f"数据不足 < {self.ma_long + 20} bar"],
            )

        close = df["close"]
        high = df["high"]
        low = df["low"]
        last_close = float(close.iloc[-1])

        # ============ 1. ADX 计算 ============
        adx, plus_dm, minus_dm = self._calculate_adx(high, low, close)
        details["adx"] = float(adx)
        details["+di"] = float(plus_dm)
        details["-di"] = float(minus_dm)

        # ADX 斜率（趋势强度变化）
        adx_history = self._calculate_adx_series(high, low, close)
        if len(adx_history) >= self.adx_lookback:
            adx_slope = adx - adx_history.iloc[-self.adx_lookback - 1]
            details["adx_slope"] = float(adx_slope)
        else:
            adx_slope = 0.0

        # ============ 2. 均线系统 ============
        ma20 = close.rolling(self.ma_short).mean()
        ma50 = close.rolling(self.ma_mid).mean()
        ma200 = close.rolling(self.ma_long).mean()

        ma20_last = float(ma20.iloc[-1])
        ma50_last = float(ma50.iloc[-1])
        ma200_last = float(ma200.iloc[-1])

        # MA50 / MA200 斜率
        ma50_slope = (ma50.iloc[-1] - ma50.iloc[-self.ma_slope_window]) / ma50.iloc[-self.ma_slope_window]
        ma200_slope = (ma200.iloc[-1] - ma200.iloc[-self.ma_slope_window * 2]) / ma200.iloc[-self.ma_slope_window * 2]

        details["ma20"] = ma20_last
        details["ma50"] = ma50_last
        details["ma200"] = ma200_last
        details["ma50_slope_pct"] = float(ma50_slope * 100)
        details["ma200_slope_pct"] = float(ma200_slope * 100)

        # 三排列
        perfect_long = last_close > ma20_last > ma50_last > ma200_last
        perfect_short = last_close < ma20_last < ma50_last < ma200_last
        details["ma_alignment"] = (
            "perfect_long" if perfect_long else
            "perfect_short" if perfect_short else
            "mixed"
        )

        # ============ 3. 道氏阶段判定 ============
        ma200_flat = abs(float(ma200_slope)) < self.ma_flat_tolerance
        ma200_rising = float(ma200_slope) > self.ma_flat_tolerance
        ma200_falling = float(ma200_slope) < -self.ma_flat_tolerance
        ma50_above_200 = ma50_last > ma200_last

        if ma200_flat and not ma50_above_200:
            dow_phase = 1
            dow_label = "底部积累"
        elif ma50_above_200 and (ma200_rising or ma200_flat):
            dow_phase = 2
            dow_label = "上涨趋势"
        elif ma200_flat and ma50_above_200:
            dow_phase = 3
            dow_label = "顶部派发"
        else:
            dow_phase = 4
            dow_label = "下跌趋势"
        details["dow_phase"] = dow_phase
        details["dow_label"] = dow_label

        # ============ 4. 方向判定 ============
        di_bullish = plus_dm > minus_dm
        di_bearish = minus_dm > plus_dm

        # 综合打分
        if adx >= self.adx_strong:
            trend_strength = "强"
            strength_mult = 1.0
        elif adx >= self.adx_weak:
            trend_strength = "弱"
            strength_mult = 0.5
        else:
            trend_strength = "无"
            strength_mult = 0.0

        # 方向：道氏阶段 + DI 对比 + 均线
        bull_signals = 0
        bear_signals = 0
        if dow_phase == 2:
            bull_signals += 1
        elif dow_phase == 4:
            bear_signals += 1
        if di_bullish:
            bull_signals += 1
        elif di_bearish:
            bear_signals += 1
        if perfect_long:
            bull_signals += 1
        elif perfect_short:
            bear_signals += 1
        if ma50_slope > 0:
            bull_signals += 1
        elif ma50_slope < 0:
            bear_signals += 1

        if bull_signals > bear_signals:
            direction = +1
            dir_label = "上涨"
        elif bear_signals > bull_signals:
            direction = -1
            dir_label = "下跌"
        else:
            direction = 0
            dir_label = "中性"

        score = direction * strength_mult

        # ADX 老化警告
        if adx_slope < -2 and adx > self.adx_strong:
            reasons.append(f"⚠️ ADX {adx:.1f} 但下降 {adx_slope:.1f}（趋势老化）")
            score *= 0.7

        details["trend_strength"] = trend_strength
        details["direction"] = dir_label
        details["bull_signals"] = bull_signals
        details["bear_signals"] = bear_signals

        reasons.append(
            f"{trend_strength}趋势 {dir_label}（ADX {adx:.1f}, +DI {plus_dm:.1f}/-DI {minus_dm:.1f}）"
        )
        reasons.append(f"道氏阶段 {dow_phase}：{dow_label}")
        reasons.append(
            f"MA 排列：{details['ma_alignment']}，MA50 斜率 {ma50_slope*100:.2f}%"
        )

        passed = score >= self.threshold
        return SignalResult(
            ticker=ticker, signal_name=self.name,
            value=float(score), passed=bool(passed),
            details=details, reasons=reasons,
        ).clamp()

    # ==================== ADX 计算（Wilder）====================

    def _calculate_adx(self, high: pd.Series, low: pd.Series, close: pd.Series) -> tuple[float, float, float]:
        """返回最新 (ADX, +DI, -DI)"""
        series = self._calculate_adx_series(high, low, close)
        if series is None or len(series) == 0:
            return 0.0, 0.0, 0.0
        adx = float(series.iloc[-1])
        plus_di_series = self._calc_di(high, low, close, plus=True)
        minus_di_series = self._calc_di(high, low, close, plus=False)
        plus_di = float(plus_di_series.iloc[-1]) if len(plus_di_series) else 0.0
        minus_di = float(minus_di_series.iloc[-1]) if len(minus_di_series) else 0.0
        return adx, plus_di, minus_di

    def _calculate_adx_series(self, high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
        """计算 ADX 序列（Wilder smoothing）"""
        try:
            n = self.adx_period
            # True Range
            tr = self._true_range(high, low, close)
            # +DM / -DM
            up_move = high.diff()
            down_move = -low.diff()
            plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=high.index)
            minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=high.index)

            # Wilder smoothing
            tr_smooth = self._wilder_smooth(tr, n)
            plus_dm_smooth = self._wilder_smooth(plus_dm, n)
            minus_dm_smooth = self._wilder_smooth(minus_dm, n)

            # +DI / -DI
            plus_di = 100 * plus_dm_smooth / tr_smooth.replace(0, np.nan)
            minus_di = 100 * minus_dm_smooth / tr_smooth.replace(0, np.nan)

            # DX
            dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
            # ADX = DX 的 Wilder 平滑
            adx = self._wilder_smooth(dx.fillna(0), n)
            return adx
        except Exception as e:
            log.warning(f"ADX 计算失败: {e}")
            return pd.Series(dtype=float)

    def _calc_di(self, high: pd.Series, low: pd.Series, close: pd.Series, plus: bool) -> pd.Series:
        n = self.adx_period
        tr = self._true_range(high, low, close)
        up_move = high.diff()
        down_move = -low.diff()
        if plus:
            dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=high.index)
        else:
            dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=high.index)
        tr_smooth = self._wilder_smooth(tr, n)
        dm_smooth = self._wilder_smooth(dm, n)
        return 100 * dm_smooth / tr_smooth.replace(0, np.nan)

    def _true_range(self, high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
        prev_close = close.shift(1)
        tr1 = high - low
        tr2 = (high - prev_close).abs()
        tr3 = (low - prev_close).abs()
        return pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    def _wilder_smooth(self, series: pd.Series, period: int) -> pd.Series:
        """Wilder 平滑：首值 = SMA，后续 = (prev*(period-1) + current) / period"""
        if len(series) < period:
            return series.rolling(period, min_periods=len(series)).mean()
        # 首值 SMA
        result = pd.Series(np.nan, index=series.index)
        first_valid = series.iloc[:period].mean()
        result.iloc[period - 1] = first_valid
        for i in range(period, len(series)):
            prev = result.iloc[i - 1]
            if pd.isna(prev):
                continue
            result.iloc[i] = (prev * (period - 1) + series.iloc[i]) / period
        return result
