"""Support/Resistance Polarity Signal — Murphy《金融市场技术分析》Ch3

来源：金融市场技术分析 skill `support-resistance-polarity`。

核心算法（skill E 段）：
1. **强度评分**（最大 125）：接触次数（1-5）× 时间跨度（1-5）× 成交量（1-5）
   - 强位（≥ 75）：多次触及 + 长时间 + 高量能 = 高确信度
2. **有效突破**：收盘突破 + 幅度 ≥ 3% + 量能 ≥ 150% 均量
3. **角色互换（Polarity Flip）**：突破阻力后回踩不破 → 原阻力转为支撑
4. **买卖点**：强支撑（≥ 75）上方 1-2% 买入；止损设支撑下方 3-5%

输出 SignalResult.value：
- 1.0：刚有效突破强阻力（突破且强度 ≥ 75）
- 0.5：在强支撑上方 1-2%，未突破
- 0.0：在支撑/阻力之间
- -0.5：跌破强支撑
- -1.0：触及强阻力且未突破
"""
from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import pandas as pd

from .base import BaseSignal, SignalResult
from ..utils.swing import find_swings

log = logging.getLogger(__name__)


class SupportResistanceSignal(BaseSignal):
    name = "support_resistance"
    threshold = 0.5

    lookback: int = 252             # 评估窗口（12 月）
    swing_window: int = 5
    level_cluster_pct: float = 0.02 # 价格聚集 ±2% 合并为同一水平
    strong_level_threshold: int = 75  # 强度 ≥ 75 = 强位

    # 有效突破阈值
    breakout_min_pct: float = 0.03           # 突破幅度 ≥ 3%
    breakout_vol_ratio: float = 1.5          # 量能 ≥ 150% 均量
    breakout_lookback: int = 10              # 检查最近 N 日内的突破

    # 角色互换：突破后回踩最深不超过 N%
    flip_pullback_max_pct: float = -0.05

    def __init__(self, threshold: Optional[float] = None, **kwargs):
        super().__init__(threshold=threshold, **kwargs)

    # -------------------------- 水平识别 --------------------------

    def _cluster_levels(
        self, swings: list[tuple[int, float]], close: pd.Series, volume: pd.Series
    ) -> list[dict]:
        """swing 点聚成水平位（同 ±2% 内合并）

        Returns:
            [{"price": float, "touches": int, "span_days": int,
              "avg_volume_ratio": float, "strength": int}, ...]
        """
        if not swings:
            return []

        # 按价格排序后聚类
        sorted_swings = sorted(swings, key=lambda x: x[1])
        clusters: list[list[tuple[int, float]]] = []
        for idx, price in sorted_swings:
            placed = False
            for c in clusters:
                c_price = np.mean([p for _, p in c])
                if abs(price - c_price) / c_price <= self.level_cluster_pct:
                    c.append((idx, price))
                    placed = True
                    break
            if not placed:
                clusters.append([(idx, price)])

        levels: list[dict] = []
        vol_ma = volume.rolling(50).mean() if volume is not None else None
        for c in clusters:
            prices = [p for _, p in c]
            indices = [i for i, _ in c]
            avg_price = float(np.mean(prices))
            touches = len(c)
            span_days = (max(indices) - min(indices)) if len(c) > 1 else 0

            # 接触点平均量能（相对 50 日均量）
            vol_ratios: list[float] = []
            if vol_ma is not None:
                for i in indices:
                    if 0 <= i < len(volume) and not pd.isna(vol_ma.iloc[i]):
                        vm = vol_ma.iloc[i]
                        if vm > 0:
                            vol_ratios.append(volume.iloc[i] / vm)
            avg_vol_ratio = float(np.mean(vol_ratios)) if vol_ratios else 1.0

            # 强度评分：touches × span × volume，各映射到 1-5
            t_score = min(5, touches)
            s_score = min(5, max(1, int(span_days / 30) + 1))  # 30 天/单位
            v_score = min(5, max(1, int(avg_vol_ratio * 2.5)))  # ratio 2 → 5 分
            strength = t_score * s_score * v_score

            levels.append({
                "price": avg_price,
                "touches": touches,
                "span_days": span_days,
                "avg_volume_ratio": avg_vol_ratio,
                "strength": strength,
            })
        return levels

    def _nearest_levels(self, levels: list[dict], current_price: float) -> tuple[dict | None, dict | None]:
        """找最近的支撑（下方）和阻力（上方）"""
        supports = [l for l in levels if l["price"] < current_price]
        resistances = [l for l in levels if l["price"] > current_price]
        sup = max(supports, key=lambda x: x["price"]) if supports else None
        res = min(resistances, key=lambda x: x["price"]) if resistances else None
        return sup, res

    # -------------------------- 突破与角色互换 --------------------------

    def _detect_breakout(
        self, df: pd.DataFrame, resistance: dict | None
    ) -> tuple[bool, dict]:
        """检测最近 N 日内是否有有效突破"""
        if resistance is None:
            return False, {"note": "无阻力位"}
        if len(df) < self.breakout_lookback + 1:
            return False, {"note": "数据不足"}

        close = df["close"]
        volume = df.get("volume")
        level = resistance["price"]
        recent = df.tail(self.breakout_lookback + 1)

        # 检查最近 N 日内是否有突破日
        vol_ma = volume.rolling(50).mean() if volume is not None else None
        for i in range(1, len(recent)):
            prev_close = recent["close"].iloc[i - 1]
            cur_close = recent["close"].iloc[i]
            # 前一日在阻力下/附近，当日收盘明显突破
            if prev_close <= level * 1.005 and cur_close > level:
                pct = (cur_close - level) / level
                vol_ok = True
                vol_ratio = 1.0
                if vol_ma is not None:
                    idx_in_recent = len(recent) - len(recent) + i
                    ma_val = vol_ma.reindex(recent.index).iloc[i]
                    if not pd.isna(ma_val) and ma_val > 0:
                        vol_ratio = recent["volume"].iloc[i] / ma_val
                        vol_ok = vol_ratio >= self.breakout_vol_ratio
                if pct >= self.breakout_min_pct and vol_ok:
                    return True, {
                        "breakout_date": str(recent.index[i].date()),
                        "breakout_pct": float(pct),
                        "vol_ratio": float(vol_ratio),
                    }
        return False, {"note": "近期无有效突破"}

    def _detect_polarity_flip(
        self, df: pd.DataFrame, breakout_info: dict, resistance: dict | None
    ) -> bool:
        """突破后回踩不破 = 角色互换（支撑转阻力反转）"""
        if not breakout_info or resistance is None:
            return False
        if "breakout_date" not in breakout_info:
            return False

        try:
            bd = pd.Timestamp(breakout_info["breakout_date"])
        except Exception:
            return False
        if bd not in df.index:
            return False
        post = df.loc[df.index > bd]
        if len(post) < 3:
            return False
        level = resistance["price"]
        # 突破后最低收盘不应跌破 level × (1 + flip_pullback_max_pct)
        min_close = post["close"].min()
        threshold = level * (1 + self.flip_pullback_max_pct)
        return min_close >= threshold

    # -------------------------- 主评估 --------------------------

    def evaluate(self, ticker: str, df: pd.DataFrame, pit_date: Optional[pd.Timestamp] = None) -> SignalResult:
        if len(df) < 60:
            return SignalResult(
                ticker=ticker, signal_name=self.name, value=0.0, passed=False,
                details={"error": "数据不足（需 ≥ 60 行）"},
                reasons=[f"数据不足（需 ≥ 60 行，当前 {len(df)}）"],
            )

        window = df.tail(self.lookback) if len(df) > self.lookback else df
        close = window["close"]
        volume = window.get("volume")

        highs, lows = find_swings(close, window=self.swing_window)
        # 合并 swing highs + lows 作为关键位候选
        all_swings = [(i, p) for i, p in highs] + [(i, p) for i, p in lows]
        levels = self._cluster_levels(all_swings, close, volume if volume is not None else pd.Series(dtype=float))

        current_price = float(close.iloc[-1])
        support, resistance = self._nearest_levels(levels, current_price)

        sup_price = support["price"] if support else None
        res_price = resistance["price"] if resistance else None
        sup_strength = support["strength"] if support else 0
        res_strength = resistance["strength"] if resistance else 0

        # 有效突破检测
        breakout_valid, breakout_info = self._detect_breakout(df, resistance)
        polarity_flip = self._detect_polarity_flip(df, breakout_info, resistance) if breakout_valid else False

        # 综合评分映射
        if breakout_valid and res_strength >= self.strong_level_threshold:
            value = 1.0
            label = "有效突破强阻力（极强）"
        elif breakout_valid:
            value = 0.7
            label = "有效突破（中等强度）"
        elif support and sup_strength >= self.strong_level_threshold:
            # 距强支撑 ≤ 5% 上方
            dist_to_sup = (current_price - sup_price) / sup_price
            if 0 <= dist_to_sup <= 0.05:
                value = 0.5
                label = f"贴近强支撑（强度 {sup_strength}）"
            else:
                value = 0.3
                label = "强支撑上方"
        elif resistance and res_strength >= self.strong_level_threshold and current_price < res_price:
            # 接近强阻力（≤ 3%）
            dist_to_res = (res_price - current_price) / current_price
            if dist_to_res <= 0.03:
                value = -1.0
                label = f"触及强阻力（强度 {res_strength}）"
            else:
                value = 0.0
                label = "强阻力下方"
        elif support and current_price < sup_price * (1 + self.flip_pullback_max_pct):
            # 跌破支撑
            value = -0.5
            label = "跌破支撑"
        else:
            value = 0.0
            label = "支撑阻力之间"

        details = {
            "current_price": current_price,
            "nearest_support": sup_price,
            "nearest_resistance": res_price,
            "support_strength": int(sup_strength),
            "resistance_strength": int(res_strength),
            "breakout_valid": bool(breakout_valid),
            "breakout_detail": breakout_info,
            "polarity_flip": bool(polarity_flip),
            "level_count": len(levels),
            "label": label,
        }
        reasons = [
            f"支撑/阻力：{label}",
            f"  最近支撑 {sup_price:.2f}（强度 {sup_strength}）" if sup_price else "  无支撑位",
            f"  最近阻力 {res_price:.2f}（强度 {res_strength}）" if res_price else "  无阻力位",
        ]
        if polarity_flip:
            reasons.append("  ⚡ 角色互换：突破后回踩不破，原阻力已转为支撑")

        passed = value >= self.threshold
        return SignalResult(
            ticker=ticker, signal_name=self.name,
            value=float(value), passed=bool(passed),
            details=details, reasons=reasons,
        ).clamp()
