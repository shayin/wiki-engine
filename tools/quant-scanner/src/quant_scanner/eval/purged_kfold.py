"""Purged K-Fold Cross-Validation — López de Prado AfML Ch 7

来源：
- Marcos López de Prado (2018) *Advances in Financial Machine Learning*, Ch 7
- 原始论文：López de Prado (2015) "The Probability of Backtest Overfitting"
- Dixon, Halperin, Bilokon (2016) "Purged K-Fold Cross Validation for Trading Rules"

**核心问题**：
标准 K-fold CV 假设样本独立同分布 → 金融数据严重违反：
- Triple-Barrier 标签：t 时刻标签依赖 [t, t+vb] 的价格 → 与 t+1, t+2... 的标签**重叠**
- 滚动 IC：forward returns 滑动窗口 → 训练集和测试集样本**标签共享**
- 结果：训练集"偷看"测试集信息 → IC 评估虚高 → 策略过拟合

**Purged K-Fold 解决方案**：

1. **Purge（清洗）**：训练集中移除所有标签窗口 ∩ 测试集 的样本
   - 测试集是 [t_start, t_end]
   - 训练集样本 t_train 满足 [t_train, t_train + label_window] 与 [t_start, t_end] 相交 → 移除
2. **Embargo（禁运）**：测试集之后再加 τ bars 缓冲
   - 防止标签刚好结束、但仍有自相关性的样本影响测试集
   - 经验：embargo = 0.01 × T 或 ATR 一倍波动率对应的天数

**OOS（Out-of-Sample）IC 评估**：
对每个 fold，在 test indices 上算 IC；得到 K 个 OOS IC → 均值、标准差、
IR = mean / std → 真实样本外信息比率。

**OOS Deflated IC**：
对 N 个 alpha 跑完 Purged K-Fold，得到 N 个 OOS IR → 应用 Harvey haircut
（`factors/deflated_ic.py`） → 判断哪些 alpha 在多重检验下仍显著。

**实践意义**：
- 是评估 alpha 真实预测力的"金标准"——比单一全样本 IC 更保守、更可信
- 把过拟合 alpha 与真实 alpha 分开
- 与 Triple-Barrier（任务 #91）+ Meta-Labeling（任务 #96）形成 AfML 完整 ML 流水线

**典型用法**：
```python
from quant_scanner.eval import purged_cv_ic, purged_cv_deflated_ic
from quant_scanner.factors.alpha101 import Alpha101
from quant_scanner.factors.operators import log_returns

df = loader.load("NVDA", period="2y")
fwd = log_returns(df["close"]).shift(-5)
alphas = Alpha101().compute_all(df)

# 单 alpha OOS IC
result = purged_cv_ic(alphas["alpha_1"], fwd, n_splits=5, purge_bars=5, embargo_bars=2)
print(result["oos_ic_mean"], result["oos_ir"])

# 全部 alpha + Deflated IC（多重检验 × OOS）
report = purged_cv_deflated_ic(alphas, fwd, n_splits=5, purge_bars=5, embargo_bars=2)
print(report[["alpha", "oos_ir", "dic_pvalue", "significant"]])
```
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator

import numpy as np
import pandas as pd
from scipy import stats as sp_stats


# ============================================================
# 1. Purged K-Fold Splitter
# ============================================================

@dataclass
class PurgedKFold:
    """Purged K-Fold CV splitter（AfML Ch 7）

    Args:
        n_splits: K-fold 数（默认 5）
        purge_bars: 测试集前后被清洗的样本数（与标签窗口对齐）
        embargo_bars: 测试集之后额外隔离的样本数
    """
    n_splits: int = 5
    purge_bars: int = 10
    embargo_bars: int = 5

    def split(self, n_samples: int) -> Iterator[tuple[np.ndarray, np.ndarray]]:
        """生成 (train_idx, test_idx) 对

        流程：
        1. 把 [0, n_samples) 切成 K 段
        2. 第 k 段作 test
        3. 训练集中移除 [test_start - purge_bars, test_end + embargo_bars]
        """
        if self.n_splits < 2:
            raise ValueError("n_splits 必须 ≥ 2")
        if n_samples < self.n_splits * 2:
            raise ValueError(f"n_samples={n_samples} 太少，无法切 {self.n_splits} 折")

        indices = np.arange(n_samples)
        fold_size = n_samples // self.n_splits

        for k in range(self.n_splits):
            test_start = k * fold_size
            test_end = (k + 1) * fold_size if k < self.n_splits - 1 else n_samples
            test_idx = indices[test_start:test_end]

            # 清洗边界：测试集前 purge_bars 个样本 → 标签可能跨入测试集
            # 测试集后 embargo_bars 个样本 → 标签可能受测试集未来收益影响
            purge_start = max(0, test_start - self.purge_bars)
            embargo_end = min(n_samples, test_end + self.embargo_bars)

            mask = np.ones(n_samples, dtype=bool)
            mask[purge_start:embargo_end] = False
            # 但测试集本身要在 mask 中保留为 False（不在训练集里）
            train_idx = indices[mask]
            yield train_idx, test_idx


def purged_kfold_indices(
    n_samples: int,
    n_splits: int = 5,
    purge_bars: int = 10,
    embargo_bars: int = 5,
) -> list[tuple[np.ndarray, np.ndarray]]:
    """快捷函数：返回所有 (train_idx, test_idx) 对"""
    kf = PurgedKFold(n_splits=n_splits, purge_bars=purge_bars, embargo_bars=embargo_bars)
    return list(kf.split(n_samples))


# ============================================================
# 2. OOS IC 评估
# ============================================================

def _spearman_ic(factor: pd.Series, target: pd.Series) -> float:
    """单 fold Spearman IC（rank correlation）"""
    aligned = pd.concat([factor, target], axis=1).dropna()
    if len(aligned) < 10:
        return float("nan")
    rho, _ = sp_stats.spearmanr(aligned.iloc[:, 0], aligned.iloc[:, 1])
    return float(rho) if rho == rho else float("nan")


def purged_cv_ic(
    factor: pd.Series,
    target: pd.Series,
    n_splits: int = 5,
    purge_bars: int = 10,
    embargo_bars: int = 5,
) -> dict:
    """单 alpha 的 Purged K-Fold OOS IC 评估

    Args:
        factor: 因子序列
        target: 前瞻收益（forward returns）或 triple-barrier label
        n_splits: K-fold 数
        purge_bars: 清洗窗口
        embargo_bars: 禁运窗口

    Returns:
        dict:
        - fold_ics: list[float]，每折 OOS IC
        - oos_ic_mean: 平均 OOS IC
        - oos_ic_std: OOS IC 标准差
        - oos_ir: OOS 信息比率 = mean / std
        - n_effective: 实际有效 fold 数（IC 非 NaN）
        - n_train_total / n_test_total: 训练/测试样本数总和
    """
    if not isinstance(factor, pd.Series):
        factor = pd.Series(factor)
    if not isinstance(target, pd.Series):
        target = pd.Series(target)

    factor = factor.dropna()
    target = target.dropna()
    common = factor.index.intersection(target.index)
    factor = factor.loc[common]
    target = target.loc[common]

    n = len(common)
    fold_ics: list[float] = []
    n_train_total = 0
    n_test_total = 0
    for train_idx, test_idx in purged_kfold_indices(
        n, n_splits=n_splits, purge_bars=purge_bars, embargo_bars=embargo_bars,
    ):
        f_test = factor.iloc[test_idx]
        t_test = target.iloc[test_idx]
        ic = _spearman_ic(f_test, t_test)
        fold_ics.append(ic)
        n_train_total += len(train_idx)
        n_test_total += len(test_idx)

    valid = [x for x in fold_ics if x == x]  # filter NaN
    n_effective = len(valid)
    if n_effective == 0:
        return {
            "fold_ics": fold_ics,
            "oos_ic_mean": float("nan"),
            "oos_ic_std": float("nan"),
            "oos_ir": float("nan"),
            "n_effective": 0,
            "n_train_total": n_train_total,
            "n_test_total": n_test_total,
        }
    ic_arr = np.array(valid)
    mean = float(ic_arr.mean())
    std = float(ic_arr.std(ddof=1)) if n_effective > 1 else 0.0
    ir = float(mean / std) if std > 1e-12 else float("nan")
    return {
        "fold_ics": fold_ics,
        "oos_ic_mean": mean,
        "oos_ic_std": std,
        "oos_ir": ir,
        "n_effective": n_effective,
        "n_train_total": n_train_total,
        "n_test_total": n_test_total,
    }


# ============================================================
# 3. OOS Deflated IC（多重检验 + Purged CV）
# ============================================================

def _emax_ir_null(n_trials: int) -> float:
    """零假设下 N 个独立 IR 的期望最大值（Bailey-López de Prado 2014）

    E[max IR_0] = (1-γ) Φ⁻¹(1 - 1/N) + γ Φ⁻¹(1 - 1/(N·e))
    其中 γ ≈ 0.5772（Euler-Mascheroni 常数）
    """
    if n_trials <= 1:
        return 0.0
    gamma = 0.5772156649015329
    emax = (1 - gamma) * sp_stats.norm.ppf(1 - 1 / n_trials) + \
        gamma * sp_stats.norm.ppf(1 - 1 / (n_trials * np.e))
    return float(emax)


def purged_cv_deflated_ic(
    factors: dict[str, pd.Series],
    target: pd.Series,
    n_splits: int = 5,
    purge_bars: int = 10,
    embargo_bars: int = 5,
    n_trials: int | None = None,
    alpha: float = 0.05,
) -> pd.DataFrame:
    """对 N 个 alpha 跑 Purged K-Fold，再应用 Harvey haircut（Deflated IC）

    流程：
    1. 对每个 alpha 跑 Purged K-Fold → 得到 K 个 OOS IC
    2. 计算 OOS IR = mean / std
    3. 应用 Deflated IC：检验该 OOS IR 是否在 N 次试验下仍显著
    4. DIC p-value < alpha → 真信号（即使在 N 次测试下）

    Args:
        factors: {alpha_name: factor_series}
        target: 前瞻收益序列
        n_splits: K-fold 数
        purge_bars: 清洗窗口
        embargo_bars: 禁运窗口
        n_trials: 多重检验次数（默认 = len(factors)）
        alpha: DIC 显著性阈值（默认 0.05）

    Returns:
        DataFrame，含列：
        - alpha: 因子名
        - oos_ic_mean, oos_ic_std, oos_ir: OOS 统计
        - abs_oos_ir: |OOS IR|
        - expected_max_ir: 零假设下 N 次试验的最大期望 IR
        - dic_statistic: DIC 检验统计量
        - dic_pvalue: DIC p-value
        - significant: 是否通过 0.05 显著性
        - n_effective: 有效 fold 数
    """
    if n_trials is None:
        n_trials = len(factors)
    emax_ir = _emax_ir_null(n_trials)

    rows: list[dict] = []
    for name, f in factors.items():
        r = purged_cv_ic(
            f, target,
            n_splits=n_splits, purge_bars=purge_bars, embargo_bars=embargo_bars,
        )
        ir = r["oos_ir"]
        T = r["n_effective"]
        abs_ir = abs(ir) if ir == ir else 0.0

        if T < 2 or abs_ir < 1e-12:
            dic_stat = 0.0
            dic_p = 1.0
        else:
            # Newey-West style T_eff adjustment (reuse from deflated_ic.py logic)
            try:
                ic_series = pd.Series(r["fold_ics"]).dropna()
                rho = float(ic_series.autocorr(lag=1)) if len(ic_series) > 2 else 0.0
                if np.isnan(rho):
                    rho = 0.0
                T_eff = max(int(T * (1 - rho) / max(1 + rho, 1e-8)), 2)
            except Exception:
                T_eff = T

            denom_sq = 1.0  # 简化：忽略偏度/峰度修正（fold IC 量通常不足以估计）
            dic_stat = (abs_ir - emax_ir) * np.sqrt((T_eff - 1) / denom_sq)
            dic_p = 1.0 - sp_stats.norm.cdf(dic_stat)

        rows.append({
            "alpha": name,
            "oos_ic_mean": r["oos_ic_mean"],
            "oos_ic_std": r["oos_ic_std"],
            "oos_ir": ir,
            "abs_oos_ir": abs_ir,
            "expected_max_ir": emax_ir,
            "dic_statistic": dic_stat,
            "dic_pvalue": dic_p,
            "significant": dic_p < alpha,
            "n_effective": T,
        })

    report = pd.DataFrame(rows)
    if len(report) > 0:
        report = report.sort_values("abs_oos_ir", ascending=False).reset_index(drop=True)
    return report


def oos_summary(report: pd.DataFrame) -> dict:
    """Purged CV + Deflated IC 评估摘要

    Returns:
        {
            "total_alphas": N,
            "oos_effective": |OOS IC mean| > 0.03 的数量,
            "dic_survivors": DIC p<0.05 的数量,
            "oos_haircut_rate": DIC 砍掉的比例,
            "top_survivors": DIC 通过的 top 5,
            "biggest_casualties": |OOS IC|>0.03 但 DIC 不过的 top 5,
            "median_oos_ir": 所有 alpha OOS IR 中位数,
        }
    """
    total = len(report)
    if total == 0:
        return {
            "total_alphas": 0,
            "oos_effective": 0,
            "dic_survivors": 0,
            "oos_haircut_rate": 0.0,
            "top_survivors": [],
            "biggest_casualties": [],
            "median_oos_ir": float("nan"),
        }
    oos_eff = (report["oos_ic_mean"].abs() > 0.03).sum()
    dic_surv = report["significant"].sum()
    rate = float((oos_eff - dic_surv) / oos_eff) if oos_eff > 0 else 0.0

    survivors = report[report["significant"]].sort_values("abs_oos_ir", ascending=False)
    top_surv = survivors["alpha"].head(5).tolist()

    casualties = report[
        (report["oos_ic_mean"].abs() > 0.03) & (~report["significant"])
    ].sort_values("oos_ic_mean", key=lambda s: s.abs(), ascending=False)
    big_casual = casualties["alpha"].head(5).tolist()

    return {
        "total_alphas": total,
        "oos_effective": int(oos_eff),
        "dic_survivors": int(dic_surv),
        "oos_haircut_rate": rate,
        "top_survivors": top_surv,
        "biggest_casualties": big_casual,
        "median_oos_ir": float(report["oos_ir"].abs().median()),
    }
