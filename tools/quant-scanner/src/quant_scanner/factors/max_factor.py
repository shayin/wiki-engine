"""MAX 因子 — Bali-Cakici-Whitelaw (2010) JFE

来源：
- Bali, Cakici, Whitelaw (2010) "Maxing Out: Stocks as Lotteries and the Cross-Section of
  Expected Returns" *Journal of Financial Economics* 99(2): 427-446

**核心发现（MAX effect）**：
近期最大单日收益（MAX）高的股票，未来收益显著较低。

**行为金融解释**：
- 投资者偏好"彩票型"股票（左偏/右偏分布不对称）
- MAX 高的股票被高估（散户追捧极端收益）
- 后续均值回归 → 未来收益低
- 与-beta/异质波动率效应相关，但 MAX 是更强预测变量

**MAX 公式**：

.. math::
    \text{MAX}_t^{(n)} = \max(r_{t-1}, r_{t-2}, \ldots, r_{t-n})

其中 :math:`r_t` 为日收益，n 通常取 1（MAX1）或 5/20（平均 top-k 日收益）。

**论文变体**：
- **MAX(1)**：最近 1 个月最大单日收益
- **MAX(5)**：最近 1 个月 top-5 平均日收益
- **MAX(20)**：最近 1 个月 top-20 平均日收益（噪音大）

**预测力**：
- Bali et al. (2010)：MAX 高 vs MAX 低组合月度收益差 -1.0%（高 MAX 显著跑输）
- IC 通常 -0.03 ~ -0.05（负向因子）
- 在小盘股、散户占比高的股票上更强

**实践用法**：
- 高 MAX（近期有过单日暴涨）→ 警惕反转，不建议追高
- 低 MAX（走势平稳）→ 长期配置候选

**典型用法**：
```python
from quant_scanner.factors.max_factor import max_factor, max_factor_5

# MAX(1)：最近 20 日最大单日收益
max1 = max_factor(df["close"], window=20, k=1)

# MAX(5)：最近 20 日 top-5 平均
max5 = max_factor_5(df["close"], window=20)
```
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def max_factor(
    close: pd.Series,
    window: int = 20,
    k: int = 1,
) -> pd.Series:
    """MAX 因子 — 过去 window 天 top-k 最大单日收益的均值

    Args:
        close: 收盘价序列
        window: 回看窗口（默认 20 = 1 个月）
        k: 取 top-k 平均（默认 1 = 单日最大）

    Returns:
        MAX 因子序列（与 close 等长）
    """
    if k < 1:
        raise ValueError("k 必须 ≥ 1")
    if k > window:
        raise ValueError(f"k ({k}) 不能大于 window ({window})")
    # pct_change 首值 NaN → 填 0（"第一天无收益"），避免 rolling 丢一个样本
    rets = close.pct_change().fillna(0)

    def _topk_mean(x: np.ndarray) -> float:
        if len(x) < k:
            return np.nan
        # nlargest 取 top-k
        return float(np.sort(x)[-k:].mean())

    return rets.rolling(window=window, min_periods=window).apply(_topk_mean, raw=True)


def max_factor_5(close: pd.Series, window: int = 20) -> pd.Series:
    """MAX(5)：最近 window 天 top-5 平均日收益（Bali 原论文标准）"""
    return max_factor(close, window=window, k=5)


def max_factor_1(close: pd.Series, window: int = 20) -> pd.Series:
    """MAX(1)：最近 window 天最大单日收益"""
    return max_factor(close, window=window, k=1)


def max_decile_rank(
    close: pd.Series,
    window: int = 20,
    k: int = 5,
) -> pd.Series:
    """MAX 在过去 window 天的分位（0-1）

    高分位（>0.9）= 彩票型，警惕反转
    低分位（<0.1）= 走势平稳

    Args:
        close: 收盘价
        window: 回看窗口
        k: MAX 取 top-k 平均

    Returns:
        0-1 的分位序列
    """
    max_s = max_factor(close, window=window, k=k)
    return max_s.rolling(window=window).rank(pct=True)
