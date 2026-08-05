"""Minervini 趋势模板 8 条严格筛选

来源：《股票魔法师 Ⅱ》第 6 章 — SEPA 方法论第一道筛子。
原书明确"95% 的超级股票涨幅发生在第二阶段"，所以不在第二阶段 = 直接放弃。

8 条规则全部满足 = value = 1.0，每条 0.125。
"""
from __future__ import annotations

import pandas as pd

from .base import BaseSignal, SignalResult


class TrendTemplateSignal(BaseSignal):
    """Minervini 趋势模板 8 条（精确定义版）"""
    name = "trend_template"
    threshold = 0.875  # 8 条中至少 7 条通过（允许 1 条临界容差）

    fast_ma: int = 50
    mid_ma: int = 150
    slow_ma: int = 200
    slow_ma_rise_days: int = 22  # 200MA 上涨"至少 1 个月"

    # 52 周位置
    min_above_52w_low: float = 0.25   # 规则 6：≥ 25%
    max_below_52w_high: float = 0.25  # 规则 7：≤ 25%

    def evaluate(self, ticker: str, df: pd.DataFrame) -> SignalResult:
        reasons: list[str] = []
        details: dict = {}

        min_required = self.slow_ma + self.slow_ma_rise_days + 10
        if len(df) < min_required:
            return SignalResult(
                ticker=ticker, signal_name=self.name, value=0.0, passed=False,
                details={"error": f"数据不足（需 {min_required} 行）"},
                reasons=[f"数据不足（需 ≥ {min_required} 行）"],
            )

        close = df["close"]
        last_close = close.iloc[-1]

        ma_fast = close.rolling(self.fast_ma).mean()
        ma_mid = close.rolling(self.mid_ma).mean()
        ma_slow = close.rolling(self.slow_ma).mean()

        last_ma_fast = ma_fast.iloc[-1]
        last_ma_mid = ma_mid.iloc[-1]
        last_ma_slow = ma_slow.iloc[-1]
        slow_ma_past = ma_slow.iloc[-self.slow_ma_rise_days - 1]

        # 52 周高低
        window_252 = close.tail(252) if len(close) >= 252 else close
        high_52w = window_252.max()
        low_52w = window_252.min()

        # ============ 8 条规则 ============
        c1 = last_close > last_ma_mid          # 规则1：股价 > 150MA
        c2 = last_close > last_ma_slow         # 规则2：股价 > 200MA
        c3 = last_ma_mid > last_ma_slow        # 规则3：150MA > 200MA
        c4 = last_ma_slow > slow_ma_past       # 规则4：200MA 上涨 ≥ 1 个月
        c5 = last_ma_fast > last_ma_mid > last_ma_slow  # 规则5：50MA > 150MA > 200MA
        dist_above_low = (last_close - low_52w) / low_52w if low_52w > 0 else 0
        c6 = dist_above_low >= self.min_above_52w_low    # 规则6：距 52 周低 ≥ 25%
        dist_below_high = (last_close - high_52w) / high_52w  # 负数
        c7 = dist_below_high >= -self.max_below_52w_high  # 规则7：距 52 周高 ≤ 25%
        c8 = last_close > last_ma_fast         # 规则8：股价 > 50MA（简化版，原书要求突破前期底部）

        conditions = [c1, c2, c3, c4, c5, c6, c7, c8]
        score = sum(conditions) / 8.0
        passed = score >= self.threshold

        details.update({
            "close": float(last_close),
            f"ma{self.fast_ma}": float(last_ma_fast),
            f"ma{self.mid_ma}": float(last_ma_mid),
            f"ma{self.slow_ma}": float(last_ma_slow),
            "slow_ma_rising": bool(c4),
            "slow_ma_past": float(slow_ma_past),
            "high_52w": float(high_52w),
            "low_52w": float(low_52w),
            "dist_above_low_pct": float(dist_above_low * 100),
            "dist_below_high_pct": float(dist_below_high * 100),
            "conditions": {
                "c1_close_gt_ma150": bool(c1),
                "c2_close_gt_ma200": bool(c2),
                "c3_ma150_gt_ma200": bool(c3),
                "c4_ma200_rising": bool(c4),
                "c5_ma50_gt_ma150_gt_ma200": bool(c5),
                "c6_above_low_25pct": bool(c6),
                "c7_below_high_25pct": bool(c7),
                "c8_close_gt_ma50": bool(c8),
            },
            "n_passed": int(sum(conditions)),
        })

        reasons.append(f"1. 价格 {last_close:.2f} {'✓' if c1 else '✗'} > MA{self.mid_ma} {last_ma_mid:.2f}")
        reasons.append(f"2. 价格 {last_close:.2f} {'✓' if c2 else '✗'} > MA{self.slow_ma} {last_ma_slow:.2f}")
        reasons.append(f"3. MA{self.mid_ma} {last_ma_mid:.2f} {'✓' if c3 else '✗'} > MA{self.slow_ma} {last_ma_slow:.2f}")
        reasons.append(f"4. MA{self.slow_ma} 上涨 {'✓' if c4 else '✗'}（{self.slow_ma_rise_days}日前 {slow_ma_past:.2f} → 今 {last_ma_slow:.2f}）")
        reasons.append(f"5. MA{self.fast_ma} > MA{self.mid_ma} > MA{self.slow_ma} {'✓' if c5 else '✗'}（{last_ma_fast:.2f}/{last_ma_mid:.2f}/{last_ma_slow:.2f}）")
        reasons.append(f"6. 距 52 周低 +{dist_above_low*100:.1f}% {'✓' if c6 else '✗'}（要求 ≥ +{self.min_above_52w_low*100:.0f}%）")
        reasons.append(f"7. 距 52 周高 {dist_below_high*100:.1f}% {'✓' if c7 else '✗'}（要求 ≥ -{self.max_below_52w_high*100:.0f}%）")
        reasons.append(f"8. 价格 > MA{self.fast_ma} {'✓' if c8 else '✗'}")

        return SignalResult(
            ticker=ticker, signal_name=self.name, value=float(score), passed=bool(passed),
            details=details, reasons=reasons,
        ).clamp()
