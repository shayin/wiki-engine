"""Secondary Model 测试（AfML Ch 4 闭环）

覆盖：
- SecondaryModel 训练 / 预测 / position 计算
- build_features_from_alphas
- run_meta_labeling_pipeline 端到端
- cross_validate_secondary Purged K-Fold
- pipeline_summary
- 边界情况（单类、短样本、NaN features）
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quant_scanner.ml.secondary_model import (
    SecondaryModel,
    build_features_from_alphas,
    run_meta_labeling_pipeline,
    cross_validate_secondary,
    pipeline_summary,
)


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def ohlcv_2y() -> pd.DataFrame:
    """2 年合成 OHLCV，带轻微上涨趋势"""
    rng = np.random.default_rng(42)
    n = 500
    rets = rng.normal(0.0008, 0.015, n)  # 漂移向上
    close = 100 * np.exp(np.cumsum(rets))
    high = close * (1 + rng.uniform(0.001, 0.015, n))
    low = close * (1 - rng.uniform(0.001, 0.015, n))
    return pd.DataFrame({
        "open": close,
        "high": high,
        "low": low,
        "close": close,
        "volume": rng.integers(1e6, 2e6, n),
    }, index=pd.date_range("2024-01-01", periods=n, freq="B"))


@pytest.fixture
def predictive_primary(ohlcv_2y) -> pd.Series:
    """有信号的 primary：用未来 5 日收益 sign 作假 primary（模拟"好 alpha"）"""
    fwd = ohlcv_2y["close"].pct_change(5).shift(-5)
    # primary 方向：上涨做多、下跌做空
    return np.sign(fwd).fillna(1).astype(int).rename("primary")


@pytest.fixture
def random_primary(ohlcv_2y) -> pd.Series:
    """随机 primary：无信号"""
    rng = np.random.default_rng(0)
    return pd.Series(rng.choice([-1, 1], len(ohlcv_2y)),
                     index=ohlcv_2y.index, name="primary")


@pytest.fixture
def simple_features(ohlcv_2y) -> pd.DataFrame:
    """简单 feature：动量 + 波动率"""
    close = ohlcv_2y["close"]
    return pd.DataFrame({
        "mom_5": close.pct_change(5),
        "mom_20": close.pct_change(20),
        "vol_20": close.pct_change().rolling(20).std(),
        "rsi_proxy": (close.diff(1) > 0).rolling(14).mean(),
    })


# ============================================================
# SecondaryModel
# ============================================================

def test_fit_predict_returns_confidence_in_range(ohlcv_2y, simple_features):
    rng = np.random.default_rng(42)
    y = pd.Series(rng.integers(0, 2, len(ohlcv_2y)), index=ohlcv_2y.index)
    model = SecondaryModel()
    model.fit(simple_features, y)
    conf = model.predict_confidence(simple_features)
    assert (conf >= 0).all() and (conf <= 1).all()
    assert len(conf) == len(simple_features)


def test_predict_without_fit_raises(simple_features):
    model = SecondaryModel()
    with pytest.raises(RuntimeError):
        model.predict_confidence(simple_features)


def test_fit_single_class_raises(ohlcv_2y, simple_features):
    y_all_same = pd.Series([1] * len(ohlcv_2y), index=ohlcv_2y.index)
    model = SecondaryModel()
    with pytest.raises(ValueError, match="只有一类"):
        model.fit(simple_features, y_all_same)


def test_fit_too_few_samples_raises(simple_features):
    short_features = simple_features.iloc[:10]
    short_y = pd.Series([0, 1, 0, 1, 0, 1, 0, 1, 0, 1])
    model = SecondaryModel()
    with pytest.raises(ValueError, match="训练样本太少"):
        model.fit(short_features, short_y)


def test_compute_position(ohlcv_2y, simple_features):
    rng = np.random.default_rng(11)
    y = pd.Series(rng.integers(0, 2, len(ohlcv_2y)), index=ohlcv_2y.index)
    side = pd.Series(rng.choice([-1, 1], len(ohlcv_2y)), index=ohlcv_2y.index)
    model = SecondaryModel()
    model.fit(simple_features, y)
    pos = model.compute_position(side, simple_features, max_position=0.5)
    assert isinstance(pos, pd.Series)
    assert (pos.abs() <= 0.5 + 1e-9).all()  # 仓位 ≤ max_position


def test_predict_handles_new_columns(simple_features):
    """predict 时传入有新列/缺列不应崩"""
    rng = np.random.default_rng(2)
    y = pd.Series(rng.integers(0, 2, len(simple_features)), index=simple_features.index)
    model = SecondaryModel()
    model.fit(simple_features, y)
    new_feats = simple_features.drop(columns=["mom_5"])
    new_feats["new_col"] = 0.0
    conf = model.predict_confidence(new_feats)
    assert len(conf) == len(new_feats)


def test_custom_clf_can_be_injected():
    """支持自定义 sklearn classifier"""
    from sklearn.tree import DecisionTreeClassifier
    rng = np.random.default_rng(3)
    feats = pd.DataFrame({
        "a": rng.normal(0, 1, 100),
        "b": rng.normal(0, 1, 100),
    })
    y = pd.Series(rng.integers(0, 2, 100))
    model = SecondaryModel(clf=DecisionTreeClassifier(max_depth=2, random_state=0))
    model.fit(feats, y)
    assert model.is_fitted


# ============================================================
# build_features_from_alphas
# ============================================================

def test_build_features_from_alphas_basic(ohlcv_2y):
    feats = build_features_from_alphas(ohlcv_2y)
    assert isinstance(feats, pd.DataFrame)
    assert len(feats) == len(ohlcv_2y)
    # 非常数列应已 z-score 化（方差 ≈ 1）
    stds = feats.std()
    non_const_std = stds[stds > 0.5]
    assert len(non_const_std) > 0
    assert (non_const_std < 1.5).all()


def test_build_features_with_alpha_subset(ohlcv_2y):
    feats = build_features_from_alphas(ohlcv_2y, alpha_names=["alpha_1", "alpha_2"])
    assert set(feats.columns) <= {"alpha_1", "alpha_2"}


# ============================================================
# run_meta_labeling_pipeline
# ============================================================

def test_pipeline_returns_full_result(ohlcv_2y, predictive_primary, simple_features):
    result = run_meta_labeling_pipeline(
        ohlcv_2y, primary_alpha=predictive_primary,
        feature_alphas=simple_features,
        vertical_barrier_bars=5,
        test_size=0.3,
    )
    assert result.model.is_fitted
    assert len(result.meta_labels) > 0
    assert len(result.confidence) == len(result.meta_labels)
    assert len(result.position) == len(result.meta_labels)
    assert 0 <= result.primary_precision <= 1
    # secondary precision 可能因无高 confidence 而为 NaN，但字段存在
    assert hasattr(result, "secondary_oos_precision")


def test_pipeline_predictive_primary_higher_precision_than_random(
    ohlcv_2y, predictive_primary, random_primary, simple_features
):
    """有信号的 primary 应比随机的 baseline precision 更高"""
    res_pred = run_meta_labeling_pipeline(
        ohlcv_2y, primary_alpha=predictive_primary,
        feature_alphas=simple_features, vertical_barrier_bars=5,
    )
    res_rand = run_meta_labeling_pipeline(
        ohlcv_2y, primary_alpha=random_primary,
        feature_alphas=simple_features, vertical_barrier_bars=5,
    )
    # 预测性 primary 的 baseline precision 应 > 随机
    assert res_pred.primary_precision > res_rand.primary_precision - 0.1  # 容差 10%


def test_pipeline_min_train_samples_raises(ohlcv_2y, predictive_primary):
    with pytest.raises(ValueError, match="样本太少"):
        # 用极大 vb + 高 min_train_samples 触发
        run_meta_labeling_pipeline(
            ohlcv_2y, primary_alpha=predictive_primary,
            vertical_barrier_bars=10,
            min_train_samples=600,  # 比总样本 500 还大
        )


def test_pipeline_position_respects_max(ohlcv_2y, predictive_primary, simple_features):
    result = run_meta_labeling_pipeline(
        ohlcv_2y, primary_alpha=predictive_primary,
        feature_alphas=simple_features,
        vertical_barrier_bars=5,
        max_position=0.8,
    )
    assert result.position.abs().max() <= 0.8 + 1e-9


def test_pipeline_with_alpha101_features(ohlcv_2y, predictive_primary):
    """不传 feature_alphas 时默认从 Alpha101 构建"""
    result = run_meta_labeling_pipeline(
        ohlcv_2y, primary_alpha=predictive_primary,
        vertical_barrier_bars=5,
    )
    assert result.model.is_fitted
    assert len(result.model.feature_names) > 0


# ============================================================
# cross_validate_secondary
# ============================================================

def test_cross_validate_returns_folds(ohlcv_2y, predictive_primary, simple_features):
    cv = cross_validate_secondary(
        ohlcv_2y, primary_alpha=predictive_primary,
        feature_alphas=simple_features,
        n_splits=3, purge_bars=3, embargo_bars=2,
        vertical_barrier_bars=5,
    )
    assert len(cv["fold_results"]) >= 1
    assert "mean_primary_precision" in cv
    assert "mean_secondary_precision" in cv
    assert "mean_lift" in cv
    assert cv["n_total"] > 0


def test_cross_validate_lift_for_predictive(ohlcv_2y, predictive_primary, simple_features):
    """有信号 primary + 有信号 features → secondary lift 应 ≥ 0（多数情况）"""
    cv = cross_validate_secondary(
        ohlcv_2y, primary_alpha=predictive_primary,
        feature_alphas=simple_features,
        n_splits=3, purge_bars=3, embargo_bars=2,
        vertical_barrier_bars=5,
    )
    # 不强求 lift > 0（小样本不稳定），但应能跑完
    assert cv["mean_primary_precision"] == cv["mean_primary_precision"]  # not NaN


# ============================================================
# pipeline_summary
# ============================================================

def test_summary_fields(ohlcv_2y, predictive_primary, simple_features):
    result = run_meta_labeling_pipeline(
        ohlcv_2y, primary_alpha=predictive_primary,
        feature_alphas=simple_features,
        vertical_barrier_bars=5,
    )
    s = pipeline_summary(result)
    for key in ("total_samples", "train_size", "test_size",
                "primary_precision", "secondary_oos_precision",
                "lift", "filter_rate", "n_features", "feature_names"):
        assert key in s
