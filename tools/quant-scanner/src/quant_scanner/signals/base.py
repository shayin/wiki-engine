"""信号基类

借鉴 ai-hedge-fund/v2 设计：
- 所有信号输出归一化到 [-1, +1]
- 1.0 = 强烈看多，-1.0 = 强烈看空，0 = 中性
- 必须返回 SignalResult，包含原始数据供下游分析
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd


@dataclass
class SignalResult:
    """信号输出"""

    ticker: str
    signal_name: str
    value: float  # [-1, +1]
    passed: bool  # 是否通过筛选门槛
    details: dict = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)

    def clamp(self) -> "SignalResult":
        """将 value 限制在 [-1, +1]"""
        self.value = max(-1.0, min(1.0, self.value))
        return self


class BaseSignal(ABC):
    """所有信号的抽象基类"""

    name: str = "base"
    # 通过门槛（0-1），>该值视为 passed=True
    threshold: float = 0.5

    def __init__(self, threshold: Optional[float] = None, **kwargs):
        if threshold is not None:
            self.threshold = threshold
        # 允许子类用 kwargs 覆盖类属性（例如 VCPSignal(require_trend_template=False)）
        for k, v in kwargs.items():
            setattr(self, k, v)

    @abstractmethod
    def evaluate(self, ticker: str, df: pd.DataFrame) -> SignalResult:
        """对一只股票的日线数据评估信号

        Args:
            ticker: 股票代码
            df: 日线 OHLCV 数据，要求列：open/high/low/close/volume，DatetimeIndex

        Returns:
            SignalResult
        """
        ...

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} threshold={self.threshold}>"
