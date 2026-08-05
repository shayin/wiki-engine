"""Dow Theory Trend Phases Signal — Murphy《金融市场技术分析》Ch3 + skill 蒸馏

道氏三阶段：吸筹 / 公众参与 / 派发。
Hamilton 经典原版 + Murphy 现代量化打分版。

量价打分表（skill E 段）：
- 价格走势：HH/HL（高点抬高/低点抬高）= 正；LH/LL（高点降低/低点降低）= 负
- 成交量：上涨放量/下跌缩量 = 正；背离 = 负
- 均线排列：MA20 > MA50 > MA200 = 正；空排 = 负
- 波动率：ATR 收缩 = 吸筹特征；ATR 放大 = 派发特征
- 缺口：向上突破缺口 = 公众参与启动信号

阶段判定：
- score ≥ +3 → 吸筹（accumulation）
- 0 ≤ score < +3 → 公众参与（public_participation）
- score ≤ -3 → 派发（distribution）

输出 SignalResult.value 映射：吸筹 → 1.0；公众参与 → 0.5；派发 → -1.0。
"""
from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import pandas as pd

from .base import BaseSignal, SignalResult
from ..utils.swing import find_swings

log = logging.getLogger(__name__)


class DowPhasesSignal(BaseSignal):
    name = "dow_phases"
    threshold = 0.5

    # 打分窗口
    lookback: int = 200            # 评估窗口（≈ 9 个月）
    swing_window: int = 5          # swing 检测窗口
    vol_ma_window: int = 50        # 量能均线
    atr_window: int = 14
    atr_lookback: int = 60         # ATR 历史窗口（计算波动率趋势）

    # 阈值
    accumulation_threshold: int = 3     # ≥ +3 = 吸筹
    distribution_threshold: int = -3    # ≤ -3 = 派发

    def __init__(self, threshold: Optional[float] = None, **kwargs):
        super().__init__(threshold=threshold, **kwargs)

    # -------------------------- 子打分 --------------------------

    def _price_structure_score(self, df: pd.DataFrame) -> tuple[int, dict]:
        """价格结构打分：HH/HL = +2；LH/LL = -2；混合 = 0"""
        close = df["close"]
        highs, lows = find_swings(close, window=self.swing_window)
        if len(highs) < 2 or len(lows) < 2:
            return 0, {"note": "swing 不足"}

        last_highs = highs[-3:]
        last_lows = lows[-3:]
        hh_count = sum(1 for i in range(1, len(last_highs)) if last_highs[i][1] > last_highs[i-1][1])
        lh_count = sum(1 for i in range(1, len(last_highs)) if last_highs[i][1] < last_highs[i-1][1])
        hl_count = sum(1 for i in range(1, len(last_lows)) if last_lows[i][1] > last_lows[i-1][1])
        ll_count = sum(1 for i in range(1, len(last_lows)) if last_lows[i][1] < last_lows[i-1][1])

        if hh_count > lh_count and hl_count > ll_count:
            return 2, {"trend": "HH/HL", "hh": hh_count, "hl": hl_count}
        if lh_count > hh_count and ll_count > hl_count:
            return -2, {"trend": "LH/LL", "lh": lh_count, "ll": ll_count}
        return 0, {"trend": "mixed", "hh": hh_count, "lh": lh_count, "hl": hl_count, "ll": ll_count}

    def _volume_score(self, df: pd.DataFrame) -> tuple[int, dict]:
        """量能打分：上涨日放量 + 下跌日缩量 = 正"""
        if "volume" not in df.columns:
            return 0, {"note": "无 volume"}
        close = df["close"]
        vol = df["volume"]
        if len(close) < self.vol_ma_window + 10:
            return 0, {"note": "数据不足"}
        recent = close.tail(self.vol_ma_window)
        recent_vol = vol.tail(self.vol_ma_window)
        vol_ma = vol.rolling(self.vol_ma_window).mean().iloc[-1]
        if pd.isna(vol_ma) or vol_ma <= 0:
            return 0, {"note": "vol_ma 不可用"}

        daily_ret = recent.pct_change()
        # 上涨日平均量 / 下跌日平均量 vs 50 日均量
        up_days = daily_ret > 0
        down_days = daily_ret < 0
        if up_days.sum() == 0 or down_days.sum() == 0:
            return 0, {"note": "单边市"}
        up_vol = recent_vol.tail(self.vol_ma_window - 1)[up_days.values[1:]].mean()
        down_vol = recent_vol.tail(self.vol_ma_window - 1)[down_days.values[1:]].mean()
        if pd.isna(up_vol) or pd.isna(down_vol) or down_vol == 0:
            return 0, {"note": "量能计算失败"}

        ratio = up_vol / down_vol
        if ratio >= 1.3:
            return 2, {"up_down_vol_ratio": float(ratio)}
        if ratio >= 1.1:
            return 1, {"up_down_vol_ratio": float(ratio)}
        if ratio <= 0.7:
            return -2, {"up_down_vol_ratio": float(ratio)}
        if ratio <= 0.9:
            return -1, {"up_down_vol_ratio": float(ratio)}
        return 0, {"up_down_vol_ratio": float(ratio)}

    def _ma_alignment_score(self, df: pd.DataFrame) -> tuple[int, dict]:
        """均线排列：MA20 > MA50 > MA200 = +2；空排 = -2；其他 0/±1"""
        close = df["close"]
        if len(close) < 200:
            # MA200 不可得
            if len(close) < 50:
                return 0, {"note": "数据不足"}
            ma20 = close.rolling(20).mean().iloc[-1]
            ma50 = close.rolling(50).mean().iloc[-1]
            if pd.notna(ma20) and pd.notna(ma50):
                if ma20 > ma50:
                    return 1, {"ma20": float(ma20), "ma50": float(ma50), "ma200": None}
                return -1, {"ma20": float(ma20), "ma50": float(ma50), "ma200": None}
            return 0, {"note": "MA 计算失败"}

        ma20 = close.rolling(20).mean().iloc[-1]
        ma50 = close.rolling(50).mean().iloc[-1]
        ma200 = close.rolling(200).mean().iloc[-1]
        if any(pd.isna(x) for x in [ma20, ma50, ma200]):
            return 0, {"note": "MA NaN"}

        if ma20 > ma50 > ma200:
            return 2, {"ma20": float(ma20), "ma50": float(ma50), "ma200": float(ma200), "aligned": "bull"}
        if ma20 < ma50 < ma200:
            return -2, {"ma20": float(ma20), "ma50": float(ma50), "ma200": float(ma200), "aligned": "bear"}
        if ma20 > ma50:
            return 1, {"ma20": float(ma20), "ma50": float(ma50), "ma200": float(ma200), "aligned": "weak_bull"}
        return -1, {"ma20": float(ma20), "ma50": float(ma50), "ma200": float(ma200), "aligned": "weak_bear"}

    def _volatility_score(self, df: pd.DataFrame) -> tuple[int, dict]:
        """波动率打分：ATR 收缩 = +1（吸筹特征）；ATR 放大 + 价格高位 = -1（派发）"""
        high, low, close = df["high"], df["low"], df["close"]
        if len(close) < self.atr_lookback + self.atr_window:
            return 0, {"note": "数据不足"}
        tr = pd.concat([
            high - low,
            (high - close.shift(1)).abs(),
            (low - close.shift(1)).abs(),
        ], axis=1).max(axis=1)
        atr = tr.rolling(self.atr_window).mean()
        recent_atr = atr.iloc[-1]
        hist_atr = atr.iloc[-self.atr_lookback:].mean()
        if pd.isna(recent_atr) or pd.isna(hist_atr) or hist_atr == 0:
            return 0, {"note": "ATR NaN"}
        ratio = recent_atr / hist_atr
        # 价格位置（相对 200 日 high）
        price_pos = 0.5
        if len(close) >= 200:
            hh = close.tail(200).max()
            ll = close.tail(200).min()
            if hh > ll:
                price_pos = (close.iloc[-1] - ll) / (hh - ll)

        if ratio <= 0.8 and price_pos < 0.5:
            # 波动率收缩 + 低位 = 吸筹
            return 1, {"atr_ratio": float(ratio), "price_pos": float(price_pos)}
        if ratio >= 1.3 and price_pos > 0.7:
            # 波动率放大 + 高位 = 派发
            return -1, {"atr_ratio": float(ratio), "price_pos": float(price_pos)}
        return 0, {"atr_ratio": float(ratio), "price_pos": float(price_pos)}

    def _gap_score(self, df: pd.DataFrame) -> tuple[int, dict]:
        """缺口打分：近 20 日向上突破缺口 = +1；向下突破缺口 = -1"""
        if len(df) < 21:
            return 0, {"note": "数据不足"}
        recent = df.tail(21)
        prev_close = recent["close"].shift(1)
        gaps = recent["open"] - prev_close
        up_gaps = (gaps > 0).sum()
        down_gaps = (gaps < 0).sum()
        # 缺口幅度过滤（≥ 0.5% 才算突破缺口）
        sig_up = ((gaps / prev_close > 0.005) & (gaps > 0)).sum()
        sig_down = ((gaps / prev_close < -0.005) & (gaps < 0)).sum()

        if sig_up > sig_down and sig_up >= 1:
            return 1, {"sig_up_gaps": int(sig_up), "sig_down_gaps": int(sig_down)}
        if sig_down > sig_up and sig_down >= 1:
            return -1, {"sig_up_gaps": int(sig_up), "sig_down_gaps": int(sig_down)}
        return 0, {"sig_up_gaps": int(sig_up), "sig_down_gaps": int(sig_down)}

    # -------------------------- 主评估 --------------------------

    def evaluate(self, ticker: str, df: pd.DataFrame, pit_date: Optional[pd.Timestamp] = None) -> SignalResult:
        if len(df) < 60:
            return SignalResult(
                ticker=ticker, signal_name=self.name, value=0.0, passed=False,
                details={"error": "数据不足（需 ≥ 60 行）"},
                reasons=[f"数据不足（需 ≥ 60 行，当前 {len(df)}）"],
            )

        # 截断到 lookback 窗口
        window = df.tail(self.lookback) if len(df) > self.lookback else df

        price_score, price_d = self._price_structure_score(window)
        vol_score, vol_d = self._volume_score(window)
        ma_score, ma_d = self._ma_alignment_score(window)
        volat_score, volat_d = self._volatility_score(window)
        gap_score, gap_d = self._gap_score(window)

        total = price_score + vol_score + ma_score + volat_score + gap_score

        # 阶段映射
        if total >= self.accumulation_threshold:
            phase = "accumulation"
            value = 1.0
            phase_label = "吸筹"
        elif total <= self.distribution_threshold:
            phase = "distribution"
            value = -1.0
            phase_label = "派发"
        else:
            phase = "public_participation"
            value = 0.5 if total >= 0 else -0.5
            phase_label = "公众参与"

        subscores = {
            "price_structure": price_score,
            "volume": vol_score,
            "ma_alignment": ma_score,
            "volatility": volat_score,
            "gap": gap_score,
        }
        details = {
            "phase": phase,
            "phase_label": phase_label,
            "score": int(total),
            "subscores": subscores,
            "price_detail": price_d,
            "volume_detail": vol_d,
            "ma_detail": ma_d,
            "volatility_detail": volat_d,
            "gap_detail": gap_d,
        }
        reasons = [
            f"道氏阶段：{phase_label}（score={total}）",
            f"  价格 {price_score:+d} / 量能 {vol_score:+d} / 均线 {ma_score:+d} / 波动 {volat_score:+d} / 缺口 {gap_score:+d}",
        ]

        passed = value >= self.threshold
        return SignalResult(
            ticker=ticker, signal_name=self.name,
            value=float(value), passed=bool(passed),
            details=details, reasons=reasons,
        ).clamp()
