"""SignalEvent 采集系统

辩论共识 R2-R10：建立独立的 SignalEvent 采集，回答"哪个形态历史上准"。

核心组件：
1. SignalEvent：事件 dataclass
2. ExtractedEvent：extractor 输出的中间结构
3. EventExtractor：从 SignalResult.details 提取事件的统一接口（返回 list）
4. signal-specific extractor 映射
5. SignalEventCollector：日频采集器 + 去重冷却 + checkpoint

架构决定：
- event_type 作为统一二级键（替代 pattern_type）
- extractor 返回 list 允许同日多事件（RSI + MACD 同日并存）
- 同 ticker × signal × event_type × direction × 20 个交易日冷却
- pattern_type 为兼容字段（HTML 徽章用）
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Callable, Literal

import pandas as pd

from quant_scanner.signals.base import SignalResult

log = logging.getLogger(__name__)

DIRECTION = Literal["long", "short", "unknown"]
EVENT_KIND = Literal["transition", "confirmation", "checkpoint"]
DEFAULT_COOLDOWN_DAYS = 20  # 交易日


# =====================================================================
# 数据类
# =====================================================================


@dataclass
class ExtractedEvent:
    """extractor 输出的中间结构。"""

    event_type: str
    direction: DIRECTION
    event_kind: EVENT_KIND = "confirmation"
    confirmed: bool = True
    pattern_type: str | None = None  # 兼容字段


@dataclass
class SignalEvent:
    """采集到的形态事件。"""

    event_id: str = ""
    ticker: str = ""
    signal_name: str = ""
    event_type: str = ""
    pattern_type: str | None = None  # 兼容字段
    direction: DIRECTION = "unknown"
    confirmed_date: pd.Timestamp | None = None  # t 日
    entry_date: pd.Timestamp | None = None  # t+1，前瞻标签起算
    event_kind: EVENT_KIND = "confirmation"
    signal_value: float = 0.0
    details_whitelist: dict = field(default_factory=dict)
    holding_context: dict = field(default_factory=dict)

    def __post_init__(self):
        if not self.event_id:
            self.event_id = str(uuid.uuid4())


# =====================================================================
# EventExtractor 接口 + 内置映射
# =====================================================================


def _direction_from_value(value: float, threshold: float = 0.05) -> DIRECTION:
    """从 signal_value 符号推导 direction。"""
    if value > threshold:
        return "long"
    if value < -threshold:
        return "short"
    return "unknown"


def _extract_major_reversal(sr: SignalResult) -> list[ExtractedEvent]:
    """major_reversal：从 details.pattern_type 提取。"""
    d = sr.details or {}
    ptype = d.get("pattern_type")
    if not ptype:
        return []
    direction = _direction_from_value(sr.value)
    confirmed = bool(d.get("confirmed", True))
    return [ExtractedEvent(
        event_type=ptype,
        direction=direction,
        pattern_type=ptype,
        confirmed=confirmed,
        event_kind="confirmation",
    )]


def _extract_continuation(sr: SignalResult) -> list[ExtractedEvent]:
    """continuation：从 details.pattern_type 提取。"""
    d = sr.details or {}
    ptype = d.get("pattern_type")
    if not ptype:
        return []
    direction = _direction_from_value(sr.value)
    confirmed = bool(d.get("confirmed", True))
    return [ExtractedEvent(
        event_type=ptype,
        direction=direction,
        pattern_type=ptype,
        confirmed=confirmed,
        event_kind="confirmation",
    )]


def _extract_oscillator_timing(sr: SignalResult) -> list[ExtractedEvent]:
    """oscillator_timing：多事件提取。

    同日可同时命中 RSI/MACD/hidden/failure/extreme，各自生成独立 event。
    """
    d = sr.details or {}
    events: list[ExtractedEvent] = []
    value = sr.value
    direction = _direction_from_value(value)

    # RSI 经典背离
    rsi_div = d.get("divergence")  # "BULLISH_DIVERGENCE" / "BEARISH_DIVERGENCE"
    if rsi_div:
        if "BULLISH" in rsi_div:
            d_event: DIRECTION = "long"
        elif "BEARISH" in rsi_div:
            d_event = "short"
        else:
            d_event = direction
        events.append(ExtractedEvent(
            event_type="RSI_CLASSIC_DIVERGENCE",
            direction=d_event,
            event_kind="confirmation",
        ))

    # MACD 经典背离
    macd_div = d.get("macd_divergence")  # "BULLISH" / "BEARISH"
    if macd_div:
        if "BULLISH" in macd_div:
            d_event = "long"
        elif "BEARISH" in macd_div:
            d_event = "short"
        else:
            d_event = direction
        events.append(ExtractedEvent(
            event_type="MACD_CLASSIC_DIVERGENCE",
            direction=d_event,
            event_kind="confirmation",
        ))

    # 隐藏背离
    hidden = d.get("hidden_divergence")
    if hidden:
        if "BULLISH" in str(hidden).upper():
            d_event = "long"
        elif "BEARISH" in str(hidden).upper():
            d_event = "short"
        else:
            d_event = direction
        events.append(ExtractedEvent(
            event_type="HIDDEN_DIVERGENCE",
            direction=d_event,
            event_kind="confirmation",
        ))

    # 失败摆动
    failure = d.get("failure_swing")
    if failure:
        if "BULLISH" in str(failure).upper():
            d_event = "long"
        elif "BEARISH" in str(failure).upper():
            d_event = "short"
        else:
            d_event = direction
        events.append(ExtractedEvent(
            event_type="FAILURE_SWING",
            direction=d_event,
            event_kind="confirmation",
        ))

    # 超买超卖（value 绝对值大 + 无其他形态）
    last_rsi = d.get("rsi")
    if last_rsi is not None and not events:
        try:
            rsi_v = float(last_rsi)
        except (TypeError, ValueError):
            rsi_v = None
        if rsi_v is not None:
            if rsi_v >= 70:
                events.append(ExtractedEvent(
                    event_type="EXTREME",
                    direction="short",
                    event_kind="confirmation",
                ))
            elif rsi_v <= 30:
                events.append(ExtractedEvent(
                    event_type="EXTREME",
                    direction="long",
                    event_kind="confirmation",
                ))

    return events


def _extract_default(sr: SignalResult) -> list[ExtractedEvent]:
    """默认 extractor：非形态类 signal 按 signal_name.upper() 成事件。

    只在 passed=True 时触发，event_kind=transition。
    """
    if not sr.passed:
        return []
    direction = _direction_from_value(sr.value)
    return [ExtractedEvent(
        event_type=sr.signal_name.upper(),
        direction=direction,
        event_kind="transition",
    )]


# signal_name → extractor 映射
EXTRACTOR_MAP: dict[str, Callable[[SignalResult], list[ExtractedEvent]]] = {
    "major_reversal": _extract_major_reversal,
    "continuation": _extract_continuation,
    "oscillator_timing": _extract_oscillator_timing,
}


def get_extractor(signal_name: str) -> Callable[[SignalResult], list[ExtractedEvent]]:
    """按 signal_name 取 extractor；未注册的走默认。"""
    return EXTRACTOR_MAP.get(signal_name, _extract_default)


# =====================================================================
# Collector
# =====================================================================


class SignalEventCollector:
    """日频事件采集器。

    职责：
    - 接收 on_daily_eval(ticker, date, signal_results) 回调
    - 对每个 signal 调用对应 extractor
    - 形态/离散事件：按冷却去重
    - 持续型事件（transition）：只在状态切换（包括 false→true / state 变化）时生成
    - 输出最终事件列表

    review High 3：持续型 signal 必须做 transition 检测，不能每隔 cooldown 日重复采样。
    """

    # 视为"持续型"的 signal：默认 extractor 处理的（即未注册专属 extractor 的）
    # 这类 signal 需要做 false→true / 状态值变化的 transition 检测
    PERSISTENT_SIGNALS: set[str] = {
        "trend_template", "vcp", "pivot_point", "base_counting",
        "cup_handle", "market_direction", "trend_regime",
        "rs_rating", "can_slim", "new_high_supply",
        "dow_phases", "support_resistance",
    }

    def __init__(
        self,
        cooldown_days: int = DEFAULT_COOLDOWN_DAYS,
        whitelist_keys: tuple[str, ...] = (
            "pattern_type", "neckline", "target", "phase", "state",
            "confirmed", "rsi", "macd_line", "divergence",
        ),
    ):
        self.cooldown_days = cooldown_days
        self.whitelist_keys = whitelist_keys
        self._events: list[SignalEvent] = []
        # 去重缓存：(ticker, signal_name, event_type, direction) → 最近触发交易日 index（int）
        self._last_fired: dict[tuple[str, str, str, str], int] = {}
        # 持续型 signal 的上次状态：(ticker, signal_name) → (passed, value_bucket, state_key)
        # value_bucket：把 value 量化到 0.1 粒度的桶，避免小数漂移触发 transition
        # state_key：details 中的 phase / state / regime 等枚举状态
        self._last_persistent_state: dict[tuple[str, str], tuple[bool, int, str]] = {}

    def on_daily_eval(
        self,
        ticker: str,
        date: pd.Timestamp,
        signal_results: list[SignalResult],
        trading_dates: pd.DatetimeIndex | None = None,
        date_to_ordinal: Callable[[pd.Timestamp], int] | None = None,
    ) -> list[SignalEvent]:
        """处理单个 ticker 单日的 signal 评估结果，返回当日新生成的事件。

        Args:
            ticker: 股票代码
            date: PIT 当日（t 日）
            signal_results: 当日各 signal 的 SignalResult
            trading_dates: 交易日序列（用于计算 entry_date=t+1）
            date_to_ordinal: 把日期转成连续序号（用于冷却比较），默认用 date 整数天

        Returns:
            当日新生成的 SignalEvent 列表
        """
        if date_to_ordinal is None:
            date_to_ordinal = lambda d: int(d.toordinal())

        # 找 t+1 交易日
        next_date: pd.Timestamp | None = None
        if trading_dates is not None and len(trading_dates) > 0:
            future = trading_dates[trading_dates > date]
            if len(future) > 0:
                next_date = future[0]

        new_events: list[SignalEvent] = []
        cur_ord = date_to_ordinal(date)

        for sr in signal_results:
            extractor = get_extractor(sr.signal_name)
            try:
                extracted = extractor(sr)
            except Exception as e:
                log.warning(
                    "extractor for %s 抛异常: %s; signal_result.details=%s",
                    sr.signal_name, e, sr.details,
                )
                continue

            # review High 3：持续型 signal 做 transition 检测
            if sr.signal_name in self.PERSISTENT_SIGNALS:
                # 量化 value 到 0.1 桶 + 提取 state key
                value_bucket = int(round(sr.value * 10))
                details = sr.details or {}
                state_key = str(details.get("phase") or details.get("state") or details.get("regime") or "")
                state_tuple = (sr.passed, value_bucket, state_key)
                last_state = self._last_persistent_state.get((ticker, sr.signal_name))
                if last_state == state_tuple:
                    # 状态未变，不生成新事件（即使过了冷却期）
                    continue
                self._last_persistent_state[(ticker, sr.signal_name)] = state_tuple
                # 状态变化：如果是 false→true 或 state 变化才生成 transition
                if last_state is None:
                    # 第一次看到，passed=True 才生成
                    if not sr.passed:
                        continue
                else:
                    # 后续：必须 passed 从 false→true，或 state_key 变化，或 value_bucket 跨阈值
                    if not sr.passed:
                        # true→false 不生成事件（退场不在统计范围）
                        continue
                    if last_state[0] and sr.passed and last_state[2] == state_key and last_state[1] == value_bucket:
                        continue
                # 通过 transition 检测，继续走正常生成流程（但跳过 cooldown）

            for ev in extracted:
                key = (ticker, sr.signal_name, ev.event_type, ev.direction)
                # 形态类（confirmation）受 cooldown 控制
                # 持续型 transition 事件已被状态变化检测去重，不受 cooldown 限制
                if ev.event_kind == "confirmation":
                    last_ord = self._last_fired.get(key)
                    if last_ord is not None and (cur_ord - last_ord) < self.cooldown_days:
                        continue
                # 通过冷却，记录并生成事件
                self._last_fired[key] = cur_ord
                details_wl = {
                    k: v for k, v in (sr.details or {}).items()
                    if k in self.whitelist_keys
                }
                event = SignalEvent(
                    ticker=ticker,
                    signal_name=sr.signal_name,
                    event_type=ev.event_type,
                    pattern_type=ev.pattern_type,
                    direction=ev.direction,
                    confirmed_date=date,
                    entry_date=next_date,
                    event_kind=ev.event_kind,
                    signal_value=sr.value,
                    details_whitelist=details_wl,
                )
                self._events.append(event)
                new_events.append(event)

        return new_events

    @property
    def events(self) -> list[SignalEvent]:
        """返回所有已采集事件。"""
        return list(self._events)

    def clear(self) -> None:
        """清空采集器（测试用）。"""
        self._events.clear()
        self._last_fired.clear()


def filter_long_only(events: list[SignalEvent]) -> list[SignalEvent]:
    """筛选 long 方向事件（第一期 long-only 合同）。"""
    return [e for e in events if e.direction == "long"]


def filter_diagnostic(events: list[SignalEvent]) -> list[SignalEvent]:
    """筛选 short/unknown 诊断桶事件。"""
    return [e for e in events if e.direction != "long"]
