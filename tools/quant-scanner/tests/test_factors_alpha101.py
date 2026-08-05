"""WorldQuant 101 Alpha 测试（完整 1-101）

覆盖：
- Alpha101.compute_all 不抛异常
- 每个 alpha 返回 pd.Series 长度 = 输入
- alpha_14 关键 bug 已修复（不再 AttributeError）
- alpha_7 不返回全 NaN
- IC 评估接口（smoke）
- alpha_31-101 机械化移植 smoke 测试
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quant_scanner.factors.alpha101 import (
    Alpha101, alpha_1, alpha_14, alpha_7, alpha_9,
    alpha_21, alpha_22, alpha_23, alpha_24, alpha_25,
    alpha_26, alpha_27, alpha_28, alpha_29, alpha_30,
    alpha_31, alpha_32, alpha_33, alpha_34, alpha_35,
    alpha_36, alpha_37, alpha_38, alpha_39, alpha_40,
    alpha_41, alpha_42, alpha_43, alpha_44, alpha_45,
    alpha_46, alpha_47, alpha_48, alpha_49, alpha_50,
    alpha_51, alpha_52, alpha_53, alpha_54, alpha_55,
    alpha_56, alpha_57, alpha_58, alpha_59, alpha_60,
    alpha_61, alpha_62, alpha_63, alpha_64, alpha_65,
    alpha_66, alpha_67, alpha_68, alpha_69, alpha_70,
    alpha_71, alpha_72, alpha_73, alpha_74, alpha_75,
    alpha_76, alpha_77, alpha_78, alpha_79, alpha_80,
    alpha_81, alpha_82, alpha_83, alpha_84, alpha_85,
    alpha_86, alpha_87, alpha_88, alpha_89, alpha_90,
    alpha_91, alpha_92, alpha_93, alpha_94, alpha_95,
    alpha_96, alpha_97, alpha_98, alpha_99, alpha_100,
    alpha_101,
    _signed_power, _adv,
)


@pytest.fixture
def ohlcv_300() -> pd.DataFrame:
    """300 日合成 OHLCV（带趋势+波动+成交量）"""
    rng = np.random.default_rng(42)
    n = 300
    rets = rng.normal(0.0005, 0.015, n)
    close = 100 * np.exp(np.cumsum(rets))
    high = close * (1 + rng.uniform(0, 0.008, n))
    low = close * (1 - rng.uniform(0, 0.008, n))
    op = close * (1 + rng.normal(0, 0.003, n))
    vol = rng.integers(1_000_000, 20_000_000, n).astype(float)
    dates = pd.date_range("2024-01-01", periods=n, freq="B")
    return pd.DataFrame({
        "open": op, "high": high, "low": low, "close": close, "volume": vol,
    }, index=dates)


# ============================================================
# 辅助函数
# ============================================================

def test_signed_power():
    s = pd.Series([-2.0, 0.0, 3.0])
    r = _signed_power(s, 2.0)
    assert r.iloc[0] == pytest.approx(-4.0)
    assert r.iloc[1] == 0.0
    assert r.iloc[2] == pytest.approx(9.0)


def test_adv_window():
    vol = pd.Series([10.0] * 30)
    a = _adv(vol, 20)
    assert a.iloc[-1] == 10.0


# ============================================================
# 关键 bug 回归
# ============================================================

def test_alpha_14_no_attribute_error(ohlcv_300):
    """alpha_14 历史 bug：(-1.0).cond... 抛 AttributeError，已修复"""
    r = alpha_14(ohlcv_300)
    assert isinstance(r, pd.Series)
    assert len(r) == len(ohlcv_300)


def test_alpha_7_not_all_nan(ohlcv_300):
    """alpha_7 不应全 NaN（虽然 adv20 头部会有 NaN，但中后段应有值）"""
    r = alpha_7(ohlcv_300)
    assert r.iloc[50:].notna().any()


def test_alpha_9_no_dead_code(ohlcv_300):
    """alpha_9 简化后仍返回有效序列"""
    r = alpha_9(ohlcv_300)
    assert isinstance(r, pd.Series)
    assert len(r) == len(ohlcv_300)


# ============================================================
# 批量计算
# ============================================================

def test_alpha101_compute_all_no_crash(ohlcv_300):
    """compute_all 必须不抛异常（即使单个 alpha 失败也降级为 NaN Series）"""
    alpha = Alpha101()
    results = alpha.compute_all(ohlcv_300)
    assert len(results) == 82
    for name, series in results.items():
        assert isinstance(series, pd.Series)
        assert len(series) == len(ohlcv_300)


def test_alpha101_list_names():
    alpha = Alpha101()
    names = alpha.list_alpha_names()
    assert len(names) == 82
    assert "alpha_1" in names
    assert "alpha_30" in names
    assert "alpha_101" in names


def test_alpha101_compute_single(ohlcv_300):
    alpha = Alpha101()
    r = alpha.compute(ohlcv_300, "alpha_5")
    assert isinstance(r, pd.Series)
    assert len(r) == len(ohlcv_300)


def test_alpha101_compute_unknown_raises(ohlcv_300):
    alpha = Alpha101()
    with pytest.raises(KeyError):
        alpha.compute(ohlcv_300, "alpha_999")


def test_alpha101_each_alpha_has_values(ohlcv_300):
    """每个 alpha 至少有一些非 NaN 值（避免全 NaN 占位）"""
    alpha = Alpha101()
    results = alpha.compute_all(ohlcv_300)
    for name, series in results.items():
        non_na = series.dropna()
        assert len(non_na) > 0, f"{name} 全 NaN"


# ============================================================
# IC smoke（不要求具体数值，只要求流程可跑）
# ============================================================

def test_alpha101_ic_evaluation_smoke(ohlcv_300):
    """用 5 日前瞻收益跑 IC：至少有一些 alpha |IC| > 0.02"""
    from quant_scanner.factors.operators import factor_ic, log_returns

    fwd = log_returns(ohlcv_300["close"]).shift(-5)
    alpha = Alpha101()
    results = alpha.compute_all(ohlcv_300)
    ics = {name: factor_ic(s, fwd) for name, s in results.items()}
    valid_ics = [ic for ic in ics.values() if not np.isnan(ic)]
    assert len(valid_ics) > 0
    # 至少 1 个 alpha 有非平凡预测力（绝对值 > 0.02）
    assert any(abs(ic) > 0.02 for ic in valid_ics)


# ============================================================
# alpha_21-30（任务 #89 第一批）
# ============================================================

def test_alpha101_implemented_count():
    """IMPLEMENTED 列表应含 82 个 alpha（101 - 19 IndNeutralize/cap 跳过）"""
    assert len(Alpha101.IMPLEMENTED) == 82
    assert 1 in Alpha101.IMPLEMENTED
    assert 101 in Alpha101.IMPLEMENTED
    # 跳过的 alpha 不在 IMPLEMENTED
    for skipped in Alpha101.SKIPPED:
        assert skipped not in Alpha101.IMPLEMENTED


@pytest.mark.parametrize("alpha_fn", [
    alpha_21, alpha_22, alpha_23, alpha_24, alpha_25,
    alpha_26, alpha_27, alpha_28, alpha_29, alpha_30,
])
def test_alpha_21_30_returns_series(ohlcv_300, alpha_fn):
    """每个 alpha 返回 pd.Series，长度与输入一致"""
    out = alpha_fn(ohlcv_300)
    assert isinstance(out, pd.Series)
    assert len(out) == len(ohlcv_300)


@pytest.mark.parametrize("alpha_fn", [
    alpha_21, alpha_22, alpha_24, alpha_26, alpha_27, alpha_28, alpha_29, alpha_30,
])
def test_alpha_21_30_has_values(ohlcv_300, alpha_fn):
    """alpha_21-30（除需要 120 日窗口的 alpha_25 和 250 日窗口的 alpha_23）应非全 NaN"""
    out = alpha_fn(ohlcv_300).dropna()
    assert len(out) > 0, f"{alpha_fn.__name__} 全 NaN"


def test_alpha_21_directional_meaning(ohlcv_300):
    """alpha_21 = sma(((close-low)-(high-close))/(high-low), 2)
    close == high 时应接近 +1（偏高点），close == low 时应接近 -1（偏低点）"""
    # 构造 close = high 的样本（强势收高）
    df_bull = ohlcv_300.copy()
    df_bull["close"] = df_bull["high"]
    out = alpha_21(df_bull).dropna()
    # 应当 ≥ 0（close=high 时 ((c-l)-(h-c))/(h-l) = (h-l-0)/(h-l) = 1.0）
    assert (out >= -0.01).mean() > 0.95, "alpha_21 在 close=high 时应 ≥ 0"


def test_alpha_27_conditional_direction(ohlcv_300):
    """alpha_27: 多数收涨时 direction=-1，否则 direction=+1"""
    out = alpha_27(ohlcv_300).dropna()
    # 数据本身 rets 均值 0.0005（小幅上涨），所以多数收涨，direction 应多为 -1
    # 输出乘以 rank(-1 * delta(close, 10))，方向可能变，至少不崩
    assert len(out) > 0


def test_alpha_23_long_window_returns_few_on_short_data():
    """alpha_23 用 250 日窗口，短数据下早期值会因窗口未满而失真但仍可算（min_periods=1）

    本测试只验证短数据不崩 + 输出长度合理。
    """
    n = 100
    dates = pd.date_range("2025-01-01", periods=n, freq="B")
    rng = np.random.default_rng(0)
    df = pd.DataFrame({
        "open": np.linspace(100, 110, n),
        "high": np.linspace(101, 111, n),
        "low": np.linspace(99, 109, n),
        "close": np.linspace(100, 110, n) + rng.normal(0, 0.5, n),
        "volume": rng.integers(1e6, 1e7, n).astype(float),
    }, index=dates)
    out = alpha_23(df).dropna()
    # 不崩 + 至少 1 个非 NaN（delta+delay 后减 1 个）
    assert len(out) > 0
    assert len(out) <= n


# ============================================================
# alpha_31-101（任务 #89 完整版：论文 1-101 全覆盖）
# ============================================================

# 需要长窗口（>=250 日）的 alpha：短数据下早期 NaN 正常
LONG_WINDOW_ALPHAS = {
    alpha_40, alpha_48, alpha_52, alpha_58, alpha_59,
    alpha_63, alpha_67, alpha_69, alpha_70, alpha_76,
    alpha_79, alpha_80, alpha_82, alpha_87, alpha_89,
    alpha_90, alpha_91, alpha_93, alpha_97, alpha_100,
}

# IndNeutralize/cap 跳过的 alpha（返回全 NaN）
SKIPPED_ALPHAS = {
    alpha_48, alpha_56, alpha_58, alpha_59, alpha_63,
    alpha_67, alpha_69, alpha_70, alpha_76, alpha_79,
    alpha_80, alpha_82, alpha_87, alpha_89, alpha_90,
    alpha_91, alpha_93, alpha_97, alpha_100,
}

ALPHA_31_101 = [
    alpha_31, alpha_32, alpha_33, alpha_34, alpha_35,
    alpha_36, alpha_37, alpha_38, alpha_39, alpha_40,
    alpha_41, alpha_42, alpha_43, alpha_44, alpha_45,
    alpha_46, alpha_47, alpha_48, alpha_49, alpha_50,
    alpha_51, alpha_52, alpha_53, alpha_54, alpha_55,
    alpha_56, alpha_57, alpha_58, alpha_59, alpha_60,
    alpha_61, alpha_62, alpha_63, alpha_64, alpha_65,
    alpha_66, alpha_67, alpha_68, alpha_69, alpha_70,
    alpha_71, alpha_72, alpha_73, alpha_74, alpha_75,
    alpha_76, alpha_77, alpha_78, alpha_79, alpha_80,
    alpha_81, alpha_82, alpha_83, alpha_84, alpha_85,
    alpha_86, alpha_87, alpha_88, alpha_89, alpha_90,
    alpha_91, alpha_92, alpha_93, alpha_94, alpha_95,
    alpha_96, alpha_97, alpha_98, alpha_99, alpha_100,
    alpha_101,
]


@pytest.fixture
def ohlcv_500() -> pd.DataFrame:
    """500 日合成 OHLCV（覆盖 alpha_40/52 等长窗口需求）"""
    rng = np.random.default_rng(42)
    n = 500
    rets = rng.normal(0.0005, 0.015, n)
    close = 100 * np.exp(np.cumsum(rets))
    high = close * (1 + rng.uniform(0, 0.008, n))
    low = close * (1 - rng.uniform(0, 0.008, n))
    op = close * (1 + rng.normal(0, 0.003, n))
    vol = rng.integers(1_000_000, 20_000_000, n).astype(float)
    dates = pd.date_range("2024-01-01", periods=n, freq="B")
    return pd.DataFrame({
        "open": op, "high": high, "low": low, "close": close, "volume": vol,
    }, index=dates)


@pytest.mark.parametrize("alpha_fn", ALPHA_31_101)
def test_alpha_31_101_returns_series(ohlcv_500, alpha_fn):
    """每个 alpha 返回 pd.Series，长度与输入一致，不抛异常"""
    out = alpha_fn(ohlcv_500)
    assert isinstance(out, pd.Series), f"{alpha_fn.__name__} 未返回 pd.Series"
    assert len(out) == len(ohlcv_500)


@pytest.mark.parametrize("alpha_fn", ALPHA_31_101)
def test_alpha_31_101_skipped_is_all_nan(ohlcv_500, alpha_fn):
    """跳过的 alpha（IndNeutralize/cap）应返回全 NaN Series"""
    if alpha_fn not in SKIPPED_ALPHAS:
        pytest.skip(f"{alpha_fn.__name__} 非跳过的 alpha")
    out = alpha_fn(ohlcv_500)
    assert out.isna().all(), f"{alpha_fn.__name__} 应返回全 NaN（跳过）"


@pytest.mark.parametrize("alpha_fn", ALPHA_31_101)
def test_alpha_31_101_non_skipped_has_values(ohlcv_500, alpha_fn):
    """非跳过的 alpha 在 500 日样本下应至少有 1 个非 NaN 值"""
    if alpha_fn in SKIPPED_ALPHAS:
        pytest.skip(f"{alpha_fn.__name__} 是跳过的 alpha")
    out = alpha_fn(ohlcv_500).dropna()
    assert len(out) > 0, f"{alpha_fn.__name__} 全 NaN（500 日样本）"


def test_alpha_101_simple_formula():
    """alpha_101 = (close-open)/((high-low)+0.001)
    手工验证：close=10, open=9, high=11, low=8 → (1)/(3.001) ≈ 0.3332"""
    df = pd.DataFrame({
        "open": [9.0], "high": [11.0], "low": [8.0], "close": [10.0],
        "volume": [1e6],
    })
    out = alpha_101(df)
    assert out.iloc[0] == pytest.approx(1.0 / 3.001, rel=1e-4)


def test_alpha101_compute_all_full_coverage(ohlcv_500):
    """compute_all 必须覆盖所有已实现的 alpha（82 个）"""
    alpha = Alpha101()
    results = alpha.compute_all(ohlcv_500)
    assert len(results) == 82
    for name, series in results.items():
        assert isinstance(series, pd.Series)
        assert len(series) == len(ohlcv_500)
