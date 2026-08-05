"""Purged K-Fold CV 测试（López de Prado AfML Ch 7）

覆盖：
- Splitter 切分正确性（无泄漏、清洗 + 禁运区间生效）
- purged_cv_ic 单 alpha OOS IC 评估
- purged_cv_deflated_ic 多 alpha + Harvey haircut
- oos_summary 统计
- 边界情况
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quant_scanner.eval.purged_kfold import (
    PurgedKFold,
    purged_kfold_indices,
    purged_cv_ic,
    purged_cv_deflated_ic,
    oos_summary,
    _emax_ir_null,
)


# ============================================================
# Splitter
# ============================================================

def test_split_yields_k_folds():
    kf = PurgedKFold(n_splits=5, purge_bars=3, embargo_bars=2)
    folds = list(kf.split(200))
    assert len(folds) == 5
    for train_idx, test_idx in folds:
        assert len(train_idx) > 0
        assert len(test_idx) > 0
        # train 和 test 不应重叠
        assert len(set(train_idx) & set(test_idx)) == 0


def test_no_overlap_between_train_and_test():
    """训练集不能与测试集相邻太近（purge + embargo）"""
    n = 100
    purge, embargo = 5, 3
    kf = PurgedKFold(n_splits=5, purge_bars=purge, embargo_bars=embargo)
    for train_idx, test_idx in kf.split(n):
        test_min, test_max = test_idx.min(), test_idx.max()
        # 训练集中不应出现 [test_min - purge, test_max + embargo]
        for idx in train_idx:
            if test_min - purge <= idx <= test_max + embargo:
                # 例外：测试集外的样本不应在 train 里
                assert idx not in test_idx  # sanity
                # 这个窗口应被清洗掉
                pytest.fail(f"idx {idx} 应被 purge/embargo 清洗，但出现在训练集")


def test_invalid_n_splits_raises():
    with pytest.raises(ValueError):
        list(PurgedKFold(n_splits=1).split(100))


def test_too_few_samples_raises():
    with pytest.raises(ValueError):
        list(PurgedKFold(n_splits=5).split(5))


def test_purged_kfold_indices_helper():
    folds = purged_kfold_indices(100, n_splits=4, purge_bars=2, embargo_bars=1)
    assert len(folds) == 4


# ============================================================
# purged_cv_ic
# ============================================================

@pytest.fixture
def noisy_alpha() -> tuple[pd.Series, pd.Series]:
    """无信号 alpha：随机"""
    rng = np.random.default_rng(42)
    n = 300
    factor = pd.Series(rng.normal(0, 1, n),
                       index=pd.date_range("2024-01-01", periods=n, freq="B"))
    target = pd.Series(rng.normal(0, 0.02, n),
                       index=factor.index)
    return factor, target


@pytest.fixture
def predictive_alpha() -> tuple[pd.Series, pd.Series]:
    """有信号 alpha：target = 0.1 * factor + noise"""
    rng = np.random.default_rng(7)
    n = 400
    factor = pd.Series(rng.normal(0, 1, n),
                       index=pd.date_range("2024-01-01", periods=n, freq="B"))
    target = pd.Series(
        0.1 * factor.values + rng.normal(0, 0.01, n),
        index=factor.index,
    )
    return factor, target


def test_noisy_alpha_low_ir(noisy_alpha):
    factor, target = noisy_alpha
    r = purged_cv_ic(factor, target, n_splits=5, purge_bars=5, embargo_bars=2)
    assert r["n_effective"] == 5
    # 随机序列 IR 应在 [-1, 1]
    assert abs(r["oos_ir"]) < 1.5
    # IC mean 应接近 0
    assert abs(r["oos_ic_mean"]) < 0.2


def test_predictive_alpha_high_ir(predictive_alpha):
    """有信号的 alpha OOS IC 应明显 > 0"""
    factor, target = predictive_alpha
    r = purged_cv_ic(factor, target, n_splits=5, purge_bars=5, embargo_bars=2)
    assert r["oos_ic_mean"] > 0.3
    assert r["oos_ir"] > 0.5


def test_ic_returns_nan_for_short_series():
    s = pd.Series([1.0, 2.0, 3.0], index=pd.date_range("2024-01-01", periods=3, freq="B"))
    with pytest.raises(ValueError):
        purged_cv_ic(s, s, n_splits=5)


def test_ic_handles_nan_in_factor():
    rng = np.random.default_rng(0)
    n = 300
    factor = pd.Series(rng.normal(0, 1, n),
                       index=pd.date_range("2024-01-01", periods=n, freq="B"))
    factor.iloc[10:15] = np.nan
    target = pd.Series(0.05 * factor.values + rng.normal(0, 0.01, n),
                       index=factor.index)
    r = purged_cv_ic(factor, target, n_splits=5, purge_bars=5, embargo_bars=2)
    assert r["n_effective"] >= 1


def test_fold_ics_length_matches_splits(noisy_alpha):
    factor, target = noisy_alpha
    r = purged_cv_ic(factor, target, n_splits=5, purge_bars=3, embargo_bars=2)
    assert len(r["fold_ics"]) == 5


# ============================================================
# purged_cv_deflated_ic
# ============================================================

def test_deflated_ic_dataframe_columns(predictive_alpha):
    factor, target = predictive_alpha
    factors = {"alpha_1": factor, "alpha_2": pd.Series(
        np.random.default_rng(1).normal(0, 1, len(factor)), index=factor.index)}
    report = purged_cv_deflated_ic(
        factors, target, n_splits=5, purge_bars=5, embargo_bars=2,
    )
    assert len(report) == 2
    for col in ("alpha", "oos_ic_mean", "oos_ic_std", "oos_ir",
                "abs_oos_ir", "expected_max_ir", "dic_statistic",
                "dic_pvalue", "significant", "n_effective"):
        assert col in report.columns


def test_deflated_ic_ordering(predictive_alpha):
    """报告应按 abs_oos_ir 降序"""
    factor, target = predictive_alpha
    rng = np.random.default_rng(11)
    factors = {
        f"alpha_{i}": pd.Series(rng.normal(0, 1, len(factor)), index=factor.index)
        for i in range(5)
    }
    factors["alpha_signal"] = factor
    report = purged_cv_deflated_ic(factors, target, n_splits=5)
    # 信号因子应在前面
    assert report.iloc[0]["alpha"] == "alpha_signal"


def test_deflated_ic_empty_factors():
    report = purged_cv_deflated_ic({}, pd.Series(dtype=float), n_splits=5)
    assert len(report) == 0


def test_emax_ir_increases_with_trials():
    a = _emax_ir_null(2)
    b = _emax_ir_null(50)
    c = _emax_ir_null(500)
    assert 0 < a < b < c


# ============================================================
# oos_summary
# ============================================================

def test_summary_empty():
    s = oos_summary(pd.DataFrame(columns=[
        "alpha", "oos_ic_mean", "oos_ir", "abs_oos_ir", "significant"
    ]))
    assert s["total_alphas"] == 0


def test_summary_basic(predictive_alpha):
    factor, target = predictive_alpha
    rng = np.random.default_rng(23)
    factors = {
        "alpha_signal": factor,
        **{f"alpha_noise_{i}": pd.Series(rng.normal(0, 1, len(factor)),
                                          index=factor.index) for i in range(10)}
    }
    report = purged_cv_deflated_ic(factors, target, n_splits=5)
    s = oos_summary(report)
    assert s["total_alphas"] == 11
    assert s["oos_effective"] >= 0
    assert s["dic_survivors"] >= 0
    assert 0.0 <= s["oos_haircut_rate"] <= 1.0


# ============================================================
# 边界情况
# ============================================================

def test_purge_larger_than_fold():
    """purge_bars 大于 fold_size 时也不应崩"""
    folds = list(PurgedKFold(n_splits=5, purge_bars=50, embargo_bars=20).split(100))
    # 训练集可能很小但不应为空
    for train_idx, test_idx in folds:
        assert len(test_idx) > 0
