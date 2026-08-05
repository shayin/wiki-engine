"""基底计数 + 强势/弱势卖出信号

来源：《股票魔法师 Ⅱ》第 9 章 — 卖出并获利了结的时机。

核心规则：
| 基底序号 | 操作 |
|---------|------|
| 1-2 基底 | 🟢 买入/持有，可加仓 |
| 3-4 基底 | 🟡 持有，不加仓 |
| 5 基底   | 🔴 减仓 50% |
| 6+ 基底  | 🔴 强制清仓 |

辅助信号：
- 最大单日/单周跌幅（自第二阶段启动以来）→ 强制卖出
- 跌破突破点 -5%+ → 弱势卖出
- 连续 3 天收低于 50 日均线 → 弱势卖出
- 放量反转（量大 + 跌）→ 顶部信号
"""
from __future__ import annotations

import pandas as pd

from .base import BaseSignal, SignalResult


class BaseCountingSignal(BaseSignal):
    """基底计数 + 卖出信号检测

    value 范围：
        +1.0 ~ +0.5：持有/可加仓（基底 1-2，无弱势信号）
         0.0      ：中性（基底 3-4）
        -0.5      ：减仓（基底 5 或弱势信号出现）
        -1.0      ：强制清仓（基底 6+ 或最大单日跌幅）
    passed=True 表示出现卖出信号
    """
    name = "base_counting"
    threshold = -0.5  # value ≤ -0.5 触发卖出

    # 基底识别参数（简化版）
    base_lookback_max: int = 600         # 最长回看 600 日（≈ 2 年，覆盖完整第二阶段）
    consolidation_min_days: int = 15     # 盘整 ≥ 15 天才算一个基底
    drawdown_for_base_pct: float = 0.10  # 盘整低点距前高 ≥ 10% 才算基底
    rally_between_bases_pct: float = 0.15  # 基底间反弹 ≥ 15% 才算独立基底

    # 卖出信号阈值
    big_drop_pct: float = -0.05          # 单日跌幅 ≤ -5% 算大幅下跌
    breakout_break_pct: float = -0.05    # 跌破突破点 -5%
    ma50_break_days: int = 3             # 连续 N 天收低于 50 日均线

    def evaluate(self, ticker: str, df: pd.DataFrame) -> SignalResult:
        reasons: list[str] = []
        details: dict = {}

        min_required = 252  # 至少 1 年数据
        if len(df) < min_required:
            return SignalResult(
                ticker=ticker, signal_name=self.name, value=0.0, passed=False,
                details={"error": f"数据不足（需 {min_required} 行）"},
                reasons=[f"数据不足（需 ≥ {min_required} 行）"],
            )

        recent = df.tail(self.base_lookback_max).copy()
        close = recent["close"]
        high = recent["high"]
        low = recent["low"]
        volume = recent["volume"]

        last_close = close.iloc[-1]

        # ============ 1. 基底计数（简化版）============
        # 用月线峰谷识别基底：每次显著回撤（≥ 10%）+ 反弹（≥ 15%）= 1 个基底
        bases = self._count_bases(close, high, low)
        n_bases = len(bases)
        details["base_count"] = n_bases
        details["bases"] = bases[-6:]  # 最近 6 个

        # ============ 2. 最大单日跌幅 ============
        daily_ret = close.pct_change()
        max_drop_idx = daily_ret.idxmin()
        max_drop = daily_ret.loc[max_drop_idx]
        is_record_drop = max_drop <= self.big_drop_pct and max_drop_idx == close.index[-1]
        # 自第二阶段启动（最近的低点）以来的最大单日跌幅
        phase2_start = self._find_phase2_start(close)
        if phase2_start is not None:
            phase2_ret = close.loc[phase2_start:].pct_change()
            phase2_max_drop = phase2_ret.min()
            phase2_max_drop_idx = phase2_ret.idxmin()
            is_record_drop = (
                phase2_max_drop <= self.big_drop_pct
                and phase2_max_drop_idx == close.index[-1]
            )
            details["phase2_max_drop_pct"] = float(phase2_max_drop * 100)
            details["phase2_max_drop_date"] = str(phase2_max_drop_idx)
        details["last_day_drop_pct"] = float(daily_ret.iloc[-1] * 100)

        # ============ 3. 跌破突破点（用最近 peak）============
        recent_peak = high.tail(60).max()
        peak_to_now = (last_close - recent_peak) / recent_peak
        broke_breakout = peak_to_now <= self.breakout_break_pct
        details["recent_peak"] = float(recent_peak)
        details["peak_to_now_pct"] = float(peak_to_now * 100)

        # ============ 4. 连续 N 天收低于 50 日均线 ============
        ma50 = close.rolling(50).mean()
        below_ma50 = (close < ma50).iloc[-self.ma50_break_days:].all() if len(close) >= self.ma50_break_days else False
        details["below_ma50_count"] = int((close.tail(10) < ma50.tail(10)).sum())

        # ============ 5. 放量反转（最近 5 天内有 1 天量大 + 跌）============
        vol_ma50 = volume.rolling(50).mean()
        last5_vol = volume.tail(5)
        last5_ret = close.pct_change().tail(5)
        reversal = ((last5_vol > vol_ma50.tail(5) * 1.5) & (last5_ret < -0.03)).any()
        details["reversal_signal"] = bool(reversal)

        # ============ 评分 ============
        # 基底生命周期基础分
        if n_bases <= 2:
            base_score = 0.8
            reasons.append(f"🟢 第 {n_bases} 基底（早期，可持有/加仓）")
        elif n_bases <= 4:
            base_score = 0.0
            reasons.append(f"🟡 第 {n_bases} 基底（中期，持有不加仓）")
        elif n_bases == 5:
            base_score = -0.5
            reasons.append(f"🔴 第 {n_bases} 基底（后期，减仓 50%）")
        else:
            base_score = -1.0
            reasons.append(f"🔴 第 {n_bases} 基底（顶部，强制清仓）")

        # 弱势卖出信号
        weak_signals = []
        if is_record_drop:
            weak_signals.append("阶段内最大单日跌幅（今日）")
        if broke_breakout:
            weak_signals.append(f"跌破突破点 {peak_to_now*100:.1f}%")
        if below_ma50:
            weak_signals.append(f"连续 {self.ma50_break_days} 天收低于 MA50")
        if reversal:
            weak_signals.append("放量反转")

        # 综合评分
        # 基底分 + 最弱信号
        weak_penalty = 0.0
        if is_record_drop or n_bases >= 6:
            weak_penalty = -1.0
        elif n_bases == 5 or (broke_breakout and below_ma50):
            weak_penalty = -0.5
        elif weak_signals:
            weak_penalty = -0.3

        # value 范围 [-1, +1]：base_score 与 weak_penalty 取较负者
        value = min(base_score, weak_penalty) if weak_penalty < 0 else base_score
        # 注意：本信号 passed=True 表示"应该卖出"
        passed = value <= self.threshold

        details.update({
            "weak_signals": weak_signals,
            "is_record_drop": bool(is_record_drop),
            "broke_breakout": bool(broke_breakout),
            "below_ma50_streak": bool(below_ma50),
            "action": self._action_label(n_bases, weak_signals),
            "base_score": float(base_score),
            "weak_penalty": float(weak_penalty),
        })

        if weak_signals:
            reasons.append(f"⚡ 弱势信号：{'；'.join(weak_signals)}")
        reasons.append(f"action = {details['action']}")

        # 反转语义：返回 SignalResult 时 passed=True 表示触发卖出
        return SignalResult(
            ticker=ticker, signal_name=self.name, value=float(value), passed=bool(passed),
            details=details, reasons=reasons,
        ).clamp()

    def _count_bases(self, close: pd.Series, high: pd.Series, low: pd.Series) -> list[dict]:
        """简化基底识别：在回看窗口内用月线（≈ 21 日）级别峰谷识别"""
        # 用月线重采样
        monthly = close.resample("21B").last().dropna()
        if len(monthly) < 6:
            return []

        # 找显著回撤 + 反弹
        bases: list[dict] = []
        i = 1
        while i < len(monthly) - 1:
            # 找峰
            if monthly.iloc[i] >= monthly.iloc[i - 1] and monthly.iloc[i] >= monthly.iloc[i + 1]:
                peak_val = monthly.iloc[i]
                peak_date = monthly.index[i]
                # 寻找后续回撤 ≥ 10%
                j = i + 1
                trough_val = peak_val
                trough_idx = i
                while j < len(monthly):
                    if monthly.iloc[j] < trough_val:
                        trough_val = monthly.iloc[j]
                        trough_idx = j
                    if (peak_val - trough_val) / peak_val >= self.drawdown_for_base_pct:
                        # 找到回撤，确认基底
                        bases.append({
                            "peak_date": str(peak_date),
                            "peak_price": float(peak_val),
                            "trough_date": str(monthly.index[trough_idx]),
                            "trough_price": float(trough_val),
                            "drawdown_pct": float((peak_val - trough_val) / peak_val * 100),
                        })
                        i = trough_idx
                        break
                    j += 1
            i += 1
        return bases

    def _find_phase2_start(self, close: pd.Series) -> pd.Timestamp | None:
        """简化：找最近一次 close 上穿 MA200 的点作为第二阶段起点"""
        if len(close) < 250:
            return None
        ma200 = close.rolling(200).mean()
        above = close > ma200
        # 找最近的 above 从 False → True 的转折点
        crossover = (above.shift(1) == False) & (above == True)
        if not crossover.any():
            return None
        last_cross = crossover[crossover].index[-1]
        return last_cross

    def _action_label(self, n_bases: int, weak_signals: list[str]) -> str:
        if weak_signals and ("阶段内最大单日跌幅（今日）" in weak_signals or n_bases >= 6):
            return "STRONG_SELL"
        if n_bases >= 6:
            return "STRONG_SELL"
        if n_bases == 5:
            return "REDUCE_50"
        if weak_signals:
            return "SELL"
        if n_bases <= 2:
            return "HOLD_OR_ADD"
        return "HOLD"
