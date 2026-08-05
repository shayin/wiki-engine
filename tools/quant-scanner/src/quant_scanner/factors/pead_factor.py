"""PEAD 因子 — Post-Earnings Announcement Drift

来源：
- Ball & Brown (1968) *An Evaluation of Accounting Income Numbers*（PEAD 首次记录）
- Bernard & Thomas (1989) *Post-Earnings-Announcement Drift*（系统化）
- Daniel, Hirshleifer, Sun (2020) JFE *Short-Horizon Behavioral Mispricing and
  Long-Horizon Correction*（方法论框架，将 PEAD/应计/短期动量整合）

**核心现象（PEAD）**：
公司发布盈利公告后，如果超预期（SUE > 0），股价不仅当天跳涨，**未来 1-3 个月还会持续漂移向上**；
反之低于预期（SUE < 0）则持续下跌。这是市场最持久的异象之一，**40+ 年未被套利消除**。

**为什么存在**：
- 投资者锚定于分析师一致预期，对盈利信息更新不足（under-reaction）
- 机构受风险约束不套利
- 散户注意力有限

**SUE（Standardized Unexpected Earnings）公式**：

.. math::
    \text{SUE}_t = \frac{\text{EPS}_t - E[\text{EPS}_t]}{\sigma(\text{EPS}_t - E[\text{EPS}_t])}

其中 :math:`E[\text{EPS}_t]` 通常用季节性随机游走（seasonal random walk）：

.. math::
    E[\text{EPS}_t] = \text{EPS}_{t-4}

即去年同季度 EPS（季节性随机游走）。

**漂移预测力**：
- SUE 最高 10% 的股票 → 未来 60 日超额收益 +2-4%
- SUE 最低 10% → 未来 60 日超额收益 -2-4%
- IC 通常 0.03-0.06（正向因子）

**实践用法**：
- 最近一次财报 SUE > 1.0（明显超预期）→ PEAD 做多窗口
- SUE < -1.0（明显不及预期）→ PEAD 做空 / 远离
- 配合财报日期：公告后 5-30 天是漂移最强阶段

**典型用法**：
```python
from quant_scanner.data.loader import DataLoader
from quant_scanner.factors.pead_factor import pead_signal

eps_data = DataLoader().load_eps_history("NVDA")
# 返回日频时间序列：每个交易日记录最近一次财报的 SUE，时间衰减
pead = pead_signal(eps_data, price_index=df.index, decay_window=60)
```

**数据要求**：
- 至少 5 个季度 EPS（loader 当前返回 5 个季度）→ 可产生 1 个 SUE（退化为 ±1）
- 至少 8 个季度（2 年）→ 可产生 4 个 SUE，MAD 归一化才有意义
- 建议结合 SEC EDGAR（loader 扩展后）拉 8+ 季度数据
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def compute_sue(
    quarterly_eps: dict[str, float] | pd.Series,
    n_surprise_for_std: int = 4,
) -> pd.Series:
    """从季度 EPS 序列计算 SUE（Standardized Unexpected Earnings）

    Args:
        quarterly_eps: {date_str: eps} 字典 或 pd.Series（按时间升序）
        n_surprise_for_std: 计算 σ 用最近 N 个 surprise（默认 4）

    Returns:
        pd.Series，index 是财报日期字符串，value 是 SUE

    Notes:
        归一化用 MAD（平均绝对偏差）替代 std，更稳健：
        - 抗异常值（极端 EPS 跳跃不会让 std 爆炸）
        - 支持单点（N=1 时 MAD = surprise 自身，SUE = ±1，保守但有意义）
        - 论文标准做法之一（Bessembinder 2003 等）
    """
    # 转为 pd.Series，按时间升序
    if isinstance(quarterly_eps, dict):
        s = pd.Series(quarterly_eps)
        s.index = pd.to_datetime(s.index)
    else:
        s = quarterly_eps.copy()
        if not isinstance(s.index, pd.DatetimeIndex):
            s.index = pd.to_datetime(s.index)
    s = s.sort_index()

    # 季节性随机游走：预期 = 去年同季（t-4）
    surprise = s - s.shift(4)
    surprise = surprise.dropna()

    if len(surprise) < 1:
        return pd.Series(dtype=float)

    # 用 MAD（平均绝对偏差）归一化，比 std 更稳健
    # rolling window 默认 4（n_surprise_for_std），min_periods=1
    mad = surprise.abs().rolling(
        window=n_surprise_for_std, min_periods=1,
    ).mean()
    # 防 0（surprise 全 0 时 MAD=0）
    mad = mad.replace(0, np.nan)
    sue = surprise / mad
    return sue.dropna()


def pead_signal(
    quarterly_eps: dict[str, float] | pd.Series,
    price_index: pd.DatetimeIndex | None = None,
    decay_window: int = 60,
    end_date: pd.Timestamp | None = None,
    lookback_days: int = 365,
) -> pd.Series:
    """构造日频 PEAD 信号时间序列

    Args:
        quarterly_eps: 季度 EPS（loader.load_eps_history 返回的 quarterly_eps）
        price_index: 价格序列的 index（用于将 SUE 映射到交易日）
                     None 则用最近 lookback_days 自然日
        decay_window: 财报后 N 天内 SUE 信号保持有效（之后线性衰减到 0）
        end_date: 截止日（None 用今天）
        lookback_days: 若 price_index 为 None，向前回溯多少天

    Returns:
        pd.Series（index 与 price_index 对齐），值是最近一次财报的 SUE（带衰减）
    """
    sue = compute_sue(quarterly_eps)
    if len(sue) == 0:
        if price_index is not None:
            return pd.Series(0.0, index=price_index)
        return pd.Series(dtype=float)

    # 确定输出时间轴
    if price_index is not None:
        out_index = price_index
    else:
        end = end_date or pd.Timestamp.now()
        start = end - pd.Timedelta(days=lookback_days)
        out_index = pd.date_range(start=start, end=end, freq="B")

    # 对每个交易日，找最近一次已发布的财报 SUE，按时间衰减
    sue_dates = sue.index
    sue_vals = sue.values
    out = np.zeros(len(out_index))

    for i, dt in enumerate(out_index):
        # 找 ≤ dt 的最近一次财报
        mask = sue_dates <= dt
        if not mask.any():
            continue
        last_idx = np.where(mask)[0][-1]
        fb_date = sue_dates[last_idx]
        days_since = (dt - fb_date).days
        if days_since > decay_window:
            continue  # 已衰减到 0
        # 线性衰减：发布当天 = 1.0，decay_window 天后 = 0
        decay = 1.0 - (days_since / decay_window)
        out[i] = sue_vals[last_idx] * decay

    return pd.Series(out, index=out_index)


def pead_categorize(sue_value: float) -> str:
    """PEAD 信号分类（用于决策提示）

    Args:
        sue_value: SUE 值

    Returns:
        'strong_beat' / 'beat' / 'in_line' / 'miss' / 'strong_miss'
    """
    if sue_value >= 2.0:
        return "strong_beat"  # 大幅超预期 → PEAD 做多
    if sue_value >= 1.0:
        return "beat"           # 超预期
    if sue_value >= -1.0:
        return "in_line"        # 符合预期
    if sue_value >= -2.0:
        return "miss"           # 不及预期
    return "strong_miss"        # 大幅不及预期 → PEAD 做空


def pead_summary(
    quarterly_eps: dict[str, float] | pd.Series,
    latest_n: int = 4,
) -> dict:
    """PEAD 摘要：最近 N 季度的 SUE + 最新分类

    Args:
        quarterly_eps: 季度 EPS 序列
        latest_n: 输出最近几季度

    Returns:
        {
            "sue_history": [(date, sue), ...],
            "latest_sue": float,
            "latest_category": str,
            "latest_fb_date": date,
            "n_quarters": int,
        }
    """
    sue = compute_sue(quarterly_eps)
    if len(sue) == 0:
        return {
            "sue_history": [],
            "latest_sue": None,
            "latest_category": "unknown",
            "latest_fb_date": None,
            "n_quarters": 0,
        }
    recent = list(sue.items())[-latest_n:]
    latest_sue = float(sue.iloc[-1])
    latest_date = sue.index[-1]
    return {
        "sue_history": [(d, float(v)) for d, v in recent],
        "latest_sue": latest_sue,
        "latest_category": pead_categorize(latest_sue),
        "latest_fb_date": latest_date,
        "n_quarters": len(sue),
    }
