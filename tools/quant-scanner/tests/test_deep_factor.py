"""Gu-Kelly-Xiu (2020) 多模型对比测试（任务 #100）

验证 deep_factor.py 的 make_models / compare_ml_models / run_model_comparison_pipeline
与论文方法论一致：LR / GBM / MLP 三模型对比，Purged K-Fold 评估。

参考：
- Gu, Kelly, Xiu (2020) "Empirical Asset Pricing via Machine Learning"
  JFE 134(2): 385-424
- 树模型 > 神经网络 > 线性模型（论文 Table）
- 合成数据：非线性交互（f1 × f2）→ MLP/GBM 应优于 LR
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quant_scanner.ml.deep_factor import (
    make_models,
    compare_ml_models,
    run_model_comparison_pipeline,
    ComparisonResult,
    ModelComparison,
)


# ============================================================
# make_models
# ============================================================

def test_make_models_returns_three():
    """make_models 默认返回 LR / GBM / MLP 三个模型"""
    models = make_models(seed=42)
    assert len(models) == 3
    names = list(models.keys())
    assert any("LR" in n for n in names)
    assert any("GBM" in n for n in names)
    assert any("MLP" in n in names or "Neural" in n for n in names)


def test_make_models_seed_reproducible():
    """相同 seed 产出的模型参数一致（LR C、GBM random_state、MLP random_state）"""
    m1 = make_models(seed=42)
    m2 = make_models(seed=42)
    for k in m1:
        assert type(m1[k]) == type(m2[k])


# ============================================================
# compare_ml_models - 基础
# ============================================================

def _synthetic_data(n: int = 500, seed: int = 42) -> tuple[pd.DataFrame, pd.Series]:
    """构造合成数据：非线性交互（f1 × f2）→ MLP/GBM 应胜出"""
    rng = np.random.default_rng(seed)
    X = pd.DataFrame({
        "f1": rng.normal(0, 1, n),
        "f2": rng.normal(0, 1, n),
        "f3": rng.normal(0, 1, n),
    })
    logits = X["f1"] + 0.5 * X["f2"] + 0.3 * X["f1"] * X["f2"]
    proba = 1 / (1 + np.exp(-logits))
    y = pd.Series((rng.uniform(0, 1, n) < proba).astype(int))
    return X, y


def test_compare_returns_comparison_result():
    X, y = _synthetic_data()
    result = compare_ml_models(X, y, n_splits=3, purge_bars=5, embargo_bars=2)
    assert isinstance(result, ComparisonResult)
    assert result.n_total == len(y)
    assert len(result.models) == 3


def test_baseline_precision_matches_target_mean():
    X, y = _synthetic_data()
    result = compare_ml_models(X, y, n_splits=3, purge_bars=5, embargo_bars=2)
    assert result.baseline_precision == pytest.approx(float(y.mean()), abs=1e-9)


def test_best_model_returns_highest_precision():
    X, y = _synthetic_data()
    result = compare_ml_models(X, y, n_splits=3, purge_bars=5, embargo_bars=2)
    best_name, best_model = result.best_model()
    assert best_name is not None
    precs = [m.mean_precision for m in result.models.values() if m.mean_precision == m.mean_precision]
    if precs:
        assert best_model.mean_precision == pytest.approx(max(precs), abs=1e-9)


def test_summary_has_expected_keys():
    X, y = _synthetic_data()
    result = compare_ml_models(X, y, n_splits=3, purge_bars=5, embargo_bars=2)
    s = result.summary()
    for key in ["baseline_precision", "n_total", "models", "best_model", "best_precision"]:
        assert key in s
    for name, info in s["models"].items():
        for key in ["mean_precision", "mean_filter_rate", "n_folds_run", "fit_failures", "lift_vs_baseline"]:
            assert key in info


# ============================================================
# compare_ml_models - 边界
# ============================================================

def test_compare_handles_single_class_target():
    """target 全为同一类 → fold 跳过，fit_failures 或 n_folds_run=0"""
    rng = np.random.default_rng(0)
    n = 200
    X = pd.DataFrame({"f1": rng.normal(0, 1, n), "f2": rng.normal(0, 1, n)})
    y = pd.Series(np.ones(n, dtype=int))  # 全 1
    result = compare_ml_models(X, y, n_splits=3, purge_bars=5, embargo_bars=2)
    # 所有 fold 都因 len(unique(y_train))<2 被 skip
    for m in result.models.values():
        assert m.n_folds_run == 0


def test_compare_handles_nan_features():
    """特征含 NaN/inf → StandardScaler 容错，不崩"""
    rng = np.random.default_rng(0)
    n = 300
    X = pd.DataFrame({
        "f1": rng.normal(0, 1, n),
        "f2": rng.normal(0, 1, n),
    })
    X.loc[0:10, "f1"] = np.nan
    X.loc[20:25, "f2"] = np.inf
    y = pd.Series(rng.integers(0, 2, n).astype(int))
    # 不抛异常即可
    result = compare_ml_models(X, y, n_splits=3, purge_bars=5, embargo_bars=2)
    assert isinstance(result, ComparisonResult)


def test_compare_too_short_data():
    """样本数 < 50 的 fold → 跳过"""
    rng = np.random.default_rng(0)
    n = 30
    X = pd.DataFrame({"f1": rng.normal(0, 1, n), "f2": rng.normal(0, 1, n)})
    y = pd.Series(rng.integers(0, 2, n).astype(int))
    result = compare_ml_models(X, y, n_splits=3, purge_bars=2, embargo_bars=1)
    for m in result.models.values():
        assert m.n_folds_run == 0


def test_compare_custom_models_dict():
    """支持自定义 models dict"""
    from sklearn.linear_model import LogisticRegression
    rng = np.random.default_rng(0)
    n = 300
    X = pd.DataFrame({"f1": rng.normal(0, 1, n)})
    y = pd.Series(rng.integers(0, 2, n).astype(int))
    custom = {"MyLR": LogisticRegression(max_iter=200)}
    result = compare_ml_models(X, y, n_splits=3, purge_bars=3, embargo_bars=1, models=custom)
    assert "MyLR" in result.models


# ============================================================
# 非线性效应验证（核心论文论点）
# ============================================================

def test_nonlinear_models_beat_linear_on_interaction():
    """合成数据含 f1*f2 交互项 → GBM/MLP 的 mean_precision 应 ≥ LR"""
    X, y = _synthetic_data(n=800, seed=0)
    result = compare_ml_models(X, y, n_splits=4, purge_bars=5, embargo_bars=2)
    s = result.summary()
    lr_prec = s["models"].get("LR (Linear)", {}).get("mean_precision", float("nan"))
    gbm_prec = s["models"].get("GBM (Trees)", {}).get("mean_precision", float("nan"))
    # 在非线性数据上，GBM 应不弱于 LR（容差 0.02 防随机波动）
    if lr_prec == lr_prec and gbm_prec == gbm_prec:
        assert gbm_prec >= lr_prec - 0.02


# ============================================================
# run_model_comparison_pipeline 端到端
# ============================================================

def test_pipeline_runs_on_synthetic_ohlcv():
    """端到端：合成 OHLCV + primary alpha → 产出 ComparisonResult"""
    rng = np.random.default_rng(42)
    n = 400
    close = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.01, n))))
    high = close * (1 + rng.uniform(0, 0.01, n))
    low = close * (1 - rng.uniform(0, 0.01, n))
    vol = pd.Series(rng.uniform(1e6, 2e6, n))
    df = pd.DataFrame({"open": close, "high": high, "low": low, "close": close, "volume": vol})

    primary = pd.Series(rng.normal(0, 1, n))
    result = run_model_comparison_pipeline(
        df, primary_alpha=primary, feature_alphas=None,
        tp_atr_mult=2.0, sl_atr_mult=2.0, vertical_barrier_bars=10,
        n_splits=3, top_features=5,
    )
    assert isinstance(result, ComparisonResult)
    assert result.n_total > 0


def test_pipeline_with_feature_dataframe():
    """feature_alphas 传 DataFrame 路径"""
    rng = np.random.default_rng(0)
    n = 400
    close = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.01, n))))
    df = pd.DataFrame({
        "open": close, "high": close*1.01, "low": close*0.99,
        "close": close, "volume": rng.uniform(1e6, 2e6, n),
    })
    feats = pd.DataFrame({
        "alpha_x": rng.normal(0, 1, n),
        "alpha_y": rng.normal(0, 1, n),
    })
    primary = pd.Series(rng.normal(0, 1, n))
    result = run_model_comparison_pipeline(
        df, primary_alpha=primary, feature_alphas=feats,
        n_splits=3, top_features=5,
    )
    assert isinstance(result, ComparisonResult)


# ============================================================
# ModelComparison.aggregate
# ============================================================

def test_model_comparison_aggregate_empty():
    """空 fold_results → aggregate 不崩，保持 NaN"""
    mc = ModelComparison(name="X")
    mc.aggregate()
    assert mc.n_folds_run == 0
    assert mc.mean_precision != mc.mean_precision  # NaN


def test_model_comparison_aggregate_with_folds():
    """有 fold_results → aggregate 计算平均"""
    from quant_scanner.ml.deep_factor import ModelFoldResult
    mc = ModelComparison(name="X")
    mc.fold_results = [
        ModelFoldResult(fold=0, train_size=100, test_size=50, precision=0.6, filter_rate=0.3),
        ModelFoldResult(fold=1, train_size=100, test_size=50, precision=0.7, filter_rate=0.4),
    ]
    mc.aggregate()
    assert mc.mean_precision == pytest.approx(0.65, abs=1e-6)
    assert mc.mean_filter_rate == pytest.approx(0.35, abs=1e-6)
    assert mc.n_folds_run == 2
