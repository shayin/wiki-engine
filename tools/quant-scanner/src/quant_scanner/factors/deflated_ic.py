"""Deflated IC — Harvey-Liu-Zhu (2016) 多重检验校正的 IC 版本

来源：
- Harvey, Liu, Zhu (2016) "...and the Cross-Section of Expected Returns" RFS
- Bailey, López de Prado (2014) "The Deflated Sharpe Ratio" SSRN

**核心问题**：
当你测试 N 个因子的 IC，挑出 |IC| 最大的那个 → **它有正向选择偏差**。
Harvey haircut：把"测过 N 次"考虑进去后，最好因子的 IC 还显著吗？

**Deflated IC 公式**（DSR 的 IC 版本）：

.. math::
    \text{DIC} = \Phi\left[(\hat{IR} - E[\max IR_0]) \sqrt{\frac{T-1}{1 - \gamma_3 \hat{IR} + \frac{\gamma_4 - 1}{4} \hat{IR}^2}}\right]

其中：
- :math:`\hat{IR}` = 观测信息比率（IC 均值 / IC 标准差）
- :math:`E[\max IR_0]` = 零假设下的最大期望 IR（依赖 N 个测试）
- :math:`T` = IC 序列长度
- :math:`\gamma_3, \gamma_4` = IC 序列的偏度、峰度（excess kurtosis）
- :math:`\Phi` = 标准正态 CDF

**DIC p-value < 0.05** → 即使做了 N 次多重检验，该因子仍显著（真信号）
**DIC p-value ≥ 0.05** → 在多重检验下失去显著性（伪信号候选）

**实践意义**：
- 当前 82 个 alpha 一起评估，p-value 应做 haircut
- 经验上 30-50% 的 alpha 在 Harvey haircut 下变伪信号

**典型用法**：
```python
from quant_scanner.factors.deflated_ic import deflated_ic_test
from quant_scanner.factors.alpha101 import Alpha101
from quant_scanner.factors.operators import log_returns

df = loader.load("NVDA", period="2y")
fwd = log_returns(df["close"]).shift(-5)
alpha = Alpha101()
results = alpha.compute_all(df)

# 跑 Deflated IC 检验
report = deflated_ic_test(results, fwd, n_trials=82)
print(report[["alpha", "ic_mean", "ir", "dic_pvalue", "significant"]])
```
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import numpy as np
import pandas as pd
from scipy import stats

from .operators import factor_ic, factor_ir


# Euler-Mascheroni 常数
_EULER_MASCHERONI = 0.5772156649015329


def expected_max_ir_under_null(
    n_trials: int,
    ic_std: float,
    method: str = "exact",
) -> float:
    """零假设下 N 次试验的最大期望 IR

    Args:
        n_trials: 测试的因子总数（N）
        ic_std: IC 序列的标准差（σ_IC）
        method:
            - "exact": Bailey-López de Prado (2014) 精确公式
            - "approx": 简化版 σ * sqrt(2 ln N)

    Returns:
        期望最大 IR（零假设下）
    """
    if n_trials < 2:
        return 0.0
    if method == "approx":
        return ic_std * np.sqrt(2 * np.log(n_trials))
    # exact: (1-γ) Φ⁻¹(1 - 1/N) + γ Φ⁻¹(1 - 1/(N·e))
    z1 = stats.norm.ppf(1 - 1.0 / n_trials)
    z2 = stats.norm.ppf(1 - 1.0 / (n_trials * np.e))
    em = _EULER_MASCHERONI
    benchmark = (1 - em) * z1 + em * z2
    return ic_std * benchmark


@dataclass
class DeflatedICResult:
    """单因子 Deflated IC 检验结果"""
    alpha_name: str
    ic_mean: float            # IC 均值
    ic_std: float             # IC 标准差
    ir: float                 # 信息比率 = ic_mean / ic_std
    skew: float               # IC 序列偏度
    kurtosis: float           # IC 序列超额峰度（excess kurtosis）
    n_obs: int                # 有效观测数（T）
    expected_max_ir: float    # 零假设下期望最大 IR
    dic_statistic: float      # Deflated IC 统计量
    dic_pvalue: float         # DIC p-value（< 0.05 = 真信号）
    significant: bool         # p-value < 0.05

    def as_dict(self) -> dict:
        return self.__dict__.copy()


def _rolling_ic_series(
    factor: pd.Series,
    forward_returns: pd.Series,
    window: int = 60,
) -> pd.Series:
    """滚动窗口计算 IC 时间序列（用于估计 IR/skew/kurt）"""
    aligned = pd.concat([factor, forward_returns], axis=1).dropna()
    if len(aligned) < window:
        # 不足一个窗口，用全样本单值
        if len(aligned) < 10:
            return pd.Series(dtype=float)
        ic_val = aligned.iloc[:, 0].corr(aligned.iloc[:, 1], method="spearman")
        return pd.Series([ic_val])
    ic_series = (
        aligned.iloc[:, 0]
        .rolling(window=window)
        .apply(lambda x: stats.spearmanr(x, aligned.iloc[:, 1].loc[x.index])[0],
               raw=False)
        .dropna()
    )
    return ic_series


def deflated_ic_test(
    factors: dict[str, pd.Series] | Iterable[tuple[str, pd.Series]],
    forward_returns: pd.Series,
    n_trials: int | None = None,
    rolling_window: int = 60,
    alpha: float = 0.05,
    method: str = "exact",
) -> pd.DataFrame:
    """对一批因子做 Deflated IC 多重检验校正

    Args:
        factors: {name: factor_series} 字典
        forward_returns: 前瞻收益序列
        n_trials: 测试总次数；None 则用 len(factors)
        rolling_window: 滚动 IC 窗口（估计 IR/skew/kurt 用）
        alpha: 显著性阈值（默认 0.05）
        method: expected_max_ir 计算方法

    Returns:
        DataFrame，按 |IC| 降序，含列：
        - alpha, ic_mean, ic_std, ir, skew, kurtosis, n_obs
        - expected_max_ir, dic_statistic, dic_pvalue, significant
    """
    if isinstance(factors, dict):
        items = list(factors.items())
    else:
        items = list(factors)
    if n_trials is None:
        n_trials = len(items)
    if n_trials < 2:
        n_trials = 2  # 避免 log(1)=0

    results: list[DeflatedICResult] = []
    for name, factor in items:
        try:
            ic_series = _rolling_ic_series(factor, forward_returns, window=rolling_window)
        except Exception:
            ic_series = pd.Series(dtype=float)

        if len(ic_series) < 5 or ic_series.std() == 0:
            results.append(DeflatedICResult(
                alpha_name=name,
                ic_mean=factor_ic(factor, forward_returns),
                ic_std=0.0,
                ir=0.0,
                skew=0.0,
                kurtosis=0.0,
                n_obs=len(ic_series),
                expected_max_ir=0.0,
                dic_statistic=0.0,
                dic_pvalue=1.0,
                significant=False,
            ))
            continue

        ic_mean = float(ic_series.mean())
        ic_std = float(ic_series.std())
        # DIC 检验 |IR|（因子可能正向或反向，方向不重要，看显著性）
        abs_ir = abs(ic_mean / ic_std) if ic_std > 0 else 0.0
        skew = float(stats.skew(ic_series))
        kurt = float(stats.kurtosis(ic_series))  # excess kurtosis
        T = len(ic_series)

        # Newey-West 调整：滚动 IC 序列高度重叠（窗口 N 日，每日滑动）
        # 一阶自相关 rho 可能很高，名义 T 严重高估有效独立样本数
        # T_eff = T * (1-rho)/(1+rho)
        rho = float(ic_series.autocorr(lag=1)) if T > 2 else 0.0
        if np.isnan(rho):
            rho = 0.0
        T_eff = max(int(T * (1 - rho) / max(1 + rho, 1e-8)), 5)

        emax = expected_max_ir_under_null(n_trials, 1.0, method=method)
        # 注：E[max IR] 已归一化到 σ=1；乘 ic_std 转回 IC 单位
        emax_ir = emax * ic_std

        # DIC 统计量（参考 DSR 公式，用 |IR| 替换 IR）
        denom_sq = 1 - skew * abs_ir + (kurt / 4.0) * (abs_ir ** 2)
        denom_sq = max(denom_sq, 1e-8)  # 防负
        dic_stat = (abs_ir - emax_ir) * np.sqrt((T_eff - 1) / denom_sq)
        # p-value: DIC 越大 → 越显著；p = 1 - Φ(dic_stat)
        dic_pvalue = float(1 - stats.norm.cdf(dic_stat))

        results.append(DeflatedICResult(
            alpha_name=name,
            ic_mean=ic_mean,
            ic_std=ic_std,
            ir=abs_ir,
            skew=skew,
            kurtosis=kurt,
            n_obs=T,
            expected_max_ir=emax_ir,
            dic_statistic=dic_stat,
            dic_pvalue=dic_pvalue,
            significant=dic_pvalue < alpha,
        ))

    df = pd.DataFrame([r.as_dict() for r in results])
    if len(df) == 0:
        return df
    # 统一列名：alpha_name → alpha（外部 API）
    df = df.rename(columns={"alpha_name": "alpha"})
    df = df.sort_values("ic_mean", key=lambda s: s.abs(), ascending=False).reset_index(drop=True)
    return df


def haircut_summary(report: pd.DataFrame, alpha: float = 0.05) -> dict:
    """Harvey haircut 摘要：N 个因子中有多少通过多重检验

    Returns:
        {
            "total": N,
            "significant_before": |IC| > 0.03 的数量,
            "significant_after": DIC p-value < alpha 的数量,
            "haircut_rate": 失效比例,
            "top_survivors": DIC 通过的前 10 个因子,
            "biggest_casualties": |IC|>0.05 但 DIC 不显著的因子,
        }
    """
    total = len(report)
    sig_before = (report["ic_mean"].abs() > 0.03).sum()
    sig_after = report["significant"].sum()
    haircut_rate = 1 - (sig_after / max(sig_before, 1))

    survivors = report[report["significant"]].head(10)["alpha"].tolist()
    casualties = report[
        (report["ic_mean"].abs() > 0.05) & (~report["significant"])
    ]["alpha"].tolist()

    return {
        "total": total,
        "significant_before": int(sig_before),
        "significant_after": int(sig_after),
        "haircut_rate": float(haircut_rate),
        "top_survivors": survivors,
        "biggest_casualties": casualties,
    }
