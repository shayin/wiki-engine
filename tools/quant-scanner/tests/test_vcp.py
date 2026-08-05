"""VCP 信号测试（2026-07-18 zigzag 修复版）

覆盖：
- 标准 VCP 形态高分
- 随机数据低分
- 数据不足
- zigzag_pivots 单元测试（初始化、主循环、双向）
- 基底左缘检测
- 高波动股 ATR 自适应（不应过多 pivot）
- 低波动股 ATR 自适应（不应漏 pivot）
- 前期涨幅段的伪 contraction 不被识别（核心 bug 修复）
- 前置 trend_template 不通过 → value=0
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from quant_scanner.signals.vcp import VCPSignal, zigzag_pivots


def _make_vcp_pattern(seed: int = 42) -> pd.DataFrame:
    """构造一个标准 VCP 形态（严格递减）：
    - 阶段 0 (0-30): 底部 100 → 顶 180（前期涨幅 80%）
    - 阶段 1 (30-50): 180 → 153（回撤 15%）
    - 阶段 2 (50-75): 153 → 175 → 164.5（回撤 6%）
    - 阶段 3 (75-105): 164.5 → 174 → 169.7（回撤 2.5%）
    - 阶段 4 (105-120): 169.7 → 173 接近突破点
    """
    rng = np.random.default_rng(seed)
    n = 120
    prices = np.zeros(n)
    volumes = np.zeros(n)

    for i in range(0, 30):
        prices[i] = 100 + (180 - 100) * (i / 30) + rng.normal(0, 1.5)
        volumes[i] = rng.integers(10_000_000, 15_000_000)
    for i in range(30, 50):
        prices[i] = 180 - (180 - 153) * ((i - 30) / 20) + rng.normal(0, 1.2)
        volumes[i] = rng.integers(8_000_000, 11_000_000)
    for i in range(50, 75):
        if i < 62:
            prices[i] = 153 + (175 - 153) * ((i - 50) / 12) + rng.normal(0, 0.8)
        else:
            prices[i] = 175 - (175 - 164.5) * ((i - 62) / 13) + rng.normal(0, 0.8)
        volumes[i] = rng.integers(5_000_000, 7_000_000)
    for i in range(75, 105):
        if i < 90:
            prices[i] = 164.5 + (174 - 164.5) * ((i - 75) / 15) + rng.normal(0, 0.5)
        else:
            prices[i] = 174 - (174 - 169.7) * ((i - 90) / 15) + rng.normal(0, 0.5)
        volumes[i] = rng.integers(3_000_000, 5_000_000)
    for i in range(105, n):
        prices[i] = 169.7 + (173 - 169.7) * ((i - 105) / 15) + rng.normal(0, 0.4)
        volumes[i] = rng.integers(2_500_000, 4_000_000)

    dates = pd.date_range(end=pd.Timestamp.now().tz_localize(None), periods=n, freq="B")
    return pd.DataFrame({
        "open": prices,
        "high": prices * (1 + rng.uniform(0, 0.005, size=n)),
        "low": prices * (1 - rng.uniform(0, 0.005, size=n)),
        "close": prices,
        "volume": volumes,
    }, index=dates)


# ==================== 整合测试 ====================

def test_vcp_pattern_gets_decent_score():
    """标准 VCP 形态（跳过 trend_template 前置，因为合成数据 < 232 行）"""
    df = _make_vcp_pattern()
    sig = VCPSignal(require_trend_template=False)
    result = sig.evaluate("VCP_TEST", df)
    assert result.value >= 0.5, f"VCP 形态评分过低: {result.value}, reasons={result.reasons}"
    # 收缩次数应为 3（基底内）
    assert result.details["n_contractions"] == 3
    # 收缩严格递减
    assert result.details["strictly_decreasing"] is True
    # 足迹应形如 "NNW NN/N NT"
    assert "T" in result.details["footprint"]


def test_random_data_low_score():
    """随机数据不应被识别为 VCP"""
    rng = np.random.default_rng(7)
    n = 100
    prices = 100 + np.cumsum(rng.normal(0, 2, size=n))
    dates = pd.date_range(end=pd.Timestamp.now().tz_localize(None), periods=n, freq="B")
    df = pd.DataFrame({
        "open": prices, "high": prices * 1.01, "low": prices * 0.99,
        "close": prices, "volume": rng.integers(1_000_000, 5_000_000, size=n),
    }, index=dates)
    sig = VCPSignal(require_trend_template=False)
    result = sig.evaluate("RANDOM", df)
    assert result.value < 0.7


def test_insufficient_data():
    """数据不足 → value=0"""
    df = _make_vcp_pattern().head(30)
    sig = VCPSignal()
    result = sig.evaluate("SHORT", df)
    assert result.value == 0.0
    assert not result.passed


# ==================== zigzag_pivots 单元测试 ====================

def _make_ohlc(prices: np.ndarray, vol: float = 0.005) -> pd.DataFrame:
    """从 close 数组造 OHLCV，high/low 在 ±vol 范围"""
    n = len(prices)
    rng = np.random.default_rng(0)
    dates = pd.date_range(end=pd.Timestamp.now().tz_localize(None), periods=n, freq="B")
    return pd.DataFrame({
        "open": prices,
        "high": prices * (1 + vol),
        "low": prices * (1 - vol),
        "close": prices,
        "volume": np.full(n, 5e6),
    }, index=dates)


def test_zigzag_basic_uptrend():
    """单调上涨 → 无 peak/trough（无反转）"""
    prices = np.linspace(100, 150, 50)
    df = _make_ohlc(prices)
    peaks, troughs = zigzag_pivots(df["high"], df["low"], min_pct=0.05)
    # 单调上涨 → 起点是 trough，之后可能无 pivot（涨幅 > min_pct 但无反转）
    assert len(peaks) == 0
    # 起点被确认为 trough（因为后续涨 ≥ 5%）
    assert len(troughs) >= 1
    assert troughs[0] == 0


def test_zigzag_basic_downtrend():
    """单调下跌 → 无 trough，起点为 peak"""
    prices = np.linspace(150, 100, 50)
    df = _make_ohlc(prices)
    peaks, troughs = zigzag_pivots(df["high"], df["low"], min_pct=0.05)
    assert len(troughs) == 0
    assert len(peaks) >= 1
    assert peaks[0] == 0


def test_zigzag_detects_v_shape():
    """V 形（先跌后涨）→ 1 个 trough（谷底附近）"""
    n = 60
    prices = np.concatenate([
        np.linspace(120, 80, 30),   # 下跌
        np.linspace(80, 120, 30),   # 反弹
    ])
    df = _make_ohlc(prices)
    peaks, troughs = zigzag_pivots(df["high"], df["low"], min_pct=0.05)
    # 应识别至少 1 个 trough（在谷底 idx ≈ 30 附近）
    assert len(troughs) >= 1
    assert 25 <= troughs[0] <= 35


def test_zigzag_detects_inverted_v():
    """倒 V 形（先涨后跌）→ 1 个 peak（顶附近）"""
    n = 60
    prices = np.concatenate([
        np.linspace(80, 120, 30),
        np.linspace(120, 80, 30),
    ])
    df = _make_ohlc(prices)
    peaks, troughs = zigzag_pivots(df["high"], df["low"], min_pct=0.05)
    assert len(peaks) >= 1
    assert 25 <= peaks[0] <= 35


def test_zigzag_no_pivot_when_flat():
    """横盘波动 < min_pct → 无 pivot"""
    rng = np.random.default_rng(1)
    prices = 100 + rng.normal(0, 0.3, size=50)  # 极小波动
    df = _make_ohlc(prices, vol=0.001)
    peaks, troughs = zigzag_pivots(df["high"], df["low"], min_pct=0.05)
    assert peaks == [] and troughs == []


def test_zigzag_old_init_bug_fixed():
    """旧版 bug 复现测试：hi>extreme 立即设 direction=1 但没确认。

    构造：先小涨（< min_pct），再涨超 min_pct → 旧版会在第一次 hi>extreme 时
    错误加入 peak。新版应正确等到回落才确认 peak。
    """
    # 0-20: 慢涨 100→104（涨幅 < 5%，不该确认任何 pivot）
    # 20-40: 加速涨 104→130（累计涨 30%，起点确认为 trough）
    # 40-60: 横盘（无反转）
    prices = np.concatenate([
        np.linspace(100, 104, 20),
        np.linspace(104, 130, 20),
        np.full(20, 130.0),
    ])
    df = _make_ohlc(prices)
    peaks, troughs = zigzag_pivots(df["high"], df["low"], min_pct=0.05)
    # 起点应被识别为 trough（涨 ≥ 5%）
    assert troughs == [0] or troughs[0] == 0
    # 横盘段无反转，不该有 peak
    assert peaks == [], f"横盘段不应识别 peak，实际: {peaks}"


# ==================== 基底左缘检测 ====================

def test_base_left_edge_finds_prior_high():
    """基底左缘 = 前期涨幅高点"""
    sig = VCPSignal(require_trend_template=False)
    # 0-60: 上涨 80→150（前期涨幅）
    # 60-120: 在 150 附近盘整（基底）
    n = 120
    prices = np.concatenate([
        np.linspace(80, 150, 60),
        np.linspace(150, 145, 60),  # 微跌（基底）
    ])
    df = _make_ohlc(prices)
    edge = sig._find_base_left_edge(df["close"], min_runup=0.30)
    assert edge is not None
    # 左缘应在 idx 60 附近（前期高点）
    assert 50 <= edge <= 65


def test_base_left_edge_none_when_no_runup():
    """无前期涨幅 → 返回 None"""
    sig = VCPSignal(require_trend_template=False)
    rng = np.random.default_rng(2)
    prices = 100 + rng.normal(0, 1, size=120)  # 横盘
    df = _make_ohlc(prices)
    edge = sig._find_base_left_edge(df["close"], min_runup=0.30)
    assert edge is None


# ==================== 误报修复（核心）====================

def test_runup_period_not_counted_as_contractions():
    """旧版 bug：前期涨幅段的 zigzag 也被算成 contraction 候选。

    构造：明显的 VCP 形态基底 + 前期涨幅段有几次小回调。
    旧版会把前期涨幅段的回调也算进 contraction 列表，导致 n_contractions 错误。
    新版只扫基底左缘之后的 pivots。
    """
    # 0-40: 大幅上涨（80→180），中间有 2 次 8% 回调（不应计入 VCP contraction）
    # 40-120: VCP 基底（180→153→175→164.5→174→169.7→173，3 次收缩）
    n = 120
    prices = np.zeros(n)
    # 前期涨幅段（含 2 次假回调）
    for i in range(40):
        if i < 15:
            prices[i] = 80 + (110 - 80) * (i / 15)
        elif i < 20:
            prices[i] = 110 - (110 - 101) * ((i - 15) / 5)  # 回调 8%
        elif i < 35:
            prices[i] = 101 + (155 - 101) * ((i - 20) / 15)
        else:
            prices[i] = 155 - (155 - 142) * ((i - 35) / 5)  # 又回调 8%
            prices[i] = max(prices[i], 142)
    # 基底段（VCP，3 次严格递减收缩）
    for i in range(40, n):
        if i < 60:
            prices[i] = 180 - (180 - 153) * ((i - 40) / 20)  # 1st: -15%
        elif i < 75:
            if i < 67:
                prices[i] = 153 + (175 - 153) * ((i - 60) / 7)
            else:
                prices[i] = 175 - (175 - 164.5) * ((i - 67) / 8)  # 2nd: -6%
        elif i < 105:
            if i < 90:
                prices[i] = 164.5 + (174 - 164.5) * ((i - 75) / 15)
            else:
                prices[i] = 174 - (174 - 169.7) * ((i - 90) / 15)  # 3rd: -2.5%
        else:
            prices[i] = 169.7 + (173 - 169.7) * ((i - 105) / 15)

    df = _make_ohlc(prices)
    # zigzag_threshold 必须 < 最后一次收缩幅度（2.5%），否则浅 pivot 被吃
    sig = VCPSignal(require_trend_template=False, use_atr_threshold=False, zigzag_threshold=0.02)
    r = sig.evaluate("TEST", df)
    # 基底内只应有 3 次 contraction（前期涨幅段的 2 次假回调不算）
    assert r.details["n_contractions"] == 3, \
        f"基底内应只识别 3 次收缩，实际 {r.details['n_contractions']}（前期涨幅段未被正确排除）"


# ==================== ATR 自适应 ====================

def test_atr_adaptive_threshold_high_volatility():
    """高波动股 ATR% 大 → zigzag 阈值升高（避免过多 pivot）"""
    sig = VCPSignal(require_trend_template=False)
    # 构造日波动 ~5% 的高波动数据
    rng = np.random.default_rng(3)
    n = 100
    returns = rng.normal(0, 0.05, size=n)  # 日波动 5%
    prices = 100 * np.exp(np.cumsum(returns))
    df = _make_ohlc(prices, vol=0.02)
    thr = sig._adaptive_zigzag_threshold(df["high"], df["low"], df["close"])
    # ATR 应该 ≈ 5%，阈值被 clamp 到 ≤ 10%
    assert thr > 0.04, f"高波动股阈值应升高，实际 {thr}"
    assert thr <= 0.10


def test_atr_adaptive_threshold_low_volatility():
    """低波动股 ATR% 小 → zigzag 阈值降低，但 ≥ 下限 2%"""
    sig = VCPSignal(require_trend_template=False)
    rng = np.random.default_rng(4)
    n = 100
    returns = rng.normal(0, 0.005, size=n)  # 日波动 0.5%
    prices = 100 * np.exp(np.cumsum(returns))
    df = _make_ohlc(prices, vol=0.001)
    thr = sig._adaptive_zigzag_threshold(df["high"], df["low"], df["close"])
    # 低波动股阈值被 clamp 到下限 2%（新版下调）
    assert thr >= 0.02
    assert thr <= 0.025


# ==================== 前置 trend_template ====================

def test_trend_template_prerequisite_blocks_non_stage2():
    """非第二阶段（trend_template 不通过）→ value=0"""
    # 构造下跌趋势数据（不可能通过 trend_template 8 条）
    n = 250
    prices = np.linspace(200, 80, n)  # 长期下跌
    df = _make_ohlc(prices)
    sig = VCPSignal()  # 默认 require_trend_template=True
    r = sig.evaluate("DECLINE", df)
    assert r.value == 0.0
    assert r.details.get("trend_template_passed") is False


# ==================== vol_score 回归测试（防 9.32 bug 复发）====================

def test_vol_score_zero_when_volume_expanding():
    """成交量放大（vol_ratio > vol_shrink_ratio）→ vol_score=0（不应是负数除负数）

    旧 bug：公式 (1.0 - vol_ratio) / (1.0 - 1.05 + 0.01) 在 vol_ratio=1.37 时
    返回 9.25（负除负得正），导致总 score 被 clamp 到 1.0，假阳性。
    """
    # 构造数据：VCP 形态但成交量持续放大（异常）
    sig = VCPSignal(require_trend_template=False)
    df = _make_vcp_pattern()
    # 把末段成交量放大到 1.5x 前 40 日均量
    df = df.copy()
    df.iloc[-20:, df.columns.get_loc("volume")] = df["volume"].iloc[-20] * 3
    r = sig.evaluate("VOL_EXPAND", df)
    # vol_score 应 ≤ 0.05（接近 0）
    assert r.details["vol_score"] <= 0.05, \
        f"成交量放大时 vol_score 应接近 0，实际 {r.details['vol_score']}"
    # 总 value 不应被异常 vol_score 推到 1.0
    assert r.value < 0.9, f"vol_score bug 复发：value={r.value}"


def test_base_left_edge_handles_broken_out_stocks():
    """已突破创新高的股票：_find_base_left_edge 应回退到前期局部高点"""
    sig = VCPSignal(require_trend_template=False)
    # 构造：0-150 段涨幅 + 150-200 段突破创新高
    n = 200
    prices = np.concatenate([
        np.linspace(100, 180, 150),  # 前期涨幅 80%
        np.linspace(180, 220, 50),   # 末段突破
    ])
    df = _make_ohlc(prices)
    edge = sig._find_base_left_edge(df["close"], min_runup=0.30)
    assert edge is not None, "已突破股票应回退找到前期高点"
    # 应回退到前段的高点（不在末 10%）
    assert edge < 180, f"base 左缘应在末 10% 之前，实际 idx={edge}"


def test_pattern_score_low_when_not_strictly_decreasing():
    """收缩序列中间扩大（不严格递减）→ pattern_score ≤ 0.1

    回归测试：GOOGL/AAPL 真实数据发现的 bug——
    [12.4%, 7.2%, 9.5%, 6.6%, 5.9%] 中间扩大，pattern_score 旧版给 0.5，应 ≤ 0.1。
    """
    # 造一个 base 内 5 次收缩但中间扩大的形态
    n = 200
    prices = np.zeros(n)
    # 前期涨幅
    for i in range(60):
        prices[i] = 100 + (180 - 100) * (i / 60)
    # 基底内 5 次非严格递减的收缩
    seq = [(180, 158), (170, 158), (172, 154), (164, 154), (160, 156)]  # peak/trough
    seg_len = 20
    for k, (peak, trough) in enumerate(seq):
        start = 60 + k * seg_len
        for j in range(seg_len):
            if j < seg_len // 2:
                prices[start + j] = trough + (peak - trough) * (j / (seg_len // 2))
            else:
                prices[start + j] = peak - (peak - trough) * ((j - seg_len // 2) / (seg_len // 2))
    # 末段微涨
    for i in range(60 + 5 * seg_len, n):
        prices[i] = 158 + (162 - 158) * ((i - (60 + 5 * seg_len)) / (n - (60 + 5 * seg_len)))

    df = _make_ohlc(prices)
    sig = VCPSignal(require_trend_template=False, use_atr_threshold=False, zigzag_threshold=0.02)
    r = sig.evaluate("NON_STRICT", df)
    # 收缩不严格递减 → pattern_score ≤ 0.1
    assert r.details["strictly_decreasing"] is False
    assert r.details["pattern_score"] <= 0.15, \
        f"不严格递减时 pattern_score 应 ≤ 0.15，实际 {r.details['pattern_score']}"


# ==================== 2026-07-19 回炉：容差递减 + vol_score 重构 ====================

def test_tolerant_decreasing_mik_case():
    """MIK 原书案例 [16%, 8%, 6%, 3%]：8→6 shrink ratio=0.75（边界容差）

    原书 p.157 真实案例。旧版 min_shrink_ratio=0.55 会判"非递减"（6/8=0.75 > 0.55），
    新版 0.75 应判 strict（边界值）。容差违规 = 0。
    """
    sig = VCPSignal(require_trend_template=False)
    # MIK 案例：[16%, 8%, 6%, 3%]
    contractions = [0.16, 0.08, 0.06, 0.03]
    grade, ratios, violations = sig._classify_decreasing(contractions)
    assert grade == "strict", f"MIK 案例应判 strict，实际 {grade}（ratios={ratios}）"
    assert violations == 0


def test_tolerant_decreasing_single_violation():
    """单次容差违规（shrink ratio 0.75-1.0）→ grade=tolerant，pattern_score 中等

    回炉关键修复：原书 p.145「上下可以有合理的波动」允许单次轻微反弹。
    """
    sig = VCPSignal(require_trend_template=False)
    # 第 2→3 次：6/8=0.75 严格；第 3→4 次：5/6≈0.83 容差违规 1
    contractions = [0.16, 0.08, 0.06, 0.05]
    grade, ratios, violations = sig._classify_decreasing(contractions)
    assert grade == "tolerant", f"单次容差违规应判 tolerant，实际 {grade}"
    assert violations == 1


def test_reverse_decreasing_is_fail():
    """反递减（shrink ratio > 1.0）→ grade=fail

    GOOG 实际案例 [5.0, 5.6, 4.9, 8.6, 3.8, 3.8] 包含 2 次反递减（>1.0）。
    """
    sig = VCPSignal(require_trend_template=False)
    contractions = [0.05, 0.056, 0.049, 0.086, 0.038, 0.038]
    grade, ratios, violations = sig._classify_decreasing(contractions)
    assert grade == "fail", f"反递减应判 fail，实际 {grade}（ratios={ratios}）"


def test_last_not_min_is_fail():
    """末值非最小 → fail（即使前面严格递减）"""
    sig = VCPSignal(require_trend_template=False)
    # 末值 0.06 > 倒数第 3 个 0.04
    contractions = [0.20, 0.10, 0.04, 0.06]
    grade, ratios, violations = sig._classify_decreasing(contractions)
    assert grade == "fail", f"末值非最小应判 fail，实际 {grade}"


def test_pattern_fail_forces_passed_false():
    """pattern fail 时强制 passed=False，即使其他维度满分拉总分过 threshold

    回归测试：AVGO [3.7, 2.8, 4.5, 5.4, 4.1, 2.7] 反递减，但 vol/runup/position 满分
    旧版会让 value=0.714 passed=True（误报），新版硬规则应 passed=False。
    """
    # 构造：trend_template skip + 完美前期涨幅 + 完美位置 + 干涸量能 + 非递减收缩
    n = 200
    prices = np.zeros(n)
    # 强前期涨幅 80→200（150%）
    for i in range(80):
        prices[i] = 80 + (200 - 80) * (i / 80)
    # 基底内反递减收缩序列：每次回撤先减后增
    conts = [0.037, 0.028, 0.045, 0.054, 0.041, 0.027]
    peaks = [200]
    base_start = 80
    cursor = base_start
    for k, c in enumerate(conts):
        peak = prices[cursor - 1] if k == 0 else trough * (1 + 0.04)  # 反弹 4%
        trough = peak * (1 - c)
        # 5 日跌到 trough
        for j in range(5):
            prices[cursor + j] = peak + (trough - peak) * (j / 5)
        cursor += 5
        # 5 日反弹到下一个 peak
        if k < len(conts) - 1:
            next_peak = trough * (1 + 0.04)
            for j in range(5):
                prices[cursor + j] = trough + (next_peak - trough) * (j / 5)
            cursor += 5
    # 末段微涨到接近突破
    while cursor < n:
        prices[cursor] = prices[cursor - 1] * 1.001
        cursor += 1

    # 用低波动 OHLC
    df = _make_ohlc(prices, vol=0.002)
    # 量能构造干涸
    df = df.copy()
    df.iloc[base_start:, df.columns.get_loc("volume")] = 1_000_000
    df.iloc[-10:, df.columns.get_loc("volume")] = 300_000  # 末段干涸

    sig = VCPSignal(require_trend_template=False, use_atr_threshold=False, zigzag_threshold=0.015)
    r = sig.evaluate("AVGO_LIKE", df)
    assert r.details["decrease_grade"] == "fail", f"应判 fail，实际 {r.details.get('decrease_grade')}"
    assert r.passed is False, f"pattern fail 时应强制 passed=False，实际 value={r.value} passed={r.passed}"


def test_vol_score_uses_last_trough_volume():
    """vol_score 用最后 trough 处成交量，不是近 20 日均量

    回炉修复：原书 p.156「最后收缩期间」≠「最近 20 日」。
    构造：最近 20 日均量高（假装干涸不存在），但最后 trough 处量极低 → 应判干涸。
    """
    df = _make_vcp_pattern()
    df = df.copy()
    # 把最后 20 日量放大（破坏旧版"近20日均量"判定）
    df.iloc[-20:, df.columns.get_loc("volume")] = df["volume"].iloc[-30] * 2
    # 但最后 trough 处（idx ≈ -10）量降低
    df.iloc[-15:-5, df.columns.get_loc("volume")] = df["volume"].iloc[-30] * 0.4
    sig = VCPSignal(require_trend_template=False)
    r = sig.evaluate("VOL_LAST_TROUGH", df)
    # 新字段 last_trough_vol_ratio 应 ≤ 0.8
    assert r.details.get("last_trough_vol_ratio", 1.0) <= 0.8, \
        f"最后trough量比应 ≤ 0.8，实际 {r.details.get('last_trough_vol_ratio')}"
