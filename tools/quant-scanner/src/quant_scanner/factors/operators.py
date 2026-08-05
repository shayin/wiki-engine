"""量化算子库（factors/operators.py）

来源：WorldQuant / Kakushadze 2015《101 Formulaic Alphas》论文中的算子定义。

**设计哲学**：
- 所有算子接受/返回 ``pd.Series`` 或 ``pd.DataFrame``，保持索引对齐
- 统一接口：输入 OHLCV 数据 + 窗口参数 → 输出 pd.Series
- 防御 NaN / 除零 / 常数输入
- 不引入外部依赖（仅 numpy + pandas）

**算子分类**（共 50+）：

A. 时序统计（ts_）
   - ts_mean / ts_std / ts_max / ts_min / ts_arg_max / ts_arg_min
   - ts_delta / ts_delay / ts_rank / ts_skew / ts_kurt / ts_median
   - ts_sum / ts_product / ts_corr / ts_covariance / ts_regression
   - ts_decay_linear / ts_decay_exp

B. 横截面（cross_）
   - rank / scale / zscore / winsorize / quantile
   （单标的模式下降级为时间窗内的 rank，跨标的时用 cross-sectional）

C. 数学（一元 + 二元）
   - add / sub / mul / div / abs / sign / log / power / max / min
   - relu / sigmoid / tanh

D. 技术（ta_）
   - rsi / macd / bollinger / atr / adx / obv / mfi / vwap

E. 价格派生
   - returns / log_returns / high_low_range / typical_price

**典型用法**：
```python
from quant_scanner.factors.operators import (
    ts_rank, ts_corr, rank, sub, div
)

# Alpha#1: rank(ts_delta(log(close), 5)) — 反转因子
delta_log = ts_delta(np.log(close), 5)
alpha_1 = rank(delta_log)
```

**与 quant-scanner 现有 signals 的区别**：
- signals/：完整策略（继承 BaseSignal，返回 SignalResult）
- factors/operators.py：原子算子，供 alpha101 / GP / ML 调用
"""
from __future__ import annotations

import numpy as np
import pandas as pd


# ============================================================
# A. 时序统计算子（ts_）
# ============================================================

def ts_mean(series: pd.Series, window: int = 20) -> pd.Series:
    """时序滚动均值"""
    return series.rolling(window=window, min_periods=max(1, window // 2)).mean()


def ts_median(series: pd.Series, window: int = 20) -> pd.Series:
    return series.rolling(window=window, min_periods=max(1, window // 2)).median()


def ts_std(series: pd.Series, window: int = 20) -> pd.Series:
    """时序滚动标准差（总体方差除以 N）"""
    return series.rolling(window=window, min_periods=max(1, window // 2)).std()


def ts_var(series: pd.Series, window: int = 20) -> pd.Series:
    return series.rolling(window=window, min_periods=max(1, window // 2)).var()


def ts_sum(series: pd.Series, window: int = 20) -> pd.Series:
    """滚动求和（WorldQuant sum）"""
    return series.rolling(window=window, min_periods=1).sum()


def ts_max(series: pd.Series, window: int = 20) -> pd.Series:
    return series.rolling(window=window, min_periods=1).max()


def ts_min(series: pd.Series, window: int = 20) -> pd.Series:
    return series.rolling(window=window, min_periods=1).min()


def ts_arg_max(series: pd.Series, window: int = 20) -> pd.Series:
    """滚动窗口内最大值出现的位置（距窗口末端的偏移天数）

    WorldQuant 定义：返回 0..window-1，window-1 表示最大值在最新日。
    """
    def _argmax(s):
        if len(s) == 0:
            return np.nan
        return float(np.argmax(s.values))
    return series.rolling(window=window, min_periods=1).apply(_argmax, raw=False)


def ts_arg_min(series: pd.Series, window: int = 20) -> pd.Series:
    def _argmin(s):
        if len(s) == 0:
            return np.nan
        return float(np.argmin(s.values))
    return series.rolling(window=window, min_periods=1).apply(_argmin, raw=False)


def ts_delta(series: pd.Series, period: int = 1) -> pd.Series:
    """ts_delta(x, d) = x_t - x_{t-d}"""
    return series.diff(period)


def ts_delay(series: pd.Series, period: int = 1) -> pd.Series:
    """ts_delay(x, d) = x_{t-d}"""
    return series.shift(period)


def ts_rank(series: pd.Series, window: int = 20) -> pd.Series:
    """滚动窗口内当前值的排名（归一化到 [0, 1]）

    WorldQuant 定义：返回当前值在窗口内的百分位。
    """
    def _rank(s):
        if len(s) == 0:
            return np.nan
        # 用 average rank 处理 ties
        ranks = pd.Series(s.values).rank(method="average").iloc[-1]
        return float((ranks - 1) / max(1, len(s) - 1))
    return series.rolling(window=window, min_periods=2).apply(_rank, raw=False)


def ts_skew(series: pd.Series, window: int = 20) -> pd.Series:
    return series.rolling(window=window, min_periods=max(3, window // 2)).skew()


def ts_kurt(series: pd.Series, window: int = 20) -> pd.Series:
    return series.rolling(window=window, min_periods=max(4, window // 2)).kurt()


def ts_corr(x: pd.Series, y: pd.Series, window: int = 20) -> pd.Series:
    """两个序列的滚动 Pearson 相关系数"""
    min_p = min(window, max(3, window // 2))
    return x.rolling(window=window, min_periods=min_p).corr(y)


def ts_cov(x: pd.Series, y: pd.Series, window: int = 20) -> pd.Series:
    min_p = min(window, max(3, window // 2))
    return x.rolling(window=window, min_periods=min_p).cov(y)


def ts_decay_linear(series: pd.Series, window: int = 20) -> pd.Series:
    """线性衰减加权平均（近期权重高）

    WorldQuant decay_linear(x, d)：weights = [d, d-1, ..., 1] / sum(1..d)
    """
    weights = np.arange(window, 0, -1, dtype=float)
    weights = weights / weights.sum()

    def _decay(s):
        if len(s) < window:
            # 不足窗口用可用部分的倒序权重
            n = len(s)
            w = np.arange(n, 0, -1, dtype=float)
            w = w / w.sum()
            return float(np.dot(s.values, w))
        return float(np.dot(s.values, weights))
    return series.rolling(window=window, min_periods=1).apply(_decay, raw=False)


def ts_decay_exp(series: pd.Series, window: int = 20, alpha: float = 0.5) -> pd.Series:
    """指数衰减（近因效应更强）"""
    return series.ewm(alpha=alpha, adjust=False, min_periods=1).mean()


def ts_product(series: pd.Series, window: int = 20) -> pd.Series:
    """滚动连乘"""
    def _prod(s):
        if len(s) == 0:
            return np.nan
        return float(np.prod(s.values))
    return series.rolling(window=window, min_periods=1).apply(_prod, raw=False)


def ts_scale(series: pd.Series, window: int = 20) -> pd.Series:
    """ts_scale(x, d) = x / ts_max(|x|, d)

    将序列缩放到 [-1, 1] 范围（按历史最大绝对值）。
    """
    abs_max = series.abs().rolling(window=window, min_periods=1).max()
    return series / abs_max.replace(0, np.nan)


# ============================================================
# B. 横截面算子（cross_）
# ============================================================
# 单标的模式下，rank/zscore 退化为时间窗内的横截面操作
# 真正的 cross-sectional 需要多标的的 DataFrame（每列一只股票）

def rank(series: pd.Series | pd.DataFrame) -> pd.Series | pd.DataFrame:
    """横截面排名，归一化到 [0, 1]

    - pd.Series: 沿时间轴 rank（等价于 ts_rank 全窗口）—— 仅作 fallback
    - pd.DataFrame: 沿列方向 rank（同一时间点跨股票排名）—— 真 cross-sectional
    """
    if isinstance(series, pd.DataFrame):
        return series.rank(axis=1, pct=True, method="average")
    # 单标的 fallback：全样本百分位
    return series.rank(pct=True, method="average")


def scale(series: pd.Series | pd.DataFrame) -> pd.Series | pd.DataFrame:
    """归一化到 sum=1（横截面）"""
    if isinstance(series, pd.DataFrame):
        return series.div(series.abs().sum(axis=1).replace(0, np.nan), axis=0)
    s = series.abs().sum()
    return series / s if s > 0 else series * 0


def zscore(series: pd.Series | pd.DataFrame, window: int = 20) -> pd.Series | pd.DataFrame:
    """滚动 z-score（时序版）

    cross-sectional 版本：传 DataFrame，按 axis=1 计算均值方差。
    """
    if isinstance(series, pd.DataFrame):
        mean = series.mean(axis=1)
        std = series.std(axis=1).replace(0, np.nan)
        return series.sub(mean, axis=0).div(std, axis=0)
    return (series - ts_mean(series, window)) / ts_std(series, window).replace(0, np.nan)


def winsorize(series: pd.Series, lower: float = 0.01, upper: float = 0.99) -> pd.Series:
    """缩尾：把超出分位数的值截断"""
    lo = series.quantile(lower)
    hi = series.quantile(upper)
    return series.clip(lower=lo, upper=hi)


def quantile_transform(series: pd.Series) -> pd.Series:
    """把任意分布转成均匀分布 [0, 1]"""
    return series.rank(pct=True, method="average")


# ============================================================
# C. 数学算子
# ============================================================

def _binary_op(a, b, op):
    """二元算子的统一处理器：支持 (Series, Series) / (Series, scalar) / (scalar, Series)"""
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return op(float(a), float(b))
    return op(a, b)


def add(a, b):
    return _binary_op(a, b, lambda x, y: x + y)


def sub(a, b):
    return _binary_op(a, b, lambda x, y: x - y)


def mul(a, b):
    return _binary_op(a, b, lambda x, y: x * y)


def div(a, b):
    """安全除法：除零返回 NaN（不抛 ZeroDivisionError）

    支持 scalar/scalar、Series/Series、Series/scalar、scalar/Series 混合输入。
    """
    def _safe_div_scalar(x, y):
        if y == 0:
            return np.nan
        return x / y
    if isinstance(a, pd.Series) and isinstance(b, pd.Series):
        with np.errstate(divide="ignore", invalid="ignore"):
            return a.divide(b.replace(0, np.nan))
    if isinstance(a, pd.Series):
        # Series / scalar
        with np.errstate(divide="ignore", invalid="ignore"):
            if b == 0:
                return pd.Series(np.nan, index=a.index)
            return a / b
    if isinstance(b, pd.Series):
        # scalar / Series
        with np.errstate(divide="ignore", invalid="ignore"):
            return a / b.replace(0, np.nan)
    return _safe_div_scalar(a, b)


def gt(a, b):
    """a > b → 1.0 / 0.0"""
    return _binary_op(a, b, lambda x, y: (x > y).astype(float) if hasattr(x, "astype") else float(x > y))


def lt(a, b):
    return _binary_op(a, b, lambda x, y: (x < y).astype(float) if hasattr(x, "astype") else float(x < y))


def eq(a, b):
    return _binary_op(a, b, lambda x, y: (x == y).astype(float) if hasattr(x, "astype") else float(x == y))


def abs_(series):
    return series.abs() if hasattr(series, "abs") else abs(series)


def sign(series):
    if hasattr(series, "np"):
        return np.sign(series)
    return np.sign(series)


def log(series):
    """自然对数，负数/零返回 NaN"""
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.log(series.where(series > 0) if hasattr(series, "where") else series)


def power(series, exp: float):
    """幂运算"""
    return np.power(series, exp)


def max_(a, b):
    """逐元素取大"""
    if isinstance(a, pd.Series) and isinstance(b, pd.Series):
        return pd.concat([a, b], axis=1).max(axis=1)
    return max(a, b)


def min_(a, b):
    if isinstance(a, pd.Series) and isinstance(b, pd.Series):
        return pd.concat([a, b], axis=1).min(axis=1)
    return min(a, b)


def relu(series, threshold: float = 0.0):
    """ReLU 激活：max(0, x - threshold)"""
    if hasattr(series, "clip"):
        return (series - threshold).clip(lower=0)
    return max(0.0, series - threshold)


def sigmoid(series):
    return 1.0 / (1.0 + np.exp(-series))


# ============================================================
# D. 技术指标算子（ta_）— 复用 features/indicators.py 中的实现
# ============================================================

def returns(close: pd.Series, period: int = 1) -> pd.Series:
    """简单收益率：close[t] / close[t-period] - 1"""
    return close.pct_change(period)


def log_returns(close: pd.Series, period: int = 1) -> pd.Series:
    """对数收益率：log(close[t] / close[t-period])"""
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.log(close / close.shift(period))


def high_low_range(high: pd.Series, low: pd.Series) -> pd.Series:
    """日内振幅：high - low"""
    return high - low


def typical_price(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    """典型价格：(H + L + C) / 3"""
    return (high + low + close) / 3.0


def vwap(high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series, window: int = 20) -> pd.Series:
    """滚动 VWAP（成交量加权平均价）"""
    tp = typical_price(high, low, close)
    pv = tp * volume
    vol_sum = ts_sum(volume, window)
    return div(pv.rolling(window=window, min_periods=1).sum(), vol_sum.replace(0, np.nan))


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """RSI（相对强弱指数）"""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100.0 - (100.0 / (1.0 + rs))


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> tuple[pd.Series, pd.Series, pd.Series]:
    """MACD（移动平均收敛发散）

    Returns:
        (macd_line, signal_line, hist)
    """
    ema_fast = close.ewm(span=fast, adjust=False, min_periods=fast).mean()
    ema_slow = close.ewm(span=slow, adjust=False, min_periods=slow).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    hist = macd_line - signal_line
    return macd_line, signal_line, hist


def bollinger(close: pd.Series, window: int = 20, num_std: float = 2.0) -> tuple[pd.Series, pd.Series, pd.Series]:
    """布林带

    Returns:
        (upper, middle, lower)
    """
    middle = ts_mean(close, window)
    std = ts_std(close, window)
    upper = middle + num_std * std
    lower = middle - num_std * std
    return upper, middle, lower


def atr(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 14) -> pd.Series:
    """ATR（平均真实波幅）"""
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1.0 / window, adjust=False, min_periods=window).mean()


def obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    """OBV（能量潮）"""
    direction = close.diff().fillna(0).apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))
    return (direction * volume).cumsum()


def mfi(high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series, period: int = 14) -> pd.Series:
    """MFI（资金流量指数，带量能的 RSI）"""
    tp = typical_price(high, low, close)
    mf = tp * volume  # 资金流量
    pos_mf = mf.where(tp.diff() > 0, 0)
    neg_mf = mf.where(tp.diff() < 0, 0)
    pos_sum = pos_mf.rolling(window=period, min_periods=period).sum()
    neg_sum = neg_mf.rolling(window=period, min_periods=period).sum()
    mfr = pos_sum / neg_sum.replace(0, np.nan)
    return 100.0 - 100.0 / (1.0 + mfr)


# ============================================================
# E. 工具函数
# ============================================================

def normalize_ohclv(df: pd.DataFrame) -> dict[str, pd.Series]:
    """从 OHLCV DataFrame 提取列，返回 dict 形式的算子可用输入"""
    required = ["open", "high", "low", "close", "volume"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"OHLCV 缺列: {missing}")
    return {
        "open": df["open"],
        "high": df["high"],
        "low": df["low"],
        "close": df["close"],
        "volume": df["volume"],
        "returns": returns(df["close"]),
        "log_returns": log_returns(df["close"]),
        "vwap": vwap(df["high"], df["low"], df["close"], df["volume"]),
    }


def factor_ic(factor: pd.Series, forward_returns: pd.Series) -> float:
    """计算因子的信息系数（Information Coefficient）

    IC = Spearman rank correlation(factor_t, forward_return_t+horizon)

    用于评估 alpha 的预测力。学术标准：|IC| > 0.03 算有效。

    Args:
        factor: 因子值序列
        forward_returns: 前瞻收益（需提前对齐 index）

    Returns:
        IC 值（float）
    """
    aligned = pd.concat([factor, forward_returns], axis=1, keys=["f", "r"]).dropna()
    if len(aligned) < 30:
        return float("nan")
    return float(aligned["f"].corr(aligned["r"], method="spearman"))


def factor_ir(factor: pd.Series, forward_returns: pd.Series, window: int = 20) -> float:
    """信息比率 = IC 均值 / IC 标准差（评估稳定性）

    学术标准：IR > 0.5 算高质量因子。Spearman 版（先 rank 再 Pearson）。
    """
    fr = forward_returns.rank(pct=True)
    fc = factor.rank(pct=True)
    ic_series = fc.rolling(window=window, min_periods=max(3, window // 2)).corr(fr)
    ic_series = ic_series.dropna()
    if len(ic_series) < 10:
        return float("nan")
    std = ic_series.std()
    if std == 0 or pd.isna(std):
        return 0.0
    return float(ic_series.mean() / std)


__all__ = [
    # 时序
    "ts_mean", "ts_median", "ts_std", "ts_var", "ts_sum",
    "ts_max", "ts_min", "ts_arg_max", "ts_arg_min",
    "ts_delta", "ts_delay", "ts_rank",
    "ts_skew", "ts_kurt", "ts_corr", "ts_cov",
    "ts_decay_linear", "ts_decay_exp", "ts_product", "ts_scale",
    # 横截面
    "rank", "scale", "zscore", "winsorize", "quantile_transform",
    # 数学
    "add", "sub", "mul", "div", "gt", "lt", "eq",
    "abs_", "sign", "log", "power", "max_", "min_",
    "relu", "sigmoid",
    # 技术
    "returns", "log_returns", "high_low_range", "typical_price", "vwap",
    "rsi", "macd", "bollinger", "atr", "obv", "mfi",
    # 工具
    "normalize_ohclv", "factor_ic", "factor_ir",
    # Regime
    "classify_regime", "regime_conditional_ic",
]


# ============================================================
# Regime（市场状态）分类 + 条件 IC（任务 #85）
# ============================================================

def classify_regime(
    df: pd.DataFrame,
    ma_long: int = 200,
    ma_short: int = 50,
) -> pd.Series:
    """基于价格趋势分类市场状态

    分类规则（Murphy/Minervini 趋势定义）：
    - **bull**（牛市）：close > MA200 且 MA200 上行（5 日斜率 > 0）
    - **bear**（熊市）：close < MA200 且 MA200 下行
    - **sideways**（震荡）：其他（MA200 走平或 close/MA200 关系不明确）

    Args:
        df: OHLCV DataFrame
        ma_long: 长期均线窗口（默认 200 日）
        ma_short: 短期均线窗口（默认 50 日，目前未使用，保留扩展）

    Returns:
        pd.Series：每个日期的 regime 标签（'bull' / 'bear' / 'sideways'）
    """
    if "close" not in df.columns or len(df) < ma_long + 5:
        return pd.Series("sideways", index=df.index)

    close = df["close"]
    ma = close.rolling(ma_long, min_periods=ma_long // 2).mean()
    ma_slope = ma.diff(5)  # 5 日斜率

    regime = pd.Series("sideways", index=df.index)
    above = close > ma
    rising = ma_slope > 0
    below = close < ma
    falling = ma_slope < 0

    regime[above & rising] = "bull"
    regime[below & falling] = "bear"
    # 其他保持 sideways

    return regime


def regime_conditional_ic(
    factor: pd.Series,
    forward_returns: pd.Series,
    regime: pd.Series,
    window: int = 20,
) -> dict[str, tuple[float, int]]:
    """分 regime 计算条件 IC

    把时序按 regime 分段（bull / bear / sideways），每段单独算 IC。
    用于发现 regime-adaptive 因子（某因子在牛市有效但熊市失效）。

    实现说明：
    - **每段独立 rank**（不是整体 rank 后分段）：保证 regime 内的相对排名不被其他 regime 稀释
    - 每段先用滚动窗口算 IC 序列，再取均值；样本不足时退化为整体 IC

    Args:
        factor: 因子值序列
        forward_returns: 前瞻收益序列
        regime: `classify_regime()` 的输出
        window: IC 计算的滚动窗口（每段内）

    Returns:
        dict：{"bull": (ic, n_days), "bear": (ic, n_days), "sideways": (ic, n_days)}
        - ic: 该 regime 下的 IC 均值（NaN 如果样本不足）
        - n_days: 该 regime 的有效样本天数（IC 序列 dropna 后）
    """
    common = factor.index.intersection(forward_returns.index).intersection(regime.index)
    if len(common) < 5:
        return {r: (float("nan"), 0) for r in ["bull", "bear", "sideways"]}

    f = factor.loc[common]
    r = forward_returns.loc[common]
    g = regime.loc[common]

    result = {}
    for label in ["bull", "bear", "sideways"]:
        mask = (g == label)
        if mask.sum() < 5:
            result[label] = (float("nan"), int(mask.sum()))
            continue

        # 段内独立 rank（避免其他 regime 稀释）
        f_seg = f[mask].rank(pct=True)
        r_seg = r[mask].rank(pct=True)

        # 样本不足用整体 IC
        if mask.sum() < window:
            valid = f_seg.notna() & r_seg.notna()
            if valid.sum() < 3:
                result[label] = (float("nan"), int(mask.sum()))
                continue
            ic = float(f_seg[valid].corr(r_seg[valid]))
            result[label] = (ic, int(valid.sum()))
            continue

        # 滚动 IC 序列
        ic_series = f_seg.rolling(window=window, min_periods=max(3, window // 2)).corr(r_seg)
        ic_series = ic_series.dropna()
        if len(ic_series) < 3:
            result[label] = (float("nan"), int(mask.sum()))
        else:
            result[label] = (float(ic_series.mean()), len(ic_series))
    return result
