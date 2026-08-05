"""indicators.py — 全套技术指标计算（~22 个，含成交量维度）

输入 OHLCV DataFrame（列：open/high/low/close/volume），输出结构化指标表。
纯 pandas/numpy 自算，不依赖外部数据源——扩展性强、可审计、可复现。

5 类指标：
- 价格动量：RSI / MACD / KDJ / WR%R / CCI / ROC / BIAS
- 成交量 ⭐：OBV / MFI / VWAP / A-D 线 / 量比 / 量均线
- 趋势：EMA(20/50/200) / SMA(50/200) / ADX / Aroon
- 波动：ATR / 布林带 / 历史波动率
- 量价背离：价升量缩 / 顶底背离（OBV vs 价格）

用法：
    from quant_scanner.features.indicators import compute_all
    table = compute_all(ohlcv_df)  # df 含 open/high/low/close/volume
"""
from __future__ import annotations

import numpy as np
import pandas as pd


# ========== Wilder smoothing 辅助函数 ==========

def wilder_smoothing(values: pd.Series, period: int = 14) -> pd.Series:
    """Wilder smoothing（任务 #99 校准）

    Wilder (1978)《New Concepts in Technical Trading Systems》原始定义：
    1. **首值** = 前 `period` 个**非 NaN** 值的简单平均（SMA）
    2. **后续值** = (prev × (period-1) + current) / period

    这是 Wilder 在 RSI/ATR/ADX 中使用的标准平滑法。

    与 `ewm(alpha=1/period, adjust=False)` 的区别：
    - ewm 首值 = 序列第一个非 NaN 值（数学上不严格符合 Wilder）
    - wilder_smoothing 首值 = SMA(period)（与 TradingView/MetaTrader 对齐）

    后续值数学上等价（都是递归加权），前 14 天有显著差异。

    NaN 处理：
    - 前导 NaN 被跳过（从第一个非 NaN 开始累计）
    - 中间 NaN 沿用前一值（典型 Wilder 行为，保证递归稳定）

    Args:
        values: 输入序列
        period: Wilder 周期（默认 14，Wilder 原始值）

    Returns:
        pd.Series，前 `period-1` 个有效值为 NaN（数据不足以计算 SMA）
    """
    values = pd.Series(values).astype(float)
    n = len(values)

    # 找第一个非 NaN
    arr = values.values
    first_valid_idx = -1
    for i in range(n):
        if not np.isnan(arr[i]):
            first_valid_idx = i
            break

    if first_valid_idx < 0 or (n - first_valid_idx) < period:
        return pd.Series([np.nan] * n, index=values.index)

    out = np.full(n, np.nan)
    # SMA 起始 = 从 first_valid_idx 开始的 period 个非 NaN 值的平均
    start = first_valid_idx
    valid_count = 0
    sma_sum = 0.0
    sma_end = -1
    for i in range(start, n):
        v = arr[i]
        if not np.isnan(v):
            sma_sum += v
            valid_count += 1
            if valid_count == period:
                sma_end = i
                break

    if sma_end < 0:
        return pd.Series([np.nan] * n, index=values.index)

    out[sma_end] = sma_sum / period

    # 后续值递归
    for i in range(sma_end + 1, n):
        v = arr[i]
        if np.isnan(v):
            out[i] = out[i - 1]
        else:
            out[i] = (out[i - 1] * (period - 1) + v) / period

    return pd.Series(out, index=values.index, name=f"wilder_{period}")


# ========== 价格动量 ==========

def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder RSI（0~100）

    **任务 #99 校准**：首值用 SMA(period) 起始（Wilder 1978 原始定义），
    后续用 Wilder smoothing。与 TradingView/MetaTrader 完全对齐。

    .. math::
        \\text{RSI} = 100 - \\frac{100}{1 + \\text{RS}}, \\quad
        \\text{RS} = \\frac{\\text{avg\\_gain}}{\\text{avg\\_loss}}

    其中 avg_gain/avg_loss 用 Wilder smoothing 平滑。
    """
    delta = close.diff()
    gain = delta.clip(lower=0).fillna(0)
    loss = (-delta).clip(lower=0).fillna(0)
    avg_gain = wilder_smoothing(gain, period=period)
    avg_loss = wilder_smoothing(loss, period=period)
    avg_loss = avg_loss.replace(0, 1e-10)
    rs = avg_gain / avg_loss
    return (100 - 100 / (1 + rs)).ffill()


def macd(close: pd.Series, fast=12, slow=26, signal=9):
    """MACD 三件套 → (macd_line, signal_line, histogram)"""
    ema_f = close.ewm(span=fast, adjust=False).mean()
    ema_s = close.ewm(span=slow, adjust=False).mean()
    macd_line = ema_f - ema_s
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    hist = macd_line - signal_line
    return macd_line, signal_line, hist


def kdj(high, low, close, n=9, m1=3, m2=3):
    """KDJ 随机指标 → (K, D, J)。J=3K-2D"""
    low_n = low.rolling(n).min()
    high_n = high.rolling(n).max()
    rsv = (close - low_n) / (high_n - low_n).replace(0, np.nan) * 100
    k = rsv.ewm(alpha=1 / m1, adjust=False).mean()
    d = k.ewm(alpha=1 / m2, adjust=False).mean()
    j = 3 * k - 2 * d
    return k, d, j


def williams_r(high, low, close, period=14) -> pd.Series:
    """威廉 %R（范围 -100~0）"""
    high_n = high.rolling(period).max()
    low_n = low.rolling(period).min()
    return (high_n - close) / (high_n - low_n).replace(0, np.nan) * -100


def cci(high, low, close, period=20) -> pd.Series:
    """CCI 商品通道指标"""
    tp = (high + low + close) / 3
    sma_tp = tp.rolling(period).mean()
    mad = tp.rolling(period).apply(lambda x: np.abs(x - x.mean()).mean(), raw=True)
    return (tp - sma_tp) / (0.015 * mad.replace(0, np.nan))


def roc(close, period=12) -> pd.Series:
    """变动率 ROC"""
    return (close - close.shift(period)) / close.shift(period).replace(0, np.nan) * 100


def bias(close, period=6) -> pd.Series:
    """乖离率 BIAS"""
    sma = close.rolling(period).mean()
    return (close - sma) / sma.replace(0, np.nan) * 100


# ========== 成交量 ⭐ ==========

def obv(close, volume) -> pd.Series:
    """OBV 能量潮（累积，OBV 上升=资金流入）"""
    sign = np.sign(close.diff().fillna(0))
    return (sign * volume).cumsum()


def mfi(high, low, close, volume, period=14) -> pd.Series:
    """MFI 资金流量指标（带量的 RSI，0~100）"""
    tp = (high + low + close) / 3
    mf = tp * volume
    delta_tp = tp.diff()
    pos_mf = mf.where(delta_tp > 0, 0)
    neg_mf = mf.where(delta_tp < 0, 0)
    pos_sum = pos_mf.rolling(period).sum()
    neg_sum = neg_mf.rolling(period).sum()
    mfr = pos_sum / neg_sum.replace(0, np.nan)
    return 100 - 100 / (1 + mfr)


def vwap(high, low, close, volume, period=20) -> pd.Series:
    """滚动 VWAP（成交量加权均价）"""
    tp = (high + low + close) / 3
    return (tp * volume).rolling(period).sum() / volume.rolling(period).sum().replace(0, np.nan)


def ad_line(high, low, close, volume) -> pd.Series:
    """A/D 线（累积派发，上升=累积买盘）"""
    clv = ((close - low) - (high - close)) / (high - low).replace(0, np.nan)
    return (clv * volume).fillna(0).cumsum()


def volume_ratio(volume, period=5) -> pd.Series:
    """量比 = 当日量 / N 日均量"""
    return volume / volume.rolling(period).mean().replace(0, np.nan)


# ========== 趋势 ==========

def adx(high, low, close, period=14):
    """ADX 趋势强度（+DI, -DI, ADX）。Wilder 法（任务 #99 校准）

    Wilder (1978) 原始定义：
    1. +DM = 上涨幅度（若 > 下跌幅度 且 > 0）
    2. -DM = 下跌幅度（若 > 上涨幅度 且 > 0）
    3. 平滑 +DM, -DM, TR 用 Wilder smoothing（首值 SMA）
    4. +DI = 100 × smoothed(+DM) / smoothed(TR)
    5. DX = |+DI - -DI| / (+DI + -DI) × 100
    6. ADX = Wilder smoothed DX
    """
    up = high.diff()
    down = -low.diff()
    plus_dm = up.where((up > down) & (up > 0), 0).fillna(0)
    minus_dm = down.where((down > up) & (down > 0), 0).fillna(0)
    tr1 = high - low
    tr2 = (high - close.shift()).abs()
    tr3 = (low - close.shift()).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1).fillna(0)

    atr_s = wilder_smoothing(tr, period=period)
    plus_dm_s = wilder_smoothing(plus_dm, period=period)
    minus_dm_s = wilder_smoothing(minus_dm, period=period)

    atr_safe = atr_s.replace(0, np.nan)
    plus_di = 100 * plus_dm_s / atr_safe
    minus_di = 100 * minus_dm_s / atr_safe
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    # ADX = Wilder smoothing of DX（前导 NaN 被自动跳过）
    adx_val = wilder_smoothing(dx, period=period)
    return plus_di, minus_di, adx_val


def aroon(high, low, period=25):
    """Aroon Up/Down（0~100）"""
    periods_since_high = high.rolling(period + 1).apply(lambda x: period - x.argmax(), raw=True)
    periods_since_low = low.rolling(period + 1).apply(lambda x: period - x.argmin(), raw=True)
    return periods_since_high / period * 100, periods_since_low / period * 100


# ========== 波动 ==========

def atr(high, low, close, period=14) -> pd.Series:
    """ATR 真实波幅（任务 #99 校准 Wilder smoothing）

    Wilder (1978) 原始定义：
    1. TR = max(high-low, |high-prev_close|, |low-prev_close|)
    2. 首日 ATR = SMA(TR, period)
    3. 后续 ATR = (prev × (period-1) + current_TR) / period
    """
    tr1 = high - low
    tr2 = (high - close.shift()).abs()
    tr3 = (low - close.shift()).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1).fillna(0)
    return wilder_smoothing(tr, period=period)


def bollinger(close, period=20, n_std=2):
    """布林带 → (upper, middle, lower, bandwidth)"""
    middle = close.rolling(period).mean()
    std = close.rolling(period).std()
    upper = middle + n_std * std
    lower = middle - n_std * std
    bandwidth = (upper - lower) / middle.replace(0, np.nan)
    return upper, middle, lower, bandwidth


def historical_volatility(close, period=20, annualize=True) -> pd.Series:
    """历史波动率（年化）"""
    ret = close.pct_change()
    vol = ret.rolling(period).std()
    return vol * np.sqrt(252) if annualize else vol


# ========== 量价背离检测 ==========

def price_volume_divergence(close, obv_series, lookback=30):
    """OBV vs 价格背离检测"""
    if len(close) < lookback:
        return None
    recent_close = close.iloc[-lookback:]
    recent_obv = obv_series.iloc[-lookback:]
    # 顶背离：价格近高，OBV 未跟上
    if close.iloc[-1] >= recent_close.quantile(0.95) and obv_series.iloc[-1] < recent_obv.quantile(0.95):
        return "top_divergence"
    # 底背离：价格近低，OBV 未创新低
    if close.iloc[-1] <= recent_close.quantile(0.05) and obv_series.iloc[-1] > recent_obv.quantile(0.05):
        return "bottom_divergence"
    return None


def volume_price_anomaly(close, volume, period=5):
    """价升量缩 / 价跌量增检测"""
    price_chg = close.pct_change(period).iloc[-1]
    vol_chg = volume.pct_change(period).iloc[-1]
    if pd.isna(price_chg) or pd.isna(vol_chg):
        return None
    if price_chg > 0.02 and vol_chg < -0.1:
        return "price_up_volume_down"  # 价升量缩=动能不足
    if price_chg < -0.02 and vol_chg > 0.1:
        return "price_down_volume_up"  # 价跌量增=抛压
    return None


# ========== 汇总 ==========

def _latest(series):
    """取序列最新非 NaN 值"""
    v = series.dropna()
    return float(v.iloc[-1]) if len(v) else None


def _prev(series):
    """取序列倒数第二个非 NaN 值"""
    v = series.dropna()
    return float(v.iloc[-2]) if len(v) >= 2 else None


def compute_all(df: pd.DataFrame) -> dict:
    """计算全套 ~22 指标。

    Args:
        df: OHLCV DataFrame，需含 open/high/low/close/volume 列（小写）

    Returns:
        {category: [indicator_dict]}，每个 indicator_dict = {name, value, prev, signal, detail}
        categories: price_momentum / volume / trend / volatility / divergence
    """
    close, high, low, volume = df["close"], df["high"], df["low"], df["volume"]
    c_val = _latest(close)
    result = {"price_momentum": [], "volume": [], "trend": [], "volatility": [], "divergence": []}

    # ---------- 价格动量 ----------
    rsi_v = rsi(close)
    rsi_val = _latest(rsi_v)
    result["price_momentum"].append({
        "name": "RSI(14)", "value": rsi_val, "prev": _prev(rsi_v),
        "signal": "超买" if rsi_val and rsi_val > 70 else ("超卖" if rsi_val and rsi_val < 30 else "中性"),
        "detail": "Wilder RSI，>70 超买 <30 超卖 40-60 中性",
    })

    macd_line, signal_line, hist = macd(close)
    m_val, s_val, h_val, h_prev = _latest(macd_line), _latest(signal_line), _latest(hist), _prev(hist)
    cross = "金叉" if (h_prev is not None and h_prev < 0 and h_val and h_val > 0) else \
            ("死叉" if (h_prev is not None and h_prev > 0 and h_val and h_val < 0) else
             ("柱扩大(动能增强)" if (h_val and h_prev and abs(h_val) > abs(h_prev)) else "柱缩小(动能衰减)"))
    result["price_momentum"].append({
        "name": "MACD(12,26,9)", "value": {"DIF": m_val, "DEA": s_val, "hist": h_val},
        "prev": h_prev, "signal": cross, "detail": "DIF 上穿 DEA=金叉，柱状图=动能",
    })

    k, d, j = kdj(high, low, close)
    j_val = _latest(j)
    result["price_momentum"].append({
        "name": "KDJ(9,3,3)", "value": {"K": _latest(k), "D": _latest(d), "J": j_val},
        "signal": "超买" if (j_val and j_val > 100) else ("超卖" if (j_val and j_val < 0) else "中性"),
        "detail": "J>100 超买 <0 超卖，K>D 多头",
    })

    wr = williams_r(high, low, close)
    wr_val = _latest(wr)
    result["price_momentum"].append({
        "name": "WR%R(14)", "value": wr_val,
        "signal": "超买" if (wr_val and wr_val > -20) else ("超卖" if (wr_val and wr_val < -80) else "中性"),
        "detail": "范围 -100~0，>-20 超买 <-80 超卖",
    })

    cci_v = cci(high, low, close)
    cci_val = _latest(cci_v)
    result["price_momentum"].append({
        "name": "CCI(20)", "value": cci_val,
        "signal": "超买" if (cci_val and cci_val > 100) else ("超卖" if (cci_val and cci_val < -100) else "中性"),
        "detail": ">100 超买 <-100 超卖",
    })
    result["price_momentum"].append({
        "name": "ROC(12)", "value": _latest(roc(close)), "signal": "动量", "detail": "变动率，正=上涨动能"})
    result["price_momentum"].append({
        "name": "BIAS(6)", "value": _latest(bias(close, 6)), "signal": "乖离", "detail": "偏离均线，过大=回归风险"})

    # ---------- 成交量 ----------
    obv_v = obv(close, volume)
    obv_val, obv_prev = _latest(obv_v), _prev(obv_v)
    result["volume"].append({
        "name": "OBV", "value": obv_val, "prev": obv_prev,
        "signal": "资金流入" if (obv_val and obv_prev and obv_val > obv_prev) else "资金流出",
        "detail": "能量潮，OBV 上升=资金净流入（领先价格）",
    })

    mfi_v = mfi(high, low, close, volume)
    mfi_val = _latest(mfi_v)
    result["volume"].append({
        "name": "MFI(14)", "value": mfi_val,
        "signal": "超买" if (mfi_val and mfi_val > 80) else ("超卖" if (mfi_val and mfi_val < 20) else "中性"),
        "detail": "带量 RSI（资金流量），>80 超买 <20 超卖",
    })

    vwap_v = vwap(high, low, close, volume)
    vwap_val = _latest(vwap_v)
    result["volume"].append({
        "name": "VWAP(20)", "value": vwap_val,
        "signal": "价偏强" if (c_val and vwap_val and c_val > vwap_val) else "价偏弱",
        "detail": "成交量加权均价，价>VWAP=多头主导",
    })

    ad = ad_line(high, low, close, volume)
    ad_val, ad_prev = _latest(ad), _prev(ad)
    result["volume"].append({
        "name": "A/D Line", "value": ad_val, "prev": ad_prev,
        "signal": "累积(买盘)" if (ad_val and ad_prev and ad_val > ad_prev) else "派发(卖盘)",
        "detail": "A/D 上升=累积买盘，背离价格=预警",
    })

    vr = volume_ratio(volume)
    vr_val = _latest(vr)
    result["volume"].append({
        "name": "量比(5)", "value": vr_val,
        "signal": "放量" if (vr_val and vr_val > 1.5) else ("缩量" if (vr_val and vr_val < 0.7) else "正常"),
        "detail": "当日量/5日均量，>1.5 放量 <0.7 缩量",
    })

    vol_ma5 = volume.rolling(5).mean()
    vol_ma20 = volume.rolling(20).mean()
    ma5, ma20 = _latest(vol_ma5), _latest(vol_ma20)
    result["volume"].append({
        "name": "量均线(5/20)", "value": {"MA5": ma5, "MA20": ma20},
        "signal": "量能放大" if (ma5 and ma20 and ma5 > ma20) else "量能萎缩",
        "detail": "5 日均量 vs 20 日均量",
    })

    # ---------- 趋势 ----------
    ema20 = close.ewm(span=20, adjust=False).mean()
    ema50 = close.ewm(span=50, adjust=False).mean()
    ema200 = close.ewm(span=200, adjust=False).mean()
    e50, e200 = _latest(ema50), _latest(ema200)
    alignment = "多头排列↑" if (c_val and e50 and e200 and c_val > e50 > e200) else \
                ("空头排列↓" if (c_val and e50 and e200 and c_val < e50 < e200) else "均线纠缠")
    result["trend"].append({
        "name": "EMA(20/50/200)", "value": {"EMA20": _latest(ema20), "EMA50": e50, "EMA200": e200},
        "signal": alignment, "detail": "多头排列=上升趋势，价>EMA50>EMA200"})

    sma50 = close.rolling(50).mean()
    sma200 = close.rolling(200).mean()
    s50, s200 = _latest(sma50), _latest(sma200)
    result["trend"].append({
        "name": "SMA(50/200)", "value": {"SMA50": s50, "SMA200": s200},
        "signal": "金叉(多头)" if (s50 and s200 and s50 > s200) else "死叉(空头)",
        "detail": "50/200 金叉=长期多头信号"})

    plus_di, minus_di, adx_v = adx(high, low, close)
    adx_val, pdi, mdi = _latest(adx_v), _latest(plus_di), _latest(minus_di)
    trend_strength = "强趋势" if (adx_val and adx_val > 25) else ("无趋势" if (adx_val and adx_val < 20) else "趋势形成中")
    trend_dir = "多头" if (pdi and mdi and pdi > mdi) else "空头"
    result["trend"].append({
        "name": "ADX(14)", "value": {"ADX": adx_val, "+DI": pdi, "-DI": mdi},
        "signal": f"{trend_strength}({trend_dir})", "detail": "ADX>25 强趋势，+DI>-DI 多头"})

    ar_up, ar_down = aroon(high, low)
    au, ad2 = _latest(ar_up), _latest(ar_down)
    result["trend"].append({
        "name": "Aroon(25)", "value": {"Up": au, "Down": ad2},
        "signal": "上行" if (au and ad2 and au > ad2) else "下行",
        "detail": "Up>70 强势上行"})

    # ---------- 波动 ----------
    atr_v = atr(high, low, close)
    atr_val = _latest(atr_v)
    result["volatility"].append({
        "name": "ATR(14)", "value": atr_val, "signal": "止损参考",
        "detail": f"波动率，止损可设 ±{atr_val:.2f}（约 {atr_val / c_val * 100:.1f}%）" if (atr_val and c_val) else "波动率"})

    upper, middle, lower, bw = bollinger(close)
    up_v, mid_v, low_v = _latest(upper), _latest(middle), _latest(lower)
    bb_pos = "触上轨(超买)" if (c_val and up_v and c_val > up_v * 0.99) else \
             ("触下轨(超卖)" if (c_val and low_v and c_val < low_v * 1.01) else
              ("中轨上方" if (c_val and mid_v and c_val > mid_v) else "中轨下方"))
    result["volatility"].append({
        "name": "Bollinger(20,2)", "value": {"上轨": up_v, "中轨": mid_v, "下轨": low_v, "带宽": _latest(bw)},
        "signal": bb_pos, "detail": "触上轨超买 触下轨超卖 带宽收缩=变盘临近"})

    hv = historical_volatility(close)
    result["volatility"].append({
        "name": "历史波动率(20)", "value": _latest(hv), "signal": "波动水平", "detail": "年化收益率标准差"})

    # ---------- 量价背离 ----------
    pv_div = price_volume_divergence(close, obv_v)
    vpa = volume_price_anomaly(close, volume)
    flags = []
    if pv_div == "top_divergence":
        flags.append("⚠️ 顶背离：价格近高但 OBV 未新高（动能衰竭）")
    elif pv_div == "bottom_divergence":
        flags.append("🟢 底背离：价格近低但 OBV 未新低（抛压衰竭）")
    if vpa == "price_up_volume_down":
        flags.append("⚠️ 价升量缩：上涨动能不足")
    elif vpa == "price_down_volume_up":
        flags.append("⚠️ 价跌量增：抛压放大")
    result["divergence"].append({
        "name": "量价背离检测", "value": pv_div or vpa or "无",
        "signal": "有背离" if flags else "无背离",
        "detail": "；".join(flags) if flags else "量价配合正常",
    })

    return result
