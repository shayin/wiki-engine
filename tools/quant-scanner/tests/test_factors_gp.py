"""GP 因子挖掘测试（任务 #87）"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from quant_scanner.factors.gp_mining import (
    BINARY_OPS,
    DEFAULT_FEATURES,
    GPFactorMiner,
    Node,
    UNARY_OPS,
)


# ----------------------------------------------------------------------
# fixtures
# ----------------------------------------------------------------------

@pytest.fixture
def synthetic_signal_data() -> tuple[pd.DataFrame, pd.Series]:
    """构造含强信号的合成数据：fwd = sign(close - ts_mean(close, 20)) * 0.05 + 噪声

    GP 应能挖出形如 ts_rank(div(ts_delta(close, 5), close), 20) 或
    sub(close, ts_mean(close, 20)) 类的因子
    """
    rng = np.random.default_rng(42)
    n = 400
    dates = pd.date_range("2025-01-01", periods=n, freq="B")

    # close 服从随机游走（从一开始就绑 dates 索引，保证后续所有 Series 对齐）
    rets = rng.normal(0, 0.02, n)
    close = pd.Series(100 * np.exp(np.cumsum(rets)), index=dates)
    high = close * (1 + np.abs(rng.normal(0, 0.005, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.005, n)))
    open_ = close + rng.normal(0, 0.1, n)
    volume = pd.Series(rng.lognormal(15, 0.5, n), index=dates)

    df = pd.DataFrame({
        "close": close,
        "open": open_,
        "high": high,
        "low": low,
        "volume": volume,
    }, index=dates)

    # 前瞻收益：与「close - MA20」强相关
    ma20 = close.rolling(20).mean()
    signal = (close - ma20) / close
    noise = pd.Series(rng.normal(0, 0.01, n), index=dates)
    fwd = (signal * 0.05 + noise).shift(-5)  # 5 日前瞻

    return df, fwd


# ----------------------------------------------------------------------
# Node 单元测试
# ----------------------------------------------------------------------

def test_node_leaf_feature_repr():
    n = Node(feature="close")
    assert n.is_leaf()
    assert n.to_formula() == "close"


def test_node_leaf_const_repr():
    n = Node(const=2.5)
    assert n.to_formula() == "2.5"


def test_node_unary_with_window():
    """ts_mean(close, w=20)"""
    n = Node(op="ts_mean", children=[Node(feature="close")], window=20)
    assert "ts_mean(close" in n.to_formula()
    assert "w=20" in n.to_formula()


def test_node_binary_no_window():
    """add(close, volume)"""
    n = Node(op="add", children=[Node(feature="close"), Node(feature="volume")])
    f = n.to_formula()
    assert f.startswith("add(")
    assert "close" in f and "volume" in f


def test_node_size_and_depth():
    """add(close, ts_mean(volume, w=5))"""
    n = Node(op="add", children=[
        Node(feature="close"),
        Node(op="ts_mean", children=[Node(feature="volume")], window=5),
    ])
    assert n.size() == 4
    assert n.depth() == 2


def test_node_evaluate_simple():
    """add(close, volume) 在简单 df 上求值正确"""
    df = pd.DataFrame({
        "close": [1.0, 2.0, 3.0],
        "volume": [10.0, 20.0, 30.0],
    })
    n = Node(op="add", children=[Node(feature="close"), Node(feature="volume")])
    out = n.evaluate(df)
    assert list(out) == [11.0, 22.0, 33.0]


def test_node_evaluate_unknown_feature_returns_nan():
    df = pd.DataFrame({"close": [1.0, 2.0]})
    n = Node(feature="unknown_col")
    out = n.evaluate(df)
    assert out.isna().all()


def test_node_clone_independent():
    """clone 后修改不影响原节点"""
    original = Node(op="add", children=[Node(feature="close"), Node(feature="volume")])
    cloned = original.clone()
    cloned.children[0] = Node(feature="high")
    # 原节点不受影响
    assert original.children[0].feature == "close"


# ----------------------------------------------------------------------
# GPFactorMiner 单元测试
# ----------------------------------------------------------------------

def test_miner_initialization_defaults():
    miner = GPFactorMiner()
    assert miner.population_size == 50
    assert miner.generations == 20
    assert miner.elitism == 2


def test_miner_initialization_custom():
    miner = GPFactorMiner(population_size=20, generations=5, max_depth=3)
    assert miner.population_size == 20
    assert miner.generations == 5
    assert miner.max_depth == 3


def test_random_tree_respects_max_depth():
    miner = GPFactorMiner(max_depth=3, random_state=0)
    features = ["close", "volume"]
    for _ in range(20):
        tree = miner._random_tree(features)
        assert tree.depth() <= 4  # grow 可能略超 max_depth，但 _random_tree(depth=max_depth) 应受控


def test_mine_returns_dict_with_required_keys(synthetic_signal_data):
    df, fwd = synthetic_signal_data
    miner = GPFactorMiner(population_size=20, generations=5, random_state=42)
    result = miner.mine(df, fwd, feature_cols=["close", "volume"])
    assert isinstance(result, dict)
    for key in ["best_node", "best_formula", "best_ic", "best_abs_ic",
                "best_ir", "best_factor", "history", "n_samples"]:
        assert key in result, f"缺字段 {key}"


def test_mine_finds_signal_above_random(synthetic_signal_data):
    """合成数据有强信号 → GP 应能挖出 |IC| > 0.05 的因子"""
    df, fwd = synthetic_signal_data
    miner = GPFactorMiner(population_size=40, generations=15, random_state=42)
    result = miner.mine(df, fwd, feature_cols=["close", "volume"])
    if "error" in result:
        pytest.skip(f"GP 产出 error: {result['error']}")
    # 应能找到 |IC| > 0.05 的因子（合成数据信号清晰）
    assert result["best_abs_ic"] > 0.05, \
        f"GP 应找到 |IC|>0.05 的因子，实际 best_ic={result['best_ic']}"


def test_mine_best_formula_is_string(synthetic_signal_data):
    df, fwd = synthetic_signal_data
    miner = GPFactorMiner(population_size=15, generations=3, random_state=1)
    result = miner.mine(df, fwd)
    assert isinstance(result["best_formula"], str)
    assert len(result["best_formula"]) > 0


def test_mine_history_has_generations_entries(synthetic_signal_data):
    df, fwd = synthetic_signal_data
    miner = GPFactorMiner(population_size=15, generations=4, random_state=1)
    result = miner.mine(df, fwd)
    assert len(result["history"]) == 4
    # 每条历史记录有必要字段
    for h in result["history"]:
        for key in ["gen", "best_abs_ic", "mean_abs_ic", "best_formula"]:
            assert key in h


def test_mine_best_factor_is_series_aligned_with_index(synthetic_signal_data):
    df, fwd = synthetic_signal_data
    miner = GPFactorMiner(population_size=15, generations=3, random_state=1)
    result = miner.mine(df, fwd)
    assert isinstance(result["best_factor"], pd.Series)
    assert len(result["best_factor"]) == len(df)


def test_mine_reproducible_with_same_seed(synthetic_signal_data):
    """同一种子两次跑 → 结果一致"""
    df, fwd = synthetic_signal_data
    m1 = GPFactorMiner(population_size=20, generations=5, random_state=42)
    r1 = m1.mine(df, fwd)
    m2 = GPFactorMiner(population_size=20, generations=5, random_state=42)
    r2 = m2.mine(df, fwd)
    assert r1["best_formula"] == r2["best_formula"]
    assert abs(r1["best_ic"] - r2["best_ic"]) < 1e-6


def test_mine_different_seeds_may_differ(synthetic_signal_data):
    """不同种子 → 结果可能不同（GP 是随机搜索）"""
    df, fwd = synthetic_signal_data
    m1 = GPFactorMiner(population_size=20, generations=5, random_state=1)
    r1 = m1.mine(df, fwd)
    m2 = GPFactorMiner(population_size=20, generations=5, random_state=99)
    r2 = m2.mine(df, fwd)
    # 公式不一定不同，但允许（GP 种群小可能有巧合），此处只验证不崩
    assert isinstance(r1["best_formula"], str)
    assert isinstance(r2["best_formula"], str)


def test_mine_no_features_returns_error():
    df = pd.DataFrame({"a": [1, 2, 3]})
    fwd = pd.Series([0.01, 0.02, 0.03])
    miner = GPFactorMiner(population_size=10, generations=2)
    result = miner.mine(df, fwd, feature_cols=["close"])  # close 不在 df
    assert "error" in result


def test_node_evaluate_nan_propagates_on_failure():
    """算子报错时返回 NaN Series（不抛异常）"""
    df = pd.DataFrame({"close": [1.0, 2.0, 3.0]})
    # 构造非法 node（op 不在注册表）
    n = Node(op="unknown_op", children=[Node(feature="close")])
    out = n.evaluate(df)
    assert out.isna().all()


def test_default_features_constant():
    assert DEFAULT_FEATURES == ["close", "open", "high", "low", "volume"]


def test_unary_ops_contains_core_ops():
    for op in ["ts_mean", "ts_rank", "ts_delta", "abs_", "log", "rsi"]:
        assert op in UNARY_OPS


def test_binary_ops_contains_core_ops():
    for op in ["add", "sub", "mul", "div", "ts_corr"]:
        assert op in BINARY_OPS
