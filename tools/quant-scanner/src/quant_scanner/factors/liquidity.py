"""流动性因子（Liquidity Factors）

来源：
- **Amihud (2002) JFE** "Illiquidity and Stock Returns: Cross-Section and Time-Series Effects"
  *Journal of Financial Economics* 69(3): 1375-1397
- **Corwin-Schultz (2012) JFE** "A Simple Way to Estimate Bid-Ask Spreads from Daily Data"
  *Journal of Financial Economics* 111(1): 42-59

**核心发现**：

1. **Amihud ILLIQ**：|日收益| / 成交额 的均值。最早最经典的流动性指标
   - ILLIQ 高 = 单位成交额引起的价格变动大 = 流动性差
   - 学术证明：ILLIQ 高的股票，未来收益显著更高（流动性溢价）
   - 跨期比较：可做标准化（对数化、z-score）

2. **Corwin-Schultz Spread**：用日内 high-low 反推买卖价差
   - 原理：日内高低价差 = 波动 + 价差（价差部分每日近似恒定）
   - 连续两天 high-low 比较可分离波动和价差
   - 无需 tick 数据，日线即可

**典型用法**：

```python
from quant_scanner.factors.liquidity import (
    amihud_illiq, corwin_schultz_spread, liquidity_composite
)
import pandas as pd

# Amihud ILLIQ（21 日滚动）
illiq = amihud_illiq(close=df["close"], volume=df["volume"], window=21)

# Corwin-Schultz 有效价差（bps）
spread = corwin_schultz_spread(high=df["high"], low=df["low"], window=21)

# 综合：低流动性 = 高潜在溢价
composite = liquidity_composite(illiq, spread)
```

**学术标准**（JFE 论文）：
- Amihud ILLIQ 月度均值排序，跨市场可解释 5-10% 的预期收益差异
- Corwin-Schultz Spread 与实际价差相关性 ~0.5-0.7
"""
from __future__ import annotations

import numpy as np
import pandas as pd


# ============================================================
# Amihud ILLIQ
# ============================================================

def amihud_illiq(
    close: pd.Series,
    volume: pd.Series,
    window: int = 21,
    min_periods: int | None = None,
) -> pd.Series:
    """Amihud ILLIQ 流动性指标

    .. math::
        \\text{ILLIQ}_t = \\frac{1}{D}\\sum_{d=1}^{D} \\frac{|r_{t-d}|}{\\text{DVOL}_{t-d}}

    其中 :math:`r_t` 为日收益，DVOL 为成交额（dollar volume）。

    Args:
        close: 收盘价
        volume: 成交量（股数；若已是成交额也可，结果更准）
        window: 滚动窗口（默认 21 = 1 个月交易日）
        min_periods: 最小非 NaN 样本数

    Returns:
        pd.Series，单位（|收益|/成交额），**值越高 = 流动性越差**
    """
    if min_periods is None:
        min_periods = max(window // 2, 5)

    ret = close.pct_change().abs()
    dv = close * volume  # 近似成交额（若无 VWAP）
    dv = dv.replace(0, np.nan)
    daily_illiq = ret / dv
    return daily_illiq.rolling(window, min_periods=min_periods).mean()


def amihud_illiq_log(
    close: pd.Series,
    volume: pd.Series,
    window: int = 21,
) -> pd.Series:
    """对数 ILLIQ（更接近正态分布，适合做 feature）

    用 log10 而非 log1p，因为 ILLIQ 通常 ~1e-10 量级，log1p 几乎不变。
    log10 标准化为"每百万美元成交额引起多少收益变动"。
    """
    illiq = amihud_illiq(close, volume, window=window)
    # ×10^6 标准化：单位 = 每 $1M 成交额引起的 |收益|
    illiq_scaled = illiq * 1e6
    return np.log10(illiq_scaled.replace([np.inf, -np.inf, 0], np.nan))


def amihud_implied_turnover(
    close: pd.Series,
    volume: pd.Series,
    window: int = 21,
) -> pd.Series:
    """Amihud 反转版本：1/ILLIQ（高 = 流动性好，可直接作"流动性得分"）"""
    illiq = amihud_illiq(close, volume, window=window)
    return 1.0 / illiq.replace(0, np.nan)


# ============================================================
# Corwin-Schultz 有效价差
# ============================================================

def corwin_schultz_spread(
    high: pd.Series,
    low: pd.Series,
    window: int = 21,
    min_periods: int | None = None,
) -> pd.Series:
    """Corwin-Schultz (2012) JFE 有效价差估计

    原理：日内 high-low 同时包含波动（σ）和价差（S）。连续两天的
    high-low 的最大值-最小值的比率可分离两者。

    .. math::
        S = \\frac{2(e^{\\alpha} - 1)}{1 + e^{\\alpha}}

    其中 :math:`\\alpha = \\frac{\\sqrt{2\\beta} - \\sqrt{\\beta}}{3 - 2\\sqrt{2}} - \\sqrt{\\frac{\\gamma}{3 - 2\\sqrt{2}}}`，
    :math:`\\beta = \\sum (\\ln(H_t/L_t))^2`，:math:`\\gamma = (\\ln(H_{t,t+1}/L_{t,t+1}))^2`。

    Args:
        high: 日内最高价
        low: 日内最低价
        window: 滚动窗口

    Returns:
        pd.Series，单位比例（0.001 = 10bps），**值越高 = 流动性越差**
    """
    if min_periods is None:
        min_periods = max(window // 2, 5)

    # 日内 log 高低价
    log_hl = np.log(high / low).replace([np.inf, -np.inf], np.nan)
    log_hl_sq = log_hl ** 2

    # 两日窗口：连续两天的 max high 和 min low
    high_2d = high.rolling(2).max()
    low_2d = low.rolling(2).min()
    log_hl_2d = np.log(high_2d / low_2d).replace([np.inf, -np.inf], np.nan)

    # Corwin-Schultz 公式（原文 Eq.12-15）：
    # β = Σ ln²(H_t/L_t) over 2 consecutive days
    # γ = ln²(H_{t,t+1} / L_{t,t+1})
    # α = [√(2β) - √β] / (3 - 2√2) - √[γ / (3 - 2√2)]
    # S = 2(e^α - 1) / (1 + e^α)
    beta = log_hl_sq.rolling(2).sum()
    gamma = log_hl_2d ** 2

    sqrt2 = np.sqrt(2.0)
    denom = 3.0 - 2.0 * sqrt2

    # 每项需非负（γ > 2β 时 α 变负 → 设 0，论文标准处理）
    term1 = np.sqrt(2.0 * beta)
    term2 = np.sqrt(beta)
    term3 = np.sqrt(np.maximum(gamma / denom, 0))
    numerator = np.maximum(term1 - term2, 0)  # 保证 ≥ 0
    alpha = numerator / denom - term3
    alpha = alpha.clip(lower=0)  # spread 非负

    # S = 2(e^α - 1) / (1 + e^α)
    spread = 2.0 * (np.exp(alpha) - 1.0) / (1.0 + np.exp(alpha))

    # 滚动窗口平均（论文用日度，这里滚动平滑）
    return spread.rolling(window, min_periods=min_periods).mean()


def corwin_schultz_spread_bps(
    high: pd.Series,
    low: pd.Series,
    window: int = 21,
) -> pd.Series:
    """Corwin-Schultz Spread 以 bps 表示（× 10000）"""
    return corwin_schultz_spread(high, low, window=window) * 10000.0


# ============================================================
# Pastor-Stambaugh 流动性因子（简化版）
# ============================================================

def pastor_stambaugh_innov(
    close: pd.Series,
    volume: pd.Series,
    market_ret: pd.Series,
    window: int = 60,
) -> pd.Series:
    """Pastor-Stambaugh (2003) JFE 流动性创新

    简化实现：用 market_ret 的 sign × volume 作流动性信号代理

    原始论文：
        r_{t+1}^i - r_{t+1}^m = θ_0 + θ_1 sign(r_t^m) × vol_t^i + ε

    θ_1 为负且显著 = 流动性低时高成交量的反转更强

    本实现用滚动回归，返回 θ_1 的滚动估计。

    Args:
        close: 个股收盘价
        volume: 个股成交量
        market_ret: 市场日收益（SPY/QQQ）
        window: 滚动回归窗口

    Returns:
        pd.Series，θ_1 估计值（负值 = 流动性有效，正值 = 无流动性效应）
    """
    stock_ret = close.pct_change()
    fwd_ret = stock_ret.shift(-1) - market_ret.shift(-1)
    sign_mkt = np.sign(market_ret)
    vol_proxy = np.log1p(volume.replace(0, np.nan))

    signal = sign_mkt * vol_proxy

    # 滚动简单回归（OLS θ_1）
    def _rolling_beta(x: np.ndarray, y: np.ndarray) -> float:
        mask = ~(np.isnan(x) | np.isnan(y))
        if mask.sum() < 10:
            return np.nan
        xm, ym = x[mask], y[mask]
        xm = xm - xm.mean()
        ym = ym - ym.mean()
        denom = (xm ** 2).sum()
        if denom < 1e-12:
            return np.nan
        return float((xm * ym).sum() / denom)

    from pandas import Series
    out = []
    s_arr = signal.values
    f_arr = fwd_ret.values
    for i in range(len(signal)):
        if i < window:
            out.append(np.nan)
            continue
        out.append(_rolling_beta(s_arr[i - window:i], f_arr[i - window:i]))
    return Series(out, index=signal.index, name="ps_innov")


# ============================================================
# 流动性综合得分
# ============================================================

def liquidity_composite(
    illiq: pd.Series,
    spread: pd.Series | None = None,
    higher_is_illiquid: bool = True,
) -> pd.Series:
    """综合流动性得分（z-score 平均）

    Args:
        illiq: Amihud ILLIQ
        spread: 可选 Corwin-Schultz Spread
        higher_is_illiquid: True=输入值高代表流动性差（默认），输出高=流动性差

    Returns:
        z-score 综合得分，**正值 = 流动性差（潜在溢价）**
    """
    def _z(s: pd.Series) -> pd.Series:
        s = s.replace([np.inf, -np.inf], np.nan)
        mu = s.rolling(252, min_periods=21).mean()
        sd = s.rolling(252, min_periods=21).std().replace(0, np.nan)
        return (s - mu) / sd

    components = [_z(illiq)]
    if spread is not None:
        components.append(_z(spread))

    stack = pd.concat(components, axis=1)
    composite = stack.mean(axis=1)
    if not higher_is_illiquid:
        composite = -composite
    return composite.clip(-5, 5)


# ============================================================
# Quick self-test
# ============================================================

if __name__ == "__main__":
    rng = np.random.default_rng(42)
    n = 500
    close = pd.Series(100 * np.exp(np.cumsum(rng.normal(0.0005, 0.015, n))), name="close")
    high = close * (1 + rng.uniform(0.001, 0.015, n))
    low = close * (1 - rng.uniform(0.001, 0.015, n))
    vol = pd.Series(rng.integers(1e6, 5e6, n).astype(float), name="volume")

    illiq = amihud_illiq(close, vol)
    spread = corwin_schultz_spread(high, low)
    comp = liquidity_composite(illiq, spread)

    print(f"Amihud ILLIQ (last): {illiq.iloc[-1]:.2e}")
    print(f"  中位数: {illiq.median():.2e}")
    print(f"CS Spread bps (last): {spread.iloc[-1]*10000:.2f}")
    print(f"Composite z (last): {comp.iloc[-1]:.2f}")
