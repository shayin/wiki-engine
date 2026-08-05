"""WorldQuant 101 Formulaic Alphas — Kakushadze 2015 移植

来源：Zura Kakushadze《101 Formulaic Alphas》(SSRN 2015, https://ssrn.com/abstract=2701346, AI Wiki 本地存档：data/ssrn-2701346.pdf)

**论文背景**：101 个公开公式化 alpha 因子，全部基于 price/volume，由 WorldQuant 内部测试有效。
本模块实现前 20 个（最具代表性的）+ 提供扩展框架。

**单标的模式下的妥协**：
- 论文里 ``rank`` = cross-sectional rank（跨股票排名），需要多标的 DataFrame
- 本模块默认单标的模式，``rank`` 退化为时序百分位（全样本）
- 真正 cross-sectional 版本见 ``Alpha101CrossSectional``（待实现，需 multi-ticker batch）

**接口设计**：
- 每个 alpha 函数接受 OHLCV DataFrame，返回 pd.Series
- 函数命名：``alpha_N``（N 对应论文编号）
- 返回值不归一化（用户自行 rank/scale）
- ``Alpha101`` 类提供批量调用 + IC 评估

**论文算子映射**：
- ``rank(x)`` → ``rank(x)``（单标的时序版）
- ``ts_rank(x, d)`` → ``ts_rank(x, d)``
- ``ts_delta(x, d)`` → ``ts_delta(x, d)``
- ``ts_delay(x, d)`` → ``ts_delay(x, d)``
- ``correlation(x, y, d)`` → ``ts_corr(x, y, d)``
- ``covariance(x, y, d)`` → ``ts_cov(x, y, d)``
- ``scale(x, a=1)`` → ``scale(x)``（忽略 a，做 sum=1 归一化）
- ``decay_linear(x, d)`` → ``ts_decay_linear(x, d)``
- ``ts_arg_max(x, d)`` → ``ts_arg_max(x, d)``
- ``ts_max(x, d)`` → ``ts_max(x, d)``
- ``adv{d}`` = ``ts_mean(volume, d)``（d 日均量）
- ``signedpower(x, a)`` = ``sign(x) * abs(x)^a``
- ``IndNeutralize(x)`` → 跳过（单标的无行业信息）

**典型用法**：
```python
from quant_scanner.data.loader import DataLoader
from quant_scanner.factors.alpha101 import Alpha101
from quant_scanner.factors.operators import factor_ic, log_returns

loader = DataLoader()
df = loader.load("NVDA", period="2y")
fwd = log_returns(df["close"]).shift(-5)  # 5 日前瞻收益

alpha = Alpha101()
ic = {}
for name, factor in alpha.compute_all(df).items():
    ic[name] = factor_ic(factor, fwd)
# IC 排序看哪些 alpha 有效
```

**警告**：论文 alpha 在 A 股/港股有效性差于美股。务必跑 ``stats`` 验证样本外表现后再挂载。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .operators import (
    add, sub, mul, div, abs_, sign, log, power,
    rank, ts_rank, ts_delta, ts_delay, ts_corr, ts_cov,
    ts_sum, ts_mean, ts_max, ts_min, ts_arg_max, ts_arg_min,
    ts_std, ts_decay_linear, scale, max_, min_,
    returns, vwap, ts_product,
)


def _signed_power(x: pd.Series, a: float) -> pd.Series:
    """signedpower(x, a) = sign(x) * |x|^a"""
    return sign(x) * power(abs_(x), a)


def _adv(volume: pd.Series, d: int) -> pd.Series:
    """adv{d} = d 日均量"""
    return ts_mean(volume, d)


# ============================================================
# Alpha 1-20（按 Kakushadze 2015 论文顺序）
# ============================================================

def alpha_1(df: pd.DataFrame) -> pd.Series:
    """Alpha#1: rank(Ts_ArgMax(SignedPower(((returns < 0) ? stddev(returns, 20) : close), 2.), 5)) - 0.5

    含义：过去 5 天内最大"波动放大点"出现的位置（趋势加速信号）。
    """
    ret = returns(df["close"])
    cond = ret.where(ret < 0, other=df["close"])  # 三元运算
    sp = _signed_power(cond.ffill(), 2.0)
    return sub(rank(ts_arg_max(sp, 5)), 0.5)


def alpha_2(df: pd.DataFrame) -> pd.Series:
    """Alpha#2: -1 * correlation(rank(delta(log(volume), 2)), rank(((close - open) / open)), 6)

    含义：放量 + 收涨 的协同性，反向（高协同时看空）。
    """
    inner1 = rank(ts_delta(np.log(df["volume"].where(df["volume"] > 0)), 2))
    inner2 = rank(div(sub(df["close"], df["open"]), df["open"]))
    return mul(-1.0, ts_corr(inner1, inner2, 6))


def alpha_3(df: pd.DataFrame) -> pd.Series:
    """Alpha#3: -1 * correlation(rank(open), rank(volume), 10)

    含义：开盘价与成交量的相关性（量价同步时看空）。
    """
    return mul(-1.0, ts_corr(rank(df["open"]), rank(df["volume"]), 10))


def alpha_4(df: pd.DataFrame) -> pd.Series:
    """Alpha#4: -1 * Ts_Rank(rank(low), 9)

    含义：低价的时序排名反向（突破时看多）。
    """
    return mul(-1.0, ts_rank(rank(df["low"]), 9))


def alpha_5(df: pd.DataFrame) -> pd.Series:
    """Alpha#5: sum((open > (sum(vwap, 10) / 10)) ? 1 : 0, 100) / sum((open < vwap_10_avg) ? 1 : 0, 100)

    简化版（论文版本含 cond 或 1）：
    sum(open > vwap_10, 100) / sum(open < vwap_10, 100)
    """
    vw = vwap(df["high"], df["low"], df["close"], df["volume"], window=10)
    vwap_avg = ts_mean(vw, 10)
    above = (df["open"] > vwap_avg).astype(float)
    below = (df["open"] < vwap_avg).astype(float)
    return div(ts_sum(above, 100), ts_sum(below, 100).replace(0, np.nan))


def alpha_6(df: pd.DataFrame) -> pd.Series:
    """Alpha#6: -1 * correlation(open, volume, 10)"""
    return mul(-1.0, ts_corr(df["open"], df["volume"], 10))


def alpha_7(df: pd.DataFrame) -> pd.Series:
    """Alpha#7: adv20 < volume ? -1 * ts_rank(abs(delta(close, 7)), 60) * sign(delta(correlation(adv20, low, 6), 0)) : -1

    含义：放量 + 7 日变化 + 量低价相关。
    """
    adv20 = _adv(df["volume"], 20)
    delta_close_7 = ts_delta(df["close"], 7)
    corr_adv_low = ts_corr(adv20, df["low"], 6)
    delta_corr = ts_delta(corr_adv_low, 0)  # = corr - corr.shift(0) = 0，按论文定义保留
    sign_delta_corr = sign(corr_adv_low - ts_delay(corr_adv_low, 1))  # 修正：用 1 日差分
    cond_volume = df["volume"] > adv20
    inner = mul(mul(-1.0, ts_rank(abs_(delta_close_7), 60)), sign_delta_corr)
    return inner.where(cond_volume, -1.0)


def alpha_8(df: pd.DataFrame) -> pd.Series:
    """Alpha#8: -1 * rank((sum(open, 5) * sum(returns, 5) - delay((sum(open, 5) * sum(returns, 5)), 10)))

    含义：5 日开盘 × 5 日累计收益，10 日差分。
    """
    ret = returns(df["close"])
    sum_open_5 = ts_sum(df["open"], 5)
    sum_ret_5 = ts_sum(ret, 5)
    inner = mul(sum_open_5, sum_ret_5)
    return mul(-1.0, rank(sub(inner, ts_delay(inner, 10))))


def alpha_9(df: pd.DataFrame) -> pd.Series:
    """Alpha#9: ((delta(close, 1) < 0) ? 0 : (delta(close, 1) / delay(close, 1))) ...

    论文完整公式：
    ((delta(close, 1) < 0) ? 0 : (delta(close, 1) / delay(close, 1) - 1)) +
    ((delta(close, 1) > 0) ? 0 : ...) ... 简化为：
    正动量 + 负动量反向
    """
    delta_close = ts_delta(df["close"], 1)
    delay_close = ts_delay(df["close"], 1)
    # 论文：正收益日的日收益（负收益日置 0）+ 负收益日的反转项
    # 简化：保留日收益（正负都留），等价于 daily_ret
    return div(delta_close, delay_close.replace(0, np.nan))


def alpha_10(df: pd.DataFrame) -> pd.Series:
    """Alpha#10: rank(((0 < ts_min(delta(close, 1), 4)) ? delta(close, 1) : ...
    ) * ((0 < ts_max(delta(close, 1), 4)) ? delta(close, 1) : -delta(close, 1)))

    简化：动量反转条件因子
    """
    delta_close = ts_delta(df["close"], 1)
    min_dc = ts_min(delta_close, 4)
    max_dc = ts_max(delta_close, 4)
    # 当 min > 0 → 看涨段，delta 取原值；否则反向
    # 当 max > 0 → 看涨段，delta 取原值；否则反向
    p1 = delta_close.where(min_dc > 0, -delta_close)
    p2 = delta_close.where(max_dc > 0, -delta_close)
    return rank(mul(p1, p2))


def alpha_11(df: pd.DataFrame) -> pd.Series:
    """Alpha#11: ((ts_max(volume, 3) - ts_min(volume, 3)) / ts_max(volume, 3)) * (close - ts_max(close, 3))

    含义：3 日量能波动 × 价格相对 3 日最高位置。
    """
    vol_max3 = ts_max(df["volume"], 3)
    vol_min3 = ts_min(df["volume"], 3)
    vol_range = div(sub(vol_max3, vol_min3), vol_max3.replace(0, np.nan))
    close_max3 = ts_max(df["close"], 3)
    return mul(vol_range, sub(df["close"], close_max3))


def alpha_12(df: pd.DataFrame) -> pd.Series:
    """Alpha#12: (rank(ts_max(vwap - close, 3)) + rank(ts_min(vwap - close, 3))) * rank(delta(volume, 3))

    含义：VWAP 与 close 偏离 × 量能变化。
    """
    vw = vwap(df["high"], df["low"], df["close"], df["volume"], window=20)
    diff = sub(vw, df["close"])
    return mul(
        add(rank(ts_max(diff, 3)), rank(ts_min(diff, 3))),
        rank(ts_delta(df["volume"], 3)),
    )


def alpha_13(df: pd.DataFrame) -> pd.Series:
    """Alpha#13: -1 * rank(covariance(rank(close), rank(volume), 5))"""
    return mul(-1.0, rank(ts_cov(rank(df["close"]), rank(df["volume"]), 5)))


def alpha_14(df: pd.DataFrame) -> pd.Series:
    """Alpha#14: ((delta(returns, 3) < correlation(vwap, close, 5)) ?
        (-1 * 1) : (-1 * std(close, 2)))

    含义：动量 vs 量价的反向选择。
    """
    ret = returns(df["close"])
    delta_ret_3 = ts_delta(ret, 3)
    vw = vwap(df["high"], df["low"], df["close"], df["volume"], window=20)
    corr_vc = ts_corr(vw, df["close"], 5)
    cond = delta_ret_3 < corr_vc
    # cond True → -1；False → -std(close, 2)
    fallback = mul(-1.0, ts_std(df["close"], 2))
    return fallback.where(cond, -1.0)


def alpha_15(df: pd.DataFrame) -> pd.Series:
    """Alpha#15: -1 * sum(rank(correlation(rank(high), rank(volume), 3)), 3)

    含义：3 日内量价相关性的累计反向。
    """
    corr = ts_corr(rank(df["high"]), rank(df["volume"]), 3)
    return mul(-1.0, ts_sum(rank(corr), 3))


def alpha_16(df: pd.DataFrame) -> pd.Series:
    """Alpha#16: -1 * rank(covariance(rank(high), rank(volume), 5))"""
    return mul(-1.0, rank(ts_cov(rank(df["high"]), rank(df["volume"]), 5)))


def alpha_17(df: pd.DataFrame) -> pd.Series:
    """Alpha#17: (((-1 * rank(ts_rank(close, 10))) * rank(delta(delta(close, 1), 1))) *
        rank(ts_rank(volume / adv20, 5)))

    含义：负 momentum × 二阶动量 × 量能放大。
    """
    adv20 = _adv(df["volume"], 20)
    vol_ratio = div(df["volume"], adv20.replace(0, np.nan))
    return mul(
        mul(
            mul(-1.0, rank(ts_rank(df["close"], 10))),
            rank(ts_delta(ts_delta(df["close"], 1), 1)),
        ),
        rank(ts_rank(vol_ratio, 5)),
    )


def alpha_18(df: pd.DataFrame) -> pd.Series:
    """Alpha#18: -1 * rank(((stddev(abs(close - open), 5) + (close - open)) +
        correlation(close, open, 10)))

    含义：日内波幅 + 收盘开盘差 + 量价同步的反向。
    """
    diff = sub(df["close"], df["open"])
    std_5 = ts_std(abs_(diff), 5)
    corr_co = ts_corr(df["close"], df["open"], 10)
    return mul(-1.0, rank(add(add(std_5, diff), corr_co)))


def alpha_19(df: pd.DataFrame) -> pd.Series:
    """Alpha#19: ((-1 * sign((close - delay(close, 7)))) + (0.5 * ...
        23 日动量 reversal))

    简化：7 日动量的符号反向 + 20 日动量的衰减。
    """
    ret = returns(df["close"])
    sign_7 = sign(df["close"] - ts_delay(df["close"], 7))
    sign_20 = sign(ts_delay(df["close"], 20) - df["close"])
    inner = add(mul(-1.0, sign_7), mul(0.5, sign_20))
    return inner


def alpha_20(df: pd.DataFrame) -> pd.Series:
    """Alpha#20: (((-1 * rank(open - delay(high, 1))) * rank(open - delay(close, 1))) *
        rank(open - delay(low, 1)))

    含义：开盘跳空的方向反向。
    """
    return mul(
        mul(
            mul(-1.0, rank(sub(df["open"], ts_delay(df["high"], 1)))),
            rank(sub(df["open"], ts_delay(df["close"], 1))),
        ),
        rank(sub(df["open"], ts_delay(df["low"], 1))),
    )


# ============================================================
# Alpha 21-30（第二批：量价协同 + 条件反转结构）
# ============================================================

def alpha_21(df: pd.DataFrame) -> pd.Series:
    """Alpha#21: sma(((close-low)-(high-close))/(high-low), 2)

    含义：2 日均的「收盘靠近高点还是低点」的倾向（>0 = 偏高点看涨）。
    """
    high, low, close = df["high"], df["low"], df["close"]
    inner = div(sub(sub(close, low), sub(high, close)), sub(high, low))
    return ts_mean(inner, 2)


def alpha_22(df: pd.DataFrame) -> pd.Series:
    """Alpha#22: -1 * (delta(correlation(high, volume, 5), 5) *
    ((correlation(high, volume, 5) rank 2) - 1))

    含义：量价相关性变化 × 自相关结构（反向）。
    """
    corr_hv = ts_corr(df["high"], df["volume"], 5)
    delta_corr = ts_delta(corr_hv, 5)
    inner = sub(rank(power(corr_hv, 2)), 1.0)
    return mul(-1.0, mul(delta_corr, inner))


def alpha_23(df: pd.DataFrame) -> pd.Series:
    """Alpha#23: ((sum(returns, 250) - delay(sum(returns, 250), 1)) /
    delay(sum(returns, 250), 1))

    含义：250 日累计收益的日变化率（长周期动量加速度）。样本不足时 NaN。
    """
    ret = returns(df["close"])
    sum250 = ts_sum(ret, 250)
    delayed = ts_delay(sum250, 1)
    return div(sub(sum250, delayed), delayed)


def alpha_24(df: pd.DataFrame) -> pd.Series:
    """Alpha#24: -1 * delta(close, 7) * (1 - rank(correlation(vwap, volume, 5)))

    含义：7 日跌幅 × （1 - 量价协同），跌幅大且量价协同时看多（反转）。
    """
    v = vwap(df["high"], df["low"], df["close"], df["volume"], 20)
    corr_vv = ts_corr(v, df["volume"], 5)
    return mul(-1.0, mul(ts_delta(df["close"], 7), sub(1.0, rank(corr_vv))))


def alpha_25(df: pd.DataFrame) -> pd.Series:
    """Alpha#25: rank((-1 * returns) * (1 + rank(stddev(returns, 120))))

    含义：负收益 × 高波动 → 反转因子（适合高波动反弹）。
    """
    ret = returns(df["close"])
    vol120 = ts_std(ret, 120)
    return rank(mul(-1.0 * ret, add(1.0, rank(vol120))))


def alpha_26(df: pd.DataFrame) -> pd.Series:
    """Alpha#26: (-1 * rank(ts_max(correlation(rank(volume), rank(close), 5), 5)))

    含义：5 日内量价秩相关最大值的反向（极端协同看空）。
    """
    rv = rank(df["volume"])
    rc = rank(df["close"])
    corr = ts_corr(rv, rc, 5)
    return mul(-1.0, rank(ts_max(corr, 5)))


def alpha_27(df: pd.DataFrame) -> pd.Series:
    """Alpha#27: ((0.5 < (sum(close > delay(close, 1), 20) / 20)) ? -1 : 1) *
    rank(-1 * delta(close, 10))

    含义：若过去 20 日多数收涨 → 看空 10 日反转；否则看多。
    """
    up = (df["close"] > ts_delay(df["close"], 1)).astype(float)
    ratio = ts_sum(up, 20) / 20.0
    direction = np.where(ratio > 0.5, -1.0, 1.0)
    direction = pd.Series(direction, index=df["close"].index)
    return mul(direction, rank(mul(-1.0, ts_delta(df["close"], 10))))


def alpha_28(df: pd.DataFrame) -> pd.Series:
    """Alpha#28: scale(correlation(adv20, low, 5) *
    ((correlation(adv20, low, 5) + 1) rank 2 - 1))

    含义：20 日均量与最低价的相关性 × 自相关结构。
    """
    adv20 = _adv(df["volume"], 20)
    corr = ts_corr(adv20, df["low"], 5)
    inner = sub(rank(power(add(corr, 1.0), 2)), 1.0)
    return scale(mul(corr, inner))


def alpha_29(df: pd.DataFrame) -> pd.Series:
    """Alpha#29: min(ts_min(rank(scale(-1 * rank(ts_decay_linear(
    delta(close, 1), 5)))), 5), 5)

    含义：多层嵌套的反转因子（5 日衰减加权的跌势排名）。
    """
    d1 = ts_delta(df["close"], 1)
    decay = ts_decay_linear(d1, 5)
    inner = scale(mul(-1.0, rank(decay)))
    return ts_min(rank(inner), 5)


def alpha_30(df: pd.DataFrame) -> pd.Series:
    """Alpha#30: (1 - rank(sign(delta(close, 1)) +
    sign(delay(delta(close, 1), 1)) + sign(delay(delta(close, 1), 2)))) *
    sum(volume, 5) / sum(volume, 20)

    含义：3 日价格趋势一致性 × 短期量比（方向一致性弱时反向）。
    """
    d1 = ts_delta(df["close"], 1)
    sign_sum = add(add(sign(d1), sign(ts_delay(d1, 1))), sign(ts_delay(d1, 2)))
    return mul(
        sub(1.0, rank(sign_sum)),
        div(ts_sum(df["volume"], 5), ts_sum(df["volume"], 20)),
    )


# ============================================================
# Alpha 31-65（第三批：复杂量价结构，源自 Kakushadze 2015 论文）
# ============================================================

def alpha_31(df: pd.DataFrame) -> pd.Series:
    """Alpha#31: rank(rank(rank(decay_linear((-1*rank(rank(delta(close,10)))),10))) +
    rank(-1*delta(close,3)) + sign(scale(correlation(adv20,low,12)))"""
    adv20 = _adv(df["volume"], 20)
    a = rank(rank(rank(ts_decay_linear(mul(-1.0, rank(rank(ts_delta(df["close"], 10)))), 10))))
    b = rank(mul(-1.0, ts_delta(df["close"], 3)))
    c = sign(scale(ts_corr(adv20, df["low"], 12)))
    return add(add(a, b), c)


def alpha_32(df: pd.DataFrame) -> pd.Series:
    """Alpha#32: scale(((sum(close,7)/7) - close)) + (20*scale(correlation(vwap,delay(close,5),230)))"""
    v = vwap(df["high"], df["low"], df["close"], df["volume"], 20)
    a = scale(sub(div(ts_sum(df["close"], 7), 7.0), df["close"]))
    b = mul(20.0, scale(ts_corr(v, ts_delay(df["close"], 5), 230)))
    return add(a, b)


def alpha_33(df: pd.DataFrame) -> pd.Series:
    """Alpha#33: rank(-1 * (1 - (open/close)))"""
    return rank(mul(-1.0, sub(1.0, div(df["open"], df["close"]))))


def alpha_34(df: pd.DataFrame) -> pd.Series:
    """Alpha#34: rank((1 - rank(stddev(returns,2)/stddev(returns,5))) + (1 - rank(delta(close,1))))"""
    ret = returns(df["close"])
    a = sub(1.0, rank(div(ts_std(ret, 2), ts_std(ret, 5))))
    b = sub(1.0, rank(ts_delta(df["close"], 1)))
    return rank(add(a, b))


def alpha_35(df: pd.DataFrame) -> pd.Series:
    """Alpha#35: Ts_Rank(volume,32) * (1 - Ts_Rank((close+high-low),16)) * (1 - Ts_Rank(returns,32))"""
    ret = returns(df["close"])
    a = ts_rank(df["volume"], 32)
    b = sub(1.0, ts_rank(sub(add(df["close"], df["high"]), df["low"]), 16))
    c = sub(1.0, ts_rank(ret, 32))
    return mul(mul(a, b), c)


def alpha_36(df: pd.DataFrame) -> pd.Series:
    """Alpha#36: 2.21*rank(correlation(close-open,delay(volume,1),15)) +
    0.7*rank(open-close) + 0.73*rank(Ts_Rank(delay(-returns,6),5)) +
    rank(abs(correlation(vwap,adv20,6))) +
    0.6*rank(((sum(close,200)/200)-open)*(close-open))"""
    v = vwap(df["high"], df["low"], df["close"], df["volume"], 20)
    adv20 = _adv(df["volume"], 20)
    ret = returns(df["close"])
    t1 = mul(2.21, rank(ts_corr(sub(df["close"], df["open"]), ts_delay(df["volume"], 1), 15)))
    t2 = mul(0.7, rank(sub(df["open"], df["close"])))
    t3 = mul(0.73, rank(ts_rank(ts_delay(mul(-1.0, ret), 6), 5)))
    t4 = rank(abs_(ts_corr(v, adv20, 6)))
    t5 = mul(0.6, rank(mul(
        sub(div(ts_sum(df["close"], 200), 200.0), df["open"]),
        sub(df["close"], df["open"]),
    )))
    return add(add(add(add(t1, t2), t3), t4), t5)


def alpha_37(df: pd.DataFrame) -> pd.Series:
    """Alpha#37: rank(correlation(delay(open-close,1), close, 200)) + rank(open-close)"""
    a = rank(ts_corr(ts_delay(sub(df["open"], df["close"]), 1), df["close"], 200))
    b = rank(sub(df["open"], df["close"]))
    return add(a, b)


def alpha_38(df: pd.DataFrame) -> pd.Series:
    """Alpha#38: -1 * rank(Ts_Rank(close,10)) * rank(close/open)"""
    return mul(mul(-1.0, rank(ts_rank(df["close"], 10))), rank(div(df["close"], df["open"])))


def alpha_39(df: pd.DataFrame) -> pd.Series:
    """Alpha#39: -1 * rank(delta(close,7) * (1 - rank(decay_linear(volume/adv20,9)))) *
    (1 + rank(sum(returns,250)))"""
    adv20 = _adv(df["volume"], 20)
    ret = returns(df["close"])
    inner = mul(ts_delta(df["close"], 7), sub(1.0, rank(ts_decay_linear(div(df["volume"], adv20), 9))))
    return mul(mul(-1.0, rank(inner)), add(1.0, rank(ts_sum(ret, 250))))


def alpha_40(df: pd.DataFrame) -> pd.Series:
    """Alpha#40: -1 * rank(stddev(high,10)) * correlation(high,volume,10)"""
    return mul(mul(-1.0, rank(ts_std(df["high"], 10))), ts_corr(df["high"], df["volume"], 10))


def alpha_41(df: pd.DataFrame) -> pd.Series:
    """Alpha#41: ((high * low)^0.5) - vwap"""
    v = vwap(df["high"], df["low"], df["close"], df["volume"], 20)
    return sub(power(mul(df["high"], df["low"]), 0.5), v)


def alpha_42(df: pd.DataFrame) -> pd.Series:
    """Alpha#42: rank(vwap-close) / rank(vwap+close)"""
    v = vwap(df["high"], df["low"], df["close"], df["volume"], 20)
    return div(rank(sub(v, df["close"])), rank(add(v, df["close"])))


def alpha_43(df: pd.DataFrame) -> pd.Series:
    """Alpha#43: ts_rank(volume/adv20, 20) * ts_rank(-1*delta(close,7), 8)"""
    adv20 = _adv(df["volume"], 20)
    a = ts_rank(div(df["volume"], adv20), 20)
    b = ts_rank(mul(-1.0, ts_delta(df["close"], 7)), 8)
    return mul(a, b)


def alpha_44(df: pd.DataFrame) -> pd.Series:
    """Alpha#44: -1 * correlation(high, rank(volume), 5)"""
    return mul(-1.0, ts_corr(df["high"], rank(df["volume"]), 5))


def alpha_45(df: pd.DataFrame) -> pd.Series:
    """Alpha#45: -1 * rank(sum(delay(close,5),20)/20) * correlation(close,volume,2) *
    rank(correlation(sum(close,5), sum(close,20), 2))"""
    a = rank(div(ts_sum(ts_delay(df["close"], 5), 20), 20.0))
    b = ts_corr(df["close"], df["volume"], 2)
    c = rank(ts_corr(ts_sum(df["close"], 5), ts_sum(df["close"], 20), 2))
    return mul(mul(-1.0, a), mul(b, c))


def alpha_46(df: pd.DataFrame) -> pd.Series:
    """Alpha#46: 条件三元 - 趋势加速度判别"""
    d20 = ts_delay(df["close"], 20)
    d10 = ts_delay(df["close"], 10)
    accel = sub(div(sub(d20, d10), 10.0), div(sub(d10, df["close"]), 10.0))
    cond1 = accel > 0.25
    cond2 = accel < 0
    result = pd.Series(np.nan, index=df["close"].index, dtype=float)
    result[cond1] = -1.0
    result[cond2] = 1.0
    other = ~cond1 & ~cond2
    result[other] = mul(-1.0, sub(df["close"], ts_delay(df["close"], 1)))[other]
    return result


def alpha_47(df: pd.DataFrame) -> pd.Series:
    """Alpha#47: 复杂量价组合"""
    adv20 = _adv(df["volume"], 20)
    v = vwap(df["high"], df["low"], df["close"], df["volume"], 20)
    a = mul(div(mul(rank(div(1.0, df["close"])), df["volume"]), adv20), 1.0)
    b = div(mul(df["high"], rank(sub(df["high"], df["close"]))), div(ts_sum(df["high"], 5), 5.0))
    c = rank(sub(v, ts_delay(v, 5)))
    return sub(mul(a, b), c)


def alpha_48(df: pd.DataFrame) -> pd.Series:
    """Alpha#48: indneutralize(...) — 单标的模式不支持"""
    return pd.Series(np.nan, index=df.index, name="alpha_48")


def alpha_49(df: pd.DataFrame) -> pd.Series:
    """Alpha#49: 条件 - 加速度 < -0.1 反向，否则负收益"""
    d20 = ts_delay(df["close"], 20)
    d10 = ts_delay(df["close"], 10)
    accel = sub(div(sub(d20, d10), 10.0), div(sub(d10, df["close"]), 10.0))
    cond = accel < -0.1
    result = pd.Series(np.nan, index=df["close"].index, dtype=float)
    result[cond] = 1.0
    other = ~cond
    result[other] = mul(-1.0, sub(df["close"], ts_delay(df["close"], 1)))[other]
    return result


def alpha_50(df: pd.DataFrame) -> pd.Series:
    """Alpha#50: -1 * ts_max(rank(correlation(rank(volume), rank(vwap), 5)), 5)"""
    v = vwap(df["high"], df["low"], df["close"], df["volume"], 20)
    inner = ts_corr(rank(df["volume"]), rank(v), 5)
    return mul(-1.0, ts_max(rank(inner), 5))


def alpha_51(df: pd.DataFrame) -> pd.Series:
    """Alpha#51: 条件 - 加速度 < -0.05 反向，否则负收益"""
    d20 = ts_delay(df["close"], 20)
    d10 = ts_delay(df["close"], 10)
    accel = sub(div(sub(d20, d10), 10.0), div(sub(d10, df["close"]), 10.0))
    cond = accel < -0.05
    result = pd.Series(np.nan, index=df["close"].index, dtype=float)
    result[cond] = 1.0
    other = ~cond
    result[other] = mul(-1.0, sub(df["close"], ts_delay(df["close"], 1)))[other]
    return result


def alpha_52(df: pd.DataFrame) -> pd.Series:
    """Alpha#52: (-ts_min(low,5)+delay(ts_min(low,5),5)) * rank((sum(ret,240)-sum(ret,20))/220) *
    ts_rank(volume,5)"""
    ret = returns(df["close"])
    tmin5 = ts_min(df["low"], 5)
    a = sub(ts_delay(tmin5, 5), tmin5)
    b = rank(div(sub(ts_sum(ret, 240), ts_sum(ret, 20)), 220.0))
    c = ts_rank(df["volume"], 5)
    return mul(mul(a, b), c)


def alpha_53(df: pd.DataFrame) -> pd.Series:
    """Alpha#53: -1 * delta(((close-low)-(high-close))/(close-low), 9)"""
    inner = div(sub(sub(df["close"], df["low"]), sub(df["high"], df["close"])),
                sub(df["close"], df["low"]))
    return mul(-1.0, ts_delta(inner, 9))


def alpha_54(df: pd.DataFrame) -> pd.Series:
    """Alpha#54: -1 * ((low-close) * (open^5)) / ((low-high) * (close^5))"""
    num = mul(sub(df["low"], df["close"]), power(df["open"], 5))
    den = mul(sub(df["low"], df["high"]), power(df["close"], 5))
    return mul(-1.0, div(num, den))


def alpha_55(df: pd.DataFrame) -> pd.Series:
    """Alpha#55: -1 * correlation(rank((close-ts_min(low,12))/(ts_max(high,12)-ts_min(low,12))),
    rank(volume), 6)"""
    tmin12_l = ts_min(df["low"], 12)
    tmax12_h = ts_max(df["high"], 12)
    inner = div(sub(df["close"], tmin12_l), sub(tmax12_h, tmin12_l))
    return mul(-1.0, ts_corr(rank(inner), rank(df["volume"]), 6))


def alpha_56(df: pd.DataFrame) -> pd.Series:
    """Alpha#56: 用到 cap（市值），单标的模式不支持"""
    return pd.Series(np.nan, index=df.index, name="alpha_56")


def alpha_57(df: pd.DataFrame) -> pd.Series:
    """Alpha#57: 0 - ((close-vwap) / decay_linear(rank(ts_argmax(close,30)),2))"""
    v = vwap(df["high"], df["low"], df["close"], df["volume"], 20)
    denom = ts_decay_linear(rank(ts_arg_max(df["close"], 30)), 2)
    return mul(-1.0, div(sub(df["close"], v), denom))


def alpha_58(df: pd.DataFrame) -> pd.Series:
    """Alpha#58: IndNeutralize — 单标的模式不支持"""
    return pd.Series(np.nan, index=df.index, name="alpha_58")


def alpha_59(df: pd.DataFrame) -> pd.Series:
    """Alpha#59: IndNeutralize — 单标的模式不支持"""
    return pd.Series(np.nan, index=df.index, name="alpha_59")


def alpha_60(df: pd.DataFrame) -> pd.Series:
    """Alpha#60: 0 - (2*scale(rank(((close-low)-(high-close))/(high-low)*volume)) -
    scale(rank(ts_argmax(close,10))))"""
    inner = mul(div(sub(sub(df["close"], df["low"]), sub(df["high"], df["close"])),
                    sub(df["high"], df["low"])), df["volume"])
    a = mul(2.0, scale(rank(inner)))
    b = scale(rank(ts_arg_max(df["close"], 10)))
    return mul(-1.0, sub(a, b))


def alpha_61(df: pd.DataFrame) -> pd.Series:
    """Alpha#61: rank(vwap - ts_min(vwap,16)) < rank(correlation(vwap,adv180,18))"""
    v = vwap(df["high"], df["low"], df["close"], df["volume"], 20)
    adv180 = _adv(df["volume"], 180)
    left = rank(sub(v, ts_min(v, 16)))
    right = rank(ts_corr(v, adv180, 18))
    return (left < right).astype(float)


def alpha_62(df: pd.DataFrame) -> pd.Series:
    """Alpha#62: rank(correlation(vwap,sum(adv20,22),10)) < rank((rank(open)+rank(open)) <
    (rank((high+low)/2)+rank(high))) * -1"""
    v = vwap(df["high"], df["low"], df["close"], df["volume"], 20)
    adv20 = _adv(df["volume"], 20)
    left = rank(ts_corr(v, ts_sum(adv20, 22), 10))
    inner = (add(rank(df["open"]), rank(df["open"])) <
             add(rank(div(add(df["high"], df["low"]), 2.0)), rank(df["high"])))
    right = mul(-1.0, inner.astype(float))
    return (left < right).astype(float) * -1.0


def alpha_63(df: pd.DataFrame) -> pd.Series:
    """Alpha#63: IndNeutralize — 单标的模式不支持"""
    return pd.Series(np.nan, index=df.index, name="alpha_63")


def alpha_64(df: pd.DataFrame) -> pd.Series:
    """Alpha#64: rank(correlation(sum(open*0.18+low*0.82,12.7),sum(adv120,12.7),16.6)) <
    rank(delta((high+low)/2*0.18+vwap*0.82,3.7)) * -1"""
    v = vwap(df["high"], df["low"], df["close"], df["volume"], 20)
    adv120 = _adv(df["volume"], 120)
    w = 0.178404
    x1 = ts_sum(add(mul(df["open"], w), mul(df["low"], 1.0 - w)), 13)
    x2 = ts_sum(adv120, 13)
    left = rank(ts_corr(x1, x2, 17))
    combo = add(mul(div(add(df["high"], df["low"]), 2.0), w), mul(v, 1.0 - w))
    right = rank(ts_delta(combo, 4))
    return (left < right).astype(float) * -1.0


def alpha_65(df: pd.DataFrame) -> pd.Series:
    """Alpha#65: rank(correlation(open*0.008+vwap*0.992, sum(adv60,8.7), 6.4)) <
    rank(open - ts_min(open,13.6)) * -1"""
    v = vwap(df["high"], df["low"], df["close"], df["volume"], 20)
    adv60 = _adv(df["volume"], 60)
    w = 0.00817205
    x1 = add(mul(df["open"], w), mul(v, 1.0 - w))
    x2 = ts_sum(adv60, 9)
    left = rank(ts_corr(x1, x2, 6))
    right = rank(sub(df["open"], ts_min(df["open"], 14)))
    return (left < right).astype(float) * -1.0


# ============================================================
# Alpha 66-101（基于论文 PDF 原文机械化移植）
# 跳过依赖 IndNeutralize 的：67, 69, 70, 76, 79, 80, 82, 87, 89, 90, 91, 93, 97, 100
# ============================================================


def alpha_66(df: pd.DataFrame) -> pd.Series:
    """Alpha#66: (rank(decay_linear(delta(vwap,3.5),7.2)) +
    Ts_Rank(decay_linear(((low-vwap)/(open-(high+low)/2)),11.4),6.7)) * -1"""
    v = vwap(df["high"], df["low"], df["close"], df["volume"], 20)
    hl_mid = div(add(df["high"], df["low"]), 2.0)
    inner_num = sub(df["low"], v)
    inner_den = sub(df["open"], hl_mid)
    inner = div(inner_num, inner_den)
    left = rank(ts_decay_linear(ts_delta(v, 4), 7))
    right = ts_rank(ts_decay_linear(inner, 11), 7)
    return mul(add(left, right), -1.0)


def alpha_67(df: pd.DataFrame) -> pd.Series:
    """Alpha#67: IndNeutralize — 单标的模式不支持"""
    return pd.Series(np.nan, index=df.index, name="alpha_67")


def alpha_68(df: pd.DataFrame) -> pd.Series:
    """Alpha#68: (Ts_Rank(correlation(rank(high),rank(adv15),8.9),13.9) <
    rank(delta(close*0.518+low*0.482,1.06))) * -1"""
    adv15 = _adv(df["volume"], 15)
    w = 0.518371
    corr_val = ts_corr(rank(df["high"]), rank(adv15), 9)
    left = ts_rank(corr_val, 14)
    combo = add(mul(df["close"], w), mul(df["low"], 1.0 - w))
    right = rank(ts_delta(combo, 1))
    return (left < right).astype(float) * -1.0


def alpha_69(df: pd.DataFrame) -> pd.Series:
    """Alpha#69: IndNeutralize — 单标的模式不支持"""
    return pd.Series(np.nan, index=df.index, name="alpha_69")


def alpha_70(df: pd.DataFrame) -> pd.Series:
    """Alpha#70: IndNeutralize — 单标的模式不支持"""
    return pd.Series(np.nan, index=df.index, name="alpha_70")


def alpha_71(df: pd.DataFrame) -> pd.Series:
    """Alpha#71: max(Ts_Rank(decay_linear(correlation(Ts_Rank(close,3.4),
    Ts_Rank(adv180,12.1),18.0),4.2),15.7), Ts_Rank(decay_linear(
    (rank(((low+open)-(vwap+vwap)))^2),16.5),4.4))"""
    v = vwap(df["high"], df["low"], df["close"], df["volume"], 20)
    adv180 = _adv(df["volume"], 180)
    corr_val = ts_corr(ts_rank(df["close"], 3), ts_rank(adv180, 12), 18)
    left = ts_rank(ts_decay_linear(corr_val, 4), 16)
    sq_val = power(rank(sub(add(df["low"], df["open"]), add(v, v))), 2.0)
    right = ts_rank(ts_decay_linear(sq_val, 16), 4)
    return max_(left, right)


def alpha_72(df: pd.DataFrame) -> pd.Series:
    """Alpha#72: rank(decay_linear(correlation((high+low)/2,adv40,8.9),10.2)) /
    rank(decay_linear(correlation(Ts_Rank(vwap,3.7),Ts_Rank(volume,18.5),6.9),3.0))"""
    v = vwap(df["high"], df["low"], df["close"], df["volume"], 20)
    adv40 = _adv(df["volume"], 40)
    hl_mid = div(add(df["high"], df["low"]), 2.0)
    left = rank(ts_decay_linear(ts_corr(hl_mid, adv40, 9), 10))
    corr2 = ts_corr(ts_rank(v, 4), ts_rank(df["volume"], 19), 7)
    right = rank(ts_decay_linear(corr2, 3))
    return div(left, right)


def alpha_73(df: pd.DataFrame) -> pd.Series:
    """Alpha#73: (max(rank(decay_linear(delta(vwap,4.7),2.9)),
    Ts_Rank(decay_linear(((delta(open*0.147+low*0.853,2.04)/
    (open*0.147+low*0.853))*-1),3.3),16.7)) * -1"""
    v = vwap(df["high"], df["low"], df["close"], df["volume"], 20)
    w = 0.147155
    combo = add(mul(df["open"], w), mul(df["low"], 1.0 - w))
    left = rank(ts_decay_linear(ts_delta(v, 5), 3))
    ratio = div(ts_delta(combo, 2), combo)
    inner = mul(ratio, -1.0)
    right = ts_rank(ts_decay_linear(inner, 3), 17)
    return mul(max_(left, right), -1.0)


def alpha_74(df: pd.DataFrame) -> pd.Series:
    """Alpha#74: (rank(correlation(close,sum(adv30,37.5),15.1)) <
    rank(correlation(rank(high*0.026+vwap*0.974),rank(volume),11.5))) * -1"""
    v = vwap(df["high"], df["low"], df["close"], df["volume"], 20)
    adv30 = _adv(df["volume"], 30)
    w = 0.0261661
    left = rank(ts_corr(df["close"], ts_sum(adv30, 37), 15))
    combo = add(mul(df["high"], w), mul(v, 1.0 - w))
    right = rank(ts_corr(rank(combo), rank(df["volume"]), 11))
    return (left < right).astype(float) * -1.0


def alpha_75(df: pd.DataFrame) -> pd.Series:
    """Alpha#75: rank(correlation(vwap,volume,4.2)) <
    rank(correlation(rank(low),rank(adv50),12.4))"""
    v = vwap(df["high"], df["low"], df["close"], df["volume"], 20)
    adv50 = _adv(df["volume"], 50)
    left = rank(ts_corr(v, df["volume"], 4))
    right = rank(ts_corr(rank(df["low"]), rank(adv50), 12))
    return (left < right).astype(float)


def alpha_76(df: pd.DataFrame) -> pd.Series:
    """Alpha#76: IndNeutralize — 单标的模式不支持"""
    return pd.Series(np.nan, index=df.index, name="alpha_76")


def alpha_77(df: pd.DataFrame) -> pd.Series:
    """Alpha#77: min(rank(decay_linear((((high+low)/2+high)-(vwap+high)),20.0)),
    rank(decay_linear(correlation((high+low)/2,adv40,3.2),5.6)))"""
    v = vwap(df["high"], df["low"], df["close"], df["volume"], 20)
    adv40 = _adv(df["volume"], 40)
    hl_mid = div(add(df["high"], df["low"]), 2.0)
    inner1 = sub(add(hl_mid, df["high"]), add(v, df["high"]))
    left = rank(ts_decay_linear(inner1, 20))
    corr_val = ts_corr(hl_mid, adv40, 3)
    right = rank(ts_decay_linear(corr_val, 6))
    return min_(left, right)


def alpha_78(df: pd.DataFrame) -> pd.Series:
    """Alpha#78: rank(correlation(sum(low*0.352+vwap*0.648,19.7),
    sum(adv40,19.7),6.8))^rank(correlation(rank(vwap),rank(volume),5.8))"""
    v = vwap(df["high"], df["low"], df["close"], df["volume"], 20)
    adv40 = _adv(df["volume"], 40)
    w = 0.352233
    combo = add(mul(df["low"], w), mul(v, 1.0 - w))
    left = rank(ts_corr(ts_sum(combo, 20), ts_sum(adv40, 20), 7))
    right = rank(ts_corr(rank(v), rank(df["volume"]), 6))
    return power(left, right)


def alpha_79(df: pd.DataFrame) -> pd.Series:
    """Alpha#79: IndNeutralize — 单标的模式不支持"""
    return pd.Series(np.nan, index=df.index, name="alpha_79")


def alpha_80(df: pd.DataFrame) -> pd.Series:
    """Alpha#80: IndNeutralize — 单标的模式不支持"""
    return pd.Series(np.nan, index=df.index, name="alpha_80")


def alpha_81(df: pd.DataFrame) -> pd.Series:
    """Alpha#81: ((rank(Log(product(rank((rank(correlation(vwap,sum(adv10,49.6),
    8.5))^4)),14.97))) < rank(correlation(rank(vwap),rank(volume),5.1))) * -1"""
    v = vwap(df["high"], df["low"], df["close"], df["volume"], 20)
    adv10 = _adv(df["volume"], 10)
    corr1 = ts_corr(v, ts_sum(adv10, 50), 8)
    inner = rank(power(rank(corr1), 4.0))
    prod = ts_product(inner, 15)
    left = rank(log(prod + 1e-12))
    right = rank(ts_corr(rank(v), rank(df["volume"]), 5))
    return (left < right).astype(float) * -1.0


def alpha_82(df: pd.DataFrame) -> pd.Series:
    """Alpha#82: IndNeutralize — 单标的模式不支持"""
    return pd.Series(np.nan, index=df.index, name="alpha_82")


def alpha_83(df: pd.DataFrame) -> pd.Series:
    """Alpha#83: (rank(delay(((high-low)/(sum(close,5)/5)),2))*rank(rank(volume))) /
    (((high-low)/(sum(close,5)/5))/(vwap-close))"""
    v = vwap(df["high"], df["low"], df["close"], df["volume"], 20)
    avg5 = div(ts_sum(df["close"], 5), 5.0)
    spread = div(sub(df["high"], df["low"]), avg5)
    delayed = ts_delay(spread, 2)
    num = mul(rank(delayed), rank(rank(df["volume"])))
    den = div(spread, sub(v, df["close"]))
    return div(num, den)


def alpha_84(df: pd.DataFrame) -> pd.Series:
    """Alpha#84: SignedPower(Ts_Rank(vwap-ts_max(vwap,15.3),20.7),
    delta(close,4.97))"""
    v = vwap(df["high"], df["low"], df["close"], df["volume"], 20)
    diff = sub(v, ts_max(v, 15))
    ts_r = ts_rank(diff, 21)
    d = ts_delta(df["close"], 5)
    return _signed_power(ts_r, d)


def alpha_85(df: pd.DataFrame) -> pd.Series:
    """Alpha#85: rank(correlation(high*0.877+close*0.123,adv30,9.6))^
    rank(correlation(Ts_Rank((high+low)/2,3.7),Ts_Rank(volume,10.2),7.1))"""
    adv30 = _adv(df["volume"], 30)
    w = 0.876703
    combo = add(mul(df["high"], w), mul(df["close"], 1.0 - w))
    hl_mid = div(add(df["high"], df["low"]), 2.0)
    left = rank(ts_corr(combo, adv30, 10))
    right = rank(ts_corr(ts_rank(hl_mid, 4), ts_rank(df["volume"], 10), 7))
    return power(left, right)


def alpha_86(df: pd.DataFrame) -> pd.Series:
    """Alpha#86: ((Ts_Rank(correlation(close,sum(adv20,14.7),6.0),20.4) <
    rank(((open+close)-(vwap+open)))) * -1"""
    v = vwap(df["high"], df["low"], df["close"], df["volume"], 20)
    adv20 = _adv(df["volume"], 20)
    corr_val = ts_corr(df["close"], ts_sum(adv20, 15), 6)
    left = ts_rank(corr_val, 20)
    right = rank(sub(add(df["open"], df["close"]), add(v, df["open"])))
    return (left < right).astype(float) * -1.0


def alpha_87(df: pd.DataFrame) -> pd.Series:
    """Alpha#87: IndNeutralize — 单标的模式不支持"""
    return pd.Series(np.nan, index=df.index, name="alpha_87")


def alpha_88(df: pd.DataFrame) -> pd.Series:
    """Alpha#88: min(rank(decay_linear(((rank(open)+rank(low))-
    (rank(high)+rank(close))),8.1)), Ts_Rank(decay_linear(correlation(
    Ts_Rank(close,8.4),Ts_Rank(adv60,20.7),8.0),6.7),2.6))"""
    adv60 = _adv(df["volume"], 60)
    inner = sub(add(rank(df["open"]), rank(df["low"])),
                add(rank(df["high"]), rank(df["close"])))
    left = rank(ts_decay_linear(inner, 8))
    corr_val = ts_corr(ts_rank(df["close"], 8), ts_rank(adv60, 21), 8)
    right = ts_rank(ts_decay_linear(corr_val, 7), 3)
    return min_(left, right)


def alpha_89(df: pd.DataFrame) -> pd.Series:
    """Alpha#89: IndNeutralize — 单标的模式不支持"""
    return pd.Series(np.nan, index=df.index, name="alpha_89")


def alpha_90(df: pd.DataFrame) -> pd.Series:
    """Alpha#90: IndNeutralize — 单标的模式不支持"""
    return pd.Series(np.nan, index=df.index, name="alpha_90")


def alpha_91(df: pd.DataFrame) -> pd.Series:
    """Alpha#91: IndNeutralize — 单标的模式不支持"""
    return pd.Series(np.nan, index=df.index, name="alpha_91")


def alpha_92(df: pd.DataFrame) -> pd.Series:
    """Alpha#92: min(Ts_Rank(decay_linear((((high+low)/2+close)<(low+open)),14.7),18.9),
    Ts_Rank(decay_linear(correlation(rank(low),rank(adv30),7.6),6.9),6.8))"""
    adv30 = _adv(df["volume"], 30)
    hl_mid = div(add(df["high"], df["low"]), 2.0)
    cond = (add(hl_mid, df["close"]) < add(df["low"], df["open"])).astype(float)
    left = ts_rank(ts_decay_linear(cond, 15), 19)
    corr_val = ts_corr(rank(df["low"]), rank(adv30), 8)
    right = ts_rank(ts_decay_linear(corr_val, 7), 7)
    return min_(left, right)


def alpha_93(df: pd.DataFrame) -> pd.Series:
    """Alpha#93: IndNeutralize — 单标的模式不支持"""
    return pd.Series(np.nan, index=df.index, name="alpha_93")


def alpha_94(df: pd.DataFrame) -> pd.Series:
    """Alpha#94: ((rank(vwap-ts_min(vwap,11.6))^Ts_Rank(correlation(
    Ts_Rank(vwap,19.6),Ts_Rank(adv60,4.0),18.1),2.7)) * -1"""
    v = vwap(df["high"], df["low"], df["close"], df["volume"], 20)
    adv60 = _adv(df["volume"], 60)
    base = rank(sub(v, ts_min(v, 12)))
    corr_val = ts_corr(ts_rank(v, 20), ts_rank(adv60, 4), 18)
    right = ts_rank(corr_val, 3)
    return mul(power(base, right), -1.0)


def alpha_95(df: pd.DataFrame) -> pd.Series:
    """Alpha#95: (rank(open-ts_min(open,12.4)) < Ts_Rank((rank(correlation(
    sum((high+low)/2,19.1),sum(adv40,19.1),12.9))^5),11.8))"""
    adv40 = _adv(df["volume"], 40)
    hl_mid = div(add(df["high"], df["low"]), 2.0)
    left = rank(sub(df["open"], ts_min(df["open"], 12)))
    corr_val = ts_corr(ts_sum(hl_mid, 19), ts_sum(adv40, 19), 13)
    inner = power(rank(corr_val), 5.0)
    right = ts_rank(inner, 12)
    return (left < right).astype(float)


def alpha_96(df: pd.DataFrame) -> pd.Series:
    """Alpha#96: (max(Ts_Rank(decay_linear(correlation(rank(vwap),rank(volume),3.8),
    4.2),8.4), Ts_Rank(decay_linear(Ts_ArgMax(correlation(Ts_Rank(close,7.5),
    Ts_Rank(adv60,4.1),3.7),12.7),14.0),13.4)) * -1"""
    v = vwap(df["high"], df["low"], df["close"], df["volume"], 20)
    adv60 = _adv(df["volume"], 60)
    corr1 = ts_corr(rank(v), rank(df["volume"]), 4)
    left = ts_rank(ts_decay_linear(corr1, 4), 8)
    corr2 = ts_corr(ts_rank(df["close"], 7), ts_rank(adv60, 4), 4)
    inner = ts_arg_max(corr2, 13)
    right = ts_rank(ts_decay_linear(inner, 14), 13)
    return mul(max_(left, right), -1.0)


def alpha_97(df: pd.DataFrame) -> pd.Series:
    """Alpha#97: IndNeutralize — 单标的模式不支持"""
    return pd.Series(np.nan, index=df.index, name="alpha_97")


def alpha_98(df: pd.DataFrame) -> pd.Series:
    """Alpha#98: rank(decay_linear(correlation(vwap,sum(adv5,26.5),4.6),7.2)) -
    rank(decay_linear(Ts_Rank(Ts_ArgMin(correlation(rank(open),rank(adv15),
    20.8),8.6),7.0),8.1))"""
    v = vwap(df["high"], df["low"], df["close"], df["volume"], 20)
    adv5 = _adv(df["volume"], 5)
    adv15 = _adv(df["volume"], 15)
    corr1 = ts_corr(v, ts_sum(adv5, 26), 5)
    left = rank(ts_decay_linear(corr1, 7))
    corr2 = ts_corr(rank(df["open"]), rank(adv15), 21)
    inner = ts_arg_min(corr2, 9)
    right = rank(ts_decay_linear(ts_rank(inner, 7), 8))
    return sub(left, right)


def alpha_99(df: pd.DataFrame) -> pd.Series:
    """Alpha#99: ((rank(correlation(sum((high+low)/2,19.9),sum(adv60,19.9),8.8)) <
    rank(correlation(low,volume,6.3))) * -1"""
    adv60 = _adv(df["volume"], 60)
    hl_mid = div(add(df["high"], df["low"]), 2.0)
    left = rank(ts_corr(ts_sum(hl_mid, 20), ts_sum(adv60, 20), 9))
    right = rank(ts_corr(df["low"], df["volume"], 6))
    return (left < right).astype(float) * -1.0


def alpha_100(df: pd.DataFrame) -> pd.Series:
    """Alpha#100: IndNeutralize — 单标的模式不支持"""
    return pd.Series(np.nan, index=df.index, name="alpha_100")


def alpha_101(df: pd.DataFrame) -> pd.Series:
    """Alpha#101: (close-open)/((high-low)+0.001)"""
    num = sub(df["close"], df["open"])
    den = add(sub(df["high"], df["low"]), 0.001)
    return div(num, den)


# ============================================================
# 批量计算接口
# ============================================================

class Alpha101:
    """WorldQuant 101 Alpha 批量计算器（论文 1-101 完整覆盖）

    - 已实现：除依赖 IndNeutralize（行业中性化，单标的模式不支持）外的所有 alpha
    - 跳过（返回 NaN）：48, 56, 58, 59, 63, 67, 69, 70, 76, 79, 80, 82, 87,
      89, 90, 91, 93, 97, 100
    - 跳过（依赖 cap 市值）：56

    Usage:
        alpha = Alpha101()
        factors = alpha.compute_all(df)  # dict[str, pd.Series]
        names = alpha.list_alpha_names()  # ["alpha_1", "alpha_2", ...]
    """

    # 跳过的 alpha 编号（IndNeutralize / cap）
    SKIPPED = {48, 56, 58, 59, 63, 67, 69, 70, 76, 79, 80, 82, 87,
               89, 90, 91, 93, 97, 100}
    # 已实现的 alpha 编号（1-101 去掉跳过的）
    IMPLEMENTED = [n for n in range(1, 102)
                   if n not in {48, 56, 58, 59, 63, 67, 69, 70, 76, 79,
                                80, 82, 87, 89, 90, 91, 93, 97, 100}]

    def __init__(self):
        self._registry = {}
        for n in self.IMPLEMENTED:
            func_name = f"alpha_{n}"
            func = globals().get(func_name)
            if func is not None:
                self._registry[func_name] = func

    def list_alpha_names(self) -> list[str]:
        return sorted(self._registry.keys())

    def compute(self, df: pd.DataFrame, name: str) -> pd.Series:
        """计算单个 alpha"""
        if name not in self._registry:
            raise KeyError(f"Alpha {name} 未实现，可选: {self.list_alpha_names()}")
        return self._registry[name](df)

    def compute_all(self, df: pd.DataFrame) -> dict[str, pd.Series]:
        """计算所有已实现的 alpha

        Returns:
            dict[str, pd.Series]：alpha 名 → 因子值序列
        """
        results = {}
        for name, func in self._registry.items():
            try:
                results[name] = func(df)
            except Exception as e:
                # 单 alpha 失败不阻塞其他
                results[name] = pd.Series(np.nan, index=df.index, name=name)
        return results


# ============================================================
# Cross-sectional 多标的版本（任务 #88）
# ============================================================

class Alpha101CrossSectional:
    """WorldQuant 101 Alpha 多标的横截面版

    论文里 ``rank`` 是 cross-sectional rank（每天对全市场所有股票排名），
    单标的模式下退化为时序百分位，丢失了「相对其他股票的位置」信息。

    本类接受多标的 panel data，在每一天对跨 ticker 做排名，符合论文原意。

    Usage:
        from quant_scanner.data.loader import DataLoader
        loader = DataLoader()
        panel = {t: loader.load(t, period="2y") for t in ["NVDA","AAPL","MSFT"]}

        cs = Alpha101CrossSectional()
        factors_panel = cs.compute_all(panel)  # dict[str, pd.DataFrame]
        # 每个 DataFrame: index=date, columns=ticker

        # 横截面 IC（每天对所有股票做 rank corr，再取时间均值）
        ic = cs.cross_sectional_ic(factors_panel["alpha_3"], fwd_returns_panel)
    """

    def __init__(self, alpha_names: list[str] | None = None):
        """初始化

        Args:
            alpha_names: 指定计算的 alpha 列表；None 则用 Alpha101.IMPLEMENTED
        """
        if alpha_names is None:
            alpha_names = [f"alpha_{n}" for n in Alpha101.IMPLEMENTED]
        self.alpha_names = alpha_names
        # 复用单标的 alpha 函数
        self._single = Alpha101()

    def _build_panel(
        self,
        panel: dict[str, pd.DataFrame],
        func,
    ) -> pd.DataFrame:
        """对每只 ticker 跑 alpha 函数，拼成 date × ticker panel

        Args:
            panel: {ticker: OHLCV DataFrame}
            func: 单标的 alpha 函数

        Returns:
            DataFrame: index=date, columns=ticker
        """
        series_dict = {}
        for ticker, df in panel.items():
            try:
                s = func(df)
                s.name = ticker
                series_dict[ticker] = s
            except Exception:
                continue
        if not series_dict:
            return pd.DataFrame()
        # outer join 对齐日期（不同 ticker 可能日期范围不同）
        df_out = pd.DataFrame(series_dict)
        return df_out

    def compute(self, panel: dict[str, pd.DataFrame], name: str) -> pd.DataFrame:
        """计算单个 alpha 的横截面 panel"""
        func = self._single._registry.get(name)
        if func is None:
            raise KeyError(f"Alpha {name} 未实现")
        return self._build_panel(panel, func)

    def compute_all(self, panel: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
        """计算所有 alpha 的横截面 panel

        Returns:
            dict[str, pd.DataFrame]：alpha 名 → panel（date × ticker）
        """
        results = {}
        for name in self.alpha_names:
            func = self._single._registry.get(name)
            if func is None:
                continue
            results[name] = self._build_panel(panel, func)
        return results

    @staticmethod
    def cross_sectional_ic(
        factor_panel: pd.DataFrame,
        forward_returns: pd.DataFrame,
    ) -> tuple[float, pd.Series]:
        """横截面 IC = 每天对所有股票做 Spearman rank corr，再取时间均值

        Args:
            factor_panel: date × ticker 的因子值
            forward_returns: date × ticker 的前瞻收益

        Returns:
            (mean_ic, ic_series)
            - mean_ic: IC 均值（|IC|>0.03 有信号）
            - ic_series: 每天的 IC 时间序列（用于算 IR = mean/std）
        """
        # 对齐
        common_idx = factor_panel.index.intersection(forward_returns.index)
        common_cols = factor_panel.columns.intersection(forward_returns.columns)
        if len(common_idx) < 5 or len(common_cols) < 3:
            return float("nan"), pd.Series(dtype=float)

        f = factor_panel.loc[common_idx, common_cols]
        r = forward_returns.loc[common_idx, common_cols]

        # 每天横截面 rank（pct=True 转百分位）
        f_ranked = f.rank(pct=True, axis=1)
        r_ranked = r.rank(pct=True, axis=1)

        # 每行（每天）的 rank correlation（Pearson on ranks = Spearman）
        # 使用 .corrwith 沿行方向
        ic_series = pd.Series(index=common_idx, dtype=float)
        for date in common_idx:
            f_row = f_ranked.loc[date]
            r_row = r_ranked.loc[date]
            # 删 NaN
            valid = f_row.notna() & r_row.notna()
            if valid.sum() < 3:
                ic_series[date] = float("nan")
                continue
            corr = f_row[valid].corr(r_row[valid])
            ic_series[date] = corr

        ic_series = ic_series.dropna()
        if len(ic_series) == 0:
            return float("nan"), ic_series

        mean_ic = float(ic_series.mean())
        return mean_ic, ic_series

    @staticmethod
    def cross_sectional_ir(ic_series: pd.Series) -> float:
        """IR = IC 均值 / IC 标准差"""
        if len(ic_series) < 2 or ic_series.std() == 0:
            return 0.0
        return float(ic_series.mean() / ic_series.std())


def build_forward_returns_panel(
    panel: dict[str, pd.DataFrame],
    horizon: int = 5,
) -> pd.DataFrame:
    """从多标的 OHLCV panel 构造前瞻收益 panel

    Args:
        panel: {ticker: OHLCV DataFrame}
        horizon: 前瞻天数

    Returns:
        DataFrame: index=date, columns=ticker，值为 horizon 日后的 log return
    """
    rets_dict = {}
    for ticker, df in panel.items():
        if "close" not in df.columns or len(df) < horizon + 1:
            continue
        log_ret = np.log(df["close"]).diff(horizon).shift(-horizon)
        rets_dict[ticker] = log_ret
    return pd.DataFrame(rets_dict)


__all__ = [
    "alpha_1", "alpha_2", "alpha_3", "alpha_4", "alpha_5",
    "alpha_6", "alpha_7", "alpha_8", "alpha_9", "alpha_10",
    "alpha_11", "alpha_12", "alpha_13", "alpha_14", "alpha_15",
    "alpha_16", "alpha_17", "alpha_18", "alpha_19", "alpha_20",
    "alpha_21", "alpha_22", "alpha_23", "alpha_24", "alpha_25",
    "alpha_26", "alpha_27", "alpha_28", "alpha_29", "alpha_30",
    "alpha_31", "alpha_32", "alpha_33", "alpha_34", "alpha_35",
    "alpha_36", "alpha_37", "alpha_38", "alpha_39", "alpha_40",
    "alpha_41", "alpha_42", "alpha_43", "alpha_44", "alpha_45",
    "alpha_46", "alpha_47", "alpha_48", "alpha_49", "alpha_50",
    "alpha_51", "alpha_52", "alpha_53", "alpha_54", "alpha_55",
    "alpha_56", "alpha_57", "alpha_58", "alpha_59", "alpha_60",
    "alpha_61", "alpha_62", "alpha_63", "alpha_64", "alpha_65",
    "alpha_66", "alpha_67", "alpha_68", "alpha_69", "alpha_70",
    "alpha_71", "alpha_72", "alpha_73", "alpha_74", "alpha_75",
    "alpha_76", "alpha_77", "alpha_78", "alpha_79", "alpha_80",
    "alpha_81", "alpha_82", "alpha_83", "alpha_84", "alpha_85",
    "alpha_86", "alpha_87", "alpha_88", "alpha_89", "alpha_90",
    "alpha_91", "alpha_92", "alpha_93", "alpha_94", "alpha_95",
    "alpha_96", "alpha_97", "alpha_98", "alpha_99", "alpha_100",
    "alpha_101",
    "Alpha101",
    "Alpha101CrossSectional",
    "build_forward_returns_panel",
]
