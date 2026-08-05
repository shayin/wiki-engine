"""PIT (Point-in-time) 兼容适配层

辩论共识 R6/R7：BaseSignal.evaluate 抽象签名不含 pit_date，但已有 5 个 signal 在签名里支持。
adapter 通过 inspect.signature 检测，自动 dispatch：

- evaluate 签名含 pit_date → 调用时传
- 不含 → 调用原签名，log.warning 一次

测试必测项：
1. evaluate_pit 不读 pit_date 之后的数据（mock loader 验证）
2. 未实现 pit_date 的 signal 走回退路径不抛 TypeError
3. 同 signal 不同 pit_date 对同一段 df 产生不同 SignalResult（外部数据型）
"""
from __future__ import annotations

import inspect
import logging
from typing import Protocol, runtime_checkable

import pandas as pd

from quant_scanner.signals.base import BaseSignal, SignalResult

log = logging.getLogger(__name__)

# 已警告过的 signal 类，避免重复 log
_WARNED_NO_PIT: set[str] = set()


@runtime_checkable
class PITAwareProtocol(Protocol):
    """支持 PIT 评估的 signal 协议。

    实现方在 evaluate 签名中加入可选的 pit_date 参数即满足此协议。
    """

    def evaluate(
        self, ticker: str, df: pd.DataFrame, pit_date: pd.Timestamp | None = None
    ) -> SignalResult: ...


def _signature_accepts_pit(signal: BaseSignal) -> bool:
    """检查 signal.evaluate 签名是否接受 pit_date 参数。"""
    try:
        sig = inspect.signature(signal.evaluate)
        return "pit_date" in sig.parameters
    except (ValueError, TypeError):
        return False


def evaluate_with_pit(
    signal: BaseSignal,
    ticker: str,
    df: pd.DataFrame,
    pit_date: pd.Timestamp,
) -> SignalResult:
    """按 PIT 评估 signal，自动 dispatch 到支持/不支持 pit_date 的调用路径。

    Args:
        signal: 信号实例
        ticker: 股票代码
        df: OHLCV 日线数据
        pit_date: PIT 截断日，signal 只能用 pit_date 及之前的数据

    Returns:
        SignalResult

    Raises:
        TypeError: 仅当 signal.evaluate 既不接受 pit_date 又不接受标准签名时
    """
    if _signature_accepts_pit(signal):
        return signal.evaluate(ticker, df, pit_date=pit_date)

    # 回退路径：调用方自行保证 df 已经按 pit_date 截断
    cls_name = signal.__class__.__name__
    if cls_name not in _WARNED_NO_PIT:
        log.warning(
            "signal %s.evaluate 不支持 pit_date 参数，"
            "回退到标准 evaluate；请确保调用方已按 pit_date 截断 df",
            cls_name,
        )
        _WARNED_NO_PIT.add(cls_name)
    return signal.evaluate(ticker, df)


def reset_warn_cache() -> None:
    """重置警告缓存（测试用）。"""
    _WARNED_NO_PIT.clear()
