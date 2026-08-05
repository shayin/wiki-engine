"""SHAP 因子评估测试（任务 #86）"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quant_scanner.factors.shap_eval import ShapFactorEvaluator


@pytest.fixture
def synthetic_factors_with_signal() -> tuple[dict, pd.Series]:
    """构造 5 个 alpha 因子，其中 alpha_1/alpha_3 与前瞻收益强相关"""
    rng = np.random.default_rng(42)
    n = 300
    dates = pd.date_range("2025-01-01", periods=n, freq="B")

    # alpha_1 强正向（与 fwd 完全正相关 + 噪声）
    fwd_signal = rng.normal(0, 0.05, n)
    alpha_1 = fwd_signal + rng.normal(0, 0.005, n)
    # alpha_3 强反向（fwd = -alpha_3 + 噪声）
    alpha_3 = -fwd_signal + rng.normal(0, 0.005, n)
    # alpha_2/4/5 噪声（与 fwd 无关）
    alpha_2 = rng.normal(0, 1, n)
    alpha_4 = rng.normal(0, 1, n)
    alpha_5 = rng.normal(0, 1, n)

    features = {
        "alpha_1": pd.Series(alpha_1, index=dates),
        "alpha_2": pd.Series(alpha_2, index=dates),
        "alpha_3": pd.Series(alpha_3, index=dates),
        "alpha_4": pd.Series(alpha_4, index=dates),
        "alpha_5": pd.Series(alpha_5, index=dates),
    }
    fwd = pd.Series(fwd_signal, index=dates)
    return features, fwd


def test_shap_evaluator_initialization():
    """初始化参数"""
    ev = ShapFactorEvaluator(n_estimators=100, max_depth=3)
    assert ev.n_estimators == 100
    assert ev.max_depth == 3


def test_evaluate_returns_dict(synthetic_factors_with_signal):
    """evaluate 返回完整 dict"""
    features, fwd = synthetic_factors_with_signal
    ev = ShapFactorEvaluator()
    result = ev.evaluate(features, fwd, top_n=3)
    assert isinstance(result, dict)
    for key in ["top_features", "feature_importance", "shap_values",
                "feature_names", "test_r2", "n_samples"]:
        assert key in result, f"缺字段 {key}"


def test_top_features_strong_signal_ranked_high(synthetic_factors_with_signal):
    """强信号因子（alpha_1/alpha_3）应在 top 2"""
    features, fwd = synthetic_factors_with_signal
    ev = ShapFactorEvaluator()
    result = ev.evaluate(features, fwd, top_n=5)
    top_names = [t[0] for t in result["top_features"]]
    # alpha_1 或 alpha_3 应在 top 2
    assert "alpha_1" in top_names[:2] or "alpha_3" in top_names[:2], \
        f"强信号因子应在 top 2，实际 top: {top_names[:2]}"


def test_top_features_direction(synthetic_factors_with_signal):
    """alpha_1 应正向、alpha_3 应反向"""
    features, fwd = synthetic_factors_with_signal
    ev = ShapFactorEvaluator()
    result = ev.evaluate(features, fwd, top_n=5)
    feature_dir = {name: direction for name, _, direction in result["top_features"]}
    # alpha_1 与 fwd 正相关 → positive
    if "alpha_1" in feature_dir:
        assert feature_dir["alpha_1"] == "positive", \
            f"alpha_1 应正向，实际 {feature_dir['alpha_1']}"
    # alpha_3 与 fwd 反向 → negative
    if "alpha_3" in feature_dir:
        assert feature_dir["alpha_3"] == "negative", \
            f"alpha_3 应反向，实际 {feature_dir['alpha_3']}"


def test_evaluate_simple_returns_list(synthetic_factors_with_signal):
    """evaluate_simple 返回 list 不含矩阵"""
    features, fwd = synthetic_factors_with_signal
    ev = ShapFactorEvaluator()
    top = ev.evaluate_simple(features, fwd, top_n=3)
    assert isinstance(top, list)
    assert len(top) == 3
    for item in top:
        assert len(item) == 3  # (name, shap_value, direction)


def test_insufficient_data():
    """样本不足 → 返回 error"""
    n = 20
    dates = pd.date_range("2025-01-01", periods=n)
    features = {"alpha_1": pd.Series(np.random.randn(n), index=dates)}
    fwd = pd.Series(np.random.randn(n), index=dates)
    ev = ShapFactorEvaluator()
    result = ev.evaluate(features, fwd)
    assert "error" in result


def test_missing_deps_handling():
    """缺依赖时优雅降级（mock shap import 失败）"""
    import sys
    old_shap = sys.modules.get("shap")
    sys.modules["shap"] = None  # 触发 ImportError

    try:
        features = {"a": pd.Series([1.0, 2.0, 3.0] * 30)}
        fwd = pd.Series([0.01, 0.02, 0.03] * 30)
        ev = ShapFactorEvaluator()
        result = ev.evaluate(features, fwd)
        assert "error" in result
    finally:
        if old_shap is not None:
            sys.modules["shap"] = old_shap
        else:
            sys.modules.pop("shap", None)


def test_test_r2_is_float(synthetic_factors_with_signal):
    """test_r2 是 float"""
    features, fwd = synthetic_factors_with_signal
    ev = ShapFactorEvaluator()
    result = ev.evaluate(features, fwd)
    assert isinstance(result["test_r2"], float)


def test_n_samples_correct(synthetic_factors_with_signal):
    """n_samples 反映 dropna 后的样本数"""
    features, fwd = synthetic_factors_with_signal
    # 加一些 NaN
    features["alpha_1"].iloc[:10] = np.nan
    ev = ShapFactorEvaluator()
    result = ev.evaluate(features, fwd)
    # 原 300，删 10 NaN → 290
    assert result["n_samples"] <= 300
    assert result["n_samples"] >= 250
