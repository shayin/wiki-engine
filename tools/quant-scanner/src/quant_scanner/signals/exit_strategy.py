"""出场策略信号（Exit Strategy）

补充 BaseCountingSignal 只判断"卖出时机"的不足，输出**具体的止损/止盈价位**和**时间止损**。

⚠️ **设计依据（HANDOFF 回测锚点，2026-07-19）**：

trend_template@60d 入场后三种出场策略 EV 对比：

| 策略 | EV | 盈亏比 | 结论 |
|------|-----|-------|------|
| **纯持有 60d（time exit）** | **+4.21%** | **2.12** | ✅ 最优 |
| 2×ATR 紧止损 | +0.34% | — | ❌ 60% 被洗出，截断赢家 |
| sell_signal 基底计数 | -0.32% | — | ❌ 过早退出 |

**核心结论**：任何提前退出（紧止损 / sell_signal）都因"截断赢家"降低 EV。

**本信号的默认参数对齐回测最优策略**：
- `atr_multiplier=3.0`（宽止损，避免被洗出）
- `max_holding_days=60`（对齐最优 time exit 周期）
- `target_reward_pct=0.20`（3:1 风险比，达到则主动止盈）
- MA 信号默认只触发 `TIGHTEN_STOP`/`WATCH` 警告，**不主动 EXIT**（避免截断赢家）
- 仅 `ma30_force=True`（跌破 MA30 = 趋势反转，必须 REDUCE）

来源：《股票魔法师 Ⅱ》第 9 章 + 《金融市场技术分析》第 13 章（Murphy）：

**三种止损规则**：

1. **ATR 止损**（波动率自适应）：
   - 入场后固定 stop = entry − k × ATR（k 通常 2-3）
   - 高波动股给宽空间（避免被洗出），低波动股给紧空间
   - 来源：Murphy「True Range 自适应止损」

2. **移动止损（trailing stop）**：
   - 持仓期间 stop = max(stop, high − k × ATR)
   - 上涨时跟随抬高，下跌时不动
   - 来源：Minervini「5/13/30 移动止损法」（5 日均线触发警告，13 日确认，30 日强制）

3. **时间止损**：
   - 持仓 N 天未达目标收益 → 自动平仓
   - 来源：Minervini「5 周定律」——5 周内未启动的票大概率是死钱
   - 配合回测锚点：trend_template@60d（胜率 60.3%，期望持有 60 交易日）

**输出 SignalResult**：
- value ∈ [0, 1]：当前持仓的安全度（1=非常安全，0=必须立刻离场）
- details 含：entry / current_stop / trailing_stop / target / holding_days / action
- passed=True 表示触发卖出信号（必须离场）

**典型用法**：
```python
from quant_scanner.signals.exit_strategy import ExitStrategySignal
sig = ExitStrategySignal(entry_price=180.0, entry_date="2026-06-01")
sr = sig.evaluate("NVDA", df)
# sr.details["current_stop"] = 168.5  # 当前止损价
# sr.details["action"] = "HOLD" / "TIGHTEN_STOP" / "EXIT_TIME" / "EXIT_STOP"
```
"""
from __future__ import annotations

import pandas as pd

from .base import BaseSignal, SignalResult


class ExitStrategySignal(BaseSignal):
    """出场策略：ATR + 移动止损 + 时间止损

    需要外部传入 entry_price 和 entry_date（默认用最近 low 作 entry 占位）。
    """
    name = "exit_strategy"
    threshold = 0.3  # value < 0.3 触发卖出

    # ATR 止损参数
    atr_window: int = 14
    atr_multiplier: float = 3.0  # 入场后 stop = entry - 3×ATR（trend@60d 回测锚点）

    # 移动止损参数（Minervini 5/13/30 法则）
    trailing_atr_multiplier: float = 2.5  # trailing = high - 2.5×ATR
    ma5_warning: bool = True  # 跌破 5 日均线 → 警告
    ma13_confirm: bool = True  # 跌破 13 日均线 → 确认走弱
    ma30_force: bool = True   # 跌破 30 日均线 → 强制减仓

    # 时间止损参数
    max_holding_days: int = 60  # 持仓 60 日不达目标 → 强制平仓（trend@60d 锚点）
    target_reward_pct: float = 0.20  # 目标收益 20%（3:1 风险比）

    # 入场参数（可由 kwargs 覆盖）
    entry_price: float | None = None  # 不传则用 df 最近 60 日 low 作为占位
    entry_date: str | None = None     # 不传则用 df.index[-60]

    def evaluate(self, ticker: str, df: pd.DataFrame) -> SignalResult:
        reasons: list[str] = []
        details: dict = {}

        if len(df) < self.atr_window + 5:
            return SignalResult(
                ticker=ticker, signal_name=self.name, value=0.0, passed=False,
                details={"error": "数据不足"},
                reasons=[f"数据不足（需 ≥ {self.atr_window + 5} 行）"],
            )

        # 解析入场点
        entry_price, entry_idx = self._resolve_entry(df)
        if entry_price is None or entry_idx is None:
            return SignalResult(
                ticker=ticker, signal_name=self.name, value=0.0, passed=False,
                details={"error": "无法确定入场点"},
                reasons=["无法确定入场点（请传 entry_price/entry_date 或保证数据足够）"],
            )

        df_post = df.loc[entry_idx:]
        if len(df_post) < 2:
            return SignalResult(
                ticker=ticker, signal_name=self.name, value=0.0, passed=False,
                details={"error": "入场后数据不足"},
                reasons=["入场后数据不足"],
            )

        high = df_post["high"]
        low = df_post["low"]
        close = df_post["close"]

        last_close = close.iloc[-1]
        holding_days = len(df_post)

        # ATR（用全样本计算，避免小样本偏差）
        full_high = df["high"]
        full_low = df["low"]
        full_close = df["close"]
        prev_close = full_close.shift(1)
        tr = pd.concat(
            [full_high - full_low,
             (full_high - prev_close).abs(),
             (full_low - prev_close).abs()],
            axis=1,
        ).max(axis=1)
        atr = tr.ewm(alpha=1.0 / self.atr_window, adjust=False,
                     min_periods=self.atr_window).mean()
        last_atr = float(atr.iloc[-1])

        # ========== 三种止损价 ==========
        # 1. 初始 ATR 止损（入场时定下）
        initial_stop = entry_price - self.atr_multiplier * last_atr
        details["initial_stop"] = float(initial_stop)

        # 2. 移动止损（trailing，跟随最高点上移）
        max_high_since_entry = float(high.max())
        trailing_stop = max_high_since_entry - self.trailing_atr_multiplier * last_atr
        trailing_stop = max(trailing_stop, initial_stop)  # 不能低于初始止损
        details["trailing_stop"] = float(trailing_stop)
        details["max_high_since_entry"] = max_high_since_entry

        # 当前有效止损 = max(初始, 移动) = 移动止损（除非还在入场初期）
        current_stop = trailing_stop
        details["current_stop"] = float(current_stop)

        # 3. 均线止损（Minervini 5/13/30）
        ma5 = close.rolling(5, min_periods=1).mean().iloc[-1]
        ma13 = close.rolling(13, min_periods=1).mean().iloc[-1]
        ma30 = close.rolling(30, min_periods=1).mean().iloc[-1]
        below_ma5 = last_close < ma5
        below_ma13 = last_close < ma13
        below_ma30 = last_close < ma30
        details.update({
            "ma5": float(ma5), "ma13": float(ma13), "ma30": float(ma30),
            "below_ma5": bool(below_ma5),
            "below_ma13": bool(below_ma13),
            "below_ma30": bool(below_ma30),
        })

        # ========== 止盈目标 ==========
        risk_per_share = entry_price - initial_stop
        if risk_per_share <= 0:
            risk_per_share = self.atr_multiplier * last_atr  # 兜底
        target = entry_price + 3.0 * risk_per_share  # 3:1 风险比
        details["target"] = float(target)
        details["risk_per_share"] = float(risk_per_share)

        # ========== 持仓状态评估 ==========
        current_return = (last_close - entry_price) / entry_price
        details["entry_price"] = float(entry_price)
        details["last_close"] = float(last_close)
        details["current_return_pct"] = float(current_return * 100)
        details["holding_days"] = int(holding_days)
        details["atr"] = float(last_atr)
        details["distance_to_stop_pct"] = float(
            (last_close - current_stop) / last_close * 100
        )

        # ========== 触发卖出条件 ==========
        # exit_reasons = 必须清仓的硬触发；reduce_reasons = 应减仓的软触发
        # 设计依据：HANDOFF 回测显示「紧止损/提前退出截断赢家」，故 MA 信号只触发减仓/警告，不主动 EXIT
        exit_reasons = []
        reduce_reasons = []

        # 硬触发 1：跌破止损（ATR 止损位）
        if last_close <= current_stop:
            exit_reasons.append(f"跌破止损（stop={current_stop:.2f}）")

        # 硬触发 2：时间止损（持仓超期 + 收益未达标）—— 回测最优策略
        target_reached = current_return >= self.target_reward_pct
        if holding_days >= self.max_holding_days and not target_reached:
            exit_reasons.append(
                f"时间止损（{holding_days}日未达目标 {self.target_reward_pct*100:.0f}%，"
                f"当前 {current_return*100:+.1f}%）"
            )

        # 软触发：跌破 MA30（强制减仓，不主动 EXIT，避免截断赢家）
        if self.ma30_force and below_ma30:
            reduce_reasons.append(f"跌破 MA30={ma30:.2f}（建议减仓 1/3）")

        # 警告级：跌破 MA13（紧止损建议）
        action = "HOLD"
        if exit_reasons:
            action = "EXIT"
        elif reduce_reasons:
            action = "REDUCE"
        elif below_ma13:
            action = "TIGHTEN_STOP"
            reasons.append(f"⚡ 跌破 MA13={ma13:.2f}，建议紧止损到 {current_stop:.2f}")
        elif below_ma5:
            action = "WATCH"
            reasons.append(f"⚠️ 跌破 MA5={ma5:.2f}，警告")

        details["action"] = action
        details["exit_reasons"] = exit_reasons
        details["reduce_reasons"] = reduce_reasons

        # ========== value 计算 ==========
        # 安全度：1=非常安全，0=必须离场
        # 综合考虑：未触发 exit、收益进度、距止损空间
        if exit_reasons:
            value = 0.0
            reasons.extend([f"🔴 触发清仓：{r}" for r in exit_reasons])
        elif reduce_reasons:
            value = 0.25  # 低安全度但不强制清仓
            reasons.extend([f"🟡 建议减仓：{r}" for r in reduce_reasons])
        else:
            # 收益进度（0~1）
            return_progress = min(1.0, max(0.0, current_return / self.target_reward_pct))
            # 距止损空间（5% = 0.5，10% = 1.0）
            stop_distance_pct = (last_close - current_stop) / last_close
            stop_safety = min(1.0, max(0.0, stop_distance_pct / 0.10))
            # 时间进度（60 日 = 1.0，越接近期限风险越高）
            time_decay = 1.0 - min(1.0, holding_days / self.max_holding_days) * 0.3
            value = return_progress * 0.4 + stop_safety * 0.4 + time_decay * 0.2

        reasons.append(f"action = {action}")
        reasons.append(
            f"入场 {entry_price:.2f} · 当前 {last_close:.2f}（{current_return*100:+.1f}%）· "
            f"止损 {current_stop:.2f}（-{(last_close-current_stop)/last_close*100:.1f}%）· "
            f"目标 {target:.2f}"
        )

        passed = value < self.threshold
        return SignalResult(
            ticker=ticker, signal_name=self.name, value=float(value),
            passed=bool(passed), details=details, reasons=reasons,
        ).clamp()

    def _resolve_entry(self, df: pd.DataFrame) -> tuple[float | None, pd.Timestamp | None]:
        """解析入场点：优先用外部传入，否则用最近 60 日最低点"""
        if self.entry_price is not None and self.entry_date is not None:
            try:
                idx = pd.Timestamp(self.entry_date)
                if idx in df.index:
                    return float(self.entry_price), idx
                # 日期不在 index 中，找最近的
                nearest = df.index[df.index.get_indexer([idx], method="nearest")[0]]
                return float(self.entry_price), nearest
            except Exception:
                return None, None

        if self.entry_price is not None:
            # 只有价格没日期，用 60 日前
            lookback = min(60, len(df) // 2)
            return float(self.entry_price), df.index[-lookback]

        # 没传任何东西：用最近 60 日最低点做占位
        lookback = min(60, len(df))
        recent = df.tail(lookback)
        low_idx = recent["low"].idxmin()
        return float(recent.loc[low_idx, "low"]), low_idx


__all__ = ["ExitStrategySignal"]
