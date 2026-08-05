"""market.py — 大盘背景层

服务"系统性背景"——单股/板块分析必须有大盘背景（熊市突破易失败、risk-off 时防御占优）。
回答：
- 大盘现在牛/熊？（SPY/QQQ/IWM vs MA200）
- VIX 情绪如何？（恐慌/平静）
- 当前 risk-on（进攻）还是 risk-off（防御）？
- 风格轮动：成长vs价值、周期vs防御、避险资产方向

核心方法：
- 大盘趋势：基准指数 vs MA50/MA200（价>MA200=牛市）
- VIX：<20 平静，20-30 警惕，>30 恐慌
- 风格轮动：成对 ETF 比值变化（QQQ/XLP 升=risk-on，IWF/IWD 升=成长占优）
- regime 判定：SPY>MA200 + QQQ/XLP 走强 = risk-on

用法：
    from quant_scanner.features.market import market_regime
    state = market_regime()  # {regime, trend, vix, style}
"""
from __future__ import annotations

import pandas as pd

from ..data.loader import DataLoader

# 基准指数
BENCHMARKS = {
    "SPY": "大盘(S&P500)", "QQQ": "纳指100", "DIA": "道琼斯", "IWM": "小盘(罗素2000)",
}
VIX_TICKER = "^VIX"

# 风格轮动配对（a/b 比值升 = a 占优）
STYLE_PAIRS = {
    "成长vs价值": ("IWF", "IWD"),
    "进攻vs防御(risk-on/off)": ("QQQ", "XLP"),
    "周期vs防御": ("XLI", "XLU"),
    "避险(黄金/长债)": ("GLD", "TLT"),
    "科技vs能源(增长vs现金)": ("XLK", "XLE"),
}


def market_trend(period: str = "1y") -> list[dict]:
    """大盘趋势：基准指数 vs MA50/MA200 + 多空头排列"""
    loader = DataLoader()
    out: list[dict] = []
    for sym, name in BENCHMARKS.items():
        df = loader.load(sym, period=period)
        if df.empty or len(df) < 200:
            continue
        close = df["close"]
        price = close.iloc[-1]
        ma50 = close.rolling(50).mean().iloc[-1]
        ma200 = close.rolling(200).mean().iloc[-1]
        bull_bear = "牛市" if price > ma200 else "熊市"
        alignment = "多头排列↑" if (price > ma50 > ma200) else \
                    ("空头排列↓" if (price < ma50 < ma200) else "均线纠缠")
        dist_ma200 = (price / ma200 - 1) * 100
        dist_ma50 = (price / ma50 - 1) * 100
        out.append({
            "symbol": sym, "name": name, "price": round(price, 2),
            "ma50": round(ma50, 2), "ma200": round(ma200, 2),
            "bull_bear": bull_bear, "alignment": alignment,
            "dist_ma200": round(dist_ma200, 1), "dist_ma50": round(dist_ma50, 1),
        })
    return out


def vix_level(period: str = "6m") -> dict | None:
    """VIX 恐慌水平"""
    loader = DataLoader()
    df = loader.load(VIX_TICKER, period=period)
    if df.empty:
        return None
    v = df["close"].iloc[-1]
    v_prev = df["close"].iloc[-5] if len(df) >= 5 else v
    level = "恐慌(>30)" if v > 30 else ("警惕(20-30)" if v > 20 else "平静(<20)")
    return {"value": round(v, 2), "level": level, "chg_5d": round((v / v_prev - 1) * 100, 1)}


def style_rotation(period: str = "6m") -> list[dict]:
    """风格轮动：成对 ETF 比值的 1月/3月变化（正值=a 占优）"""
    loader = DataLoader()
    out: list[dict] = []
    for pair_name, (a, b) in STYLE_PAIRS.items():
        df_a = loader.load(a, period=period)
        df_b = loader.load(b, period=period)
        if df_a.empty or df_b.empty:
            continue
        aligned = pd.concat([df_a["close"].rename("a"), df_b["close"].rename("b")], axis=1).dropna()
        if len(aligned) < 66:
            continue
        ratio = aligned["a"] / aligned["b"]
        chg_1m = (ratio.iloc[-1] / ratio.iloc[-22] - 1) * 100 if len(ratio) > 22 else 0
        chg_3m = (ratio.iloc[-1] / ratio.iloc[-66] - 1) * 100 if len(ratio) > 66 else 0
        out.append({
            "pair": pair_name, "a": a, "b": b,
            "ratio": round(ratio.iloc[-1], 3),
            "chg_1m": round(chg_1m, 2), "chg_3m": round(chg_3m, 2),
            "leading": a if chg_1m > 0 else b,
        })
    return out


def market_regime(period: str = "1y") -> dict:
    """市场状态总览 → {regime, trend, vix, style}

    regime: risk-on(进攻) / risk-off(防御) / 中性转折
    判定：SPY>MA200 + QQQ/XLP 比值走强 = risk-on；反之 risk-off
    """
    trend = market_trend(period)
    vix = vix_level()
    style = style_rotation(period)

    spy_trend = next((t for t in trend if t["symbol"] == "SPY"), None)
    qqq_xlp = next((s for s in style if "risk-on" in s["pair"]), None)

    spy_above_ma200 = spy_trend and spy_trend["dist_ma200"] > 0
    risk_on_style = qqq_xlp and qqq_xlp["chg_1m"] > 0
    vix_calm = vix and vix["value"] < 25

    if spy_above_ma200 and risk_on_style and vix_calm:
        regime = "risk-on（进攻：成长/科技/周期占优）"
    elif (not spy_above_ma200) and (qqq_xlp and qqq_xlp["chg_1m"] < 0):
        regime = "risk-off（防御：避险/防御/现金占优）"
    else:
        regime = "中性/转折（信号冲突）"

    return {"regime": regime, "trend": trend, "vix": vix, "style": style}


# 市场宽度样本（跨板块蓝筹，30 只代表性，兼顾速度与覆盖）
BREADTH_UNIVERSE = [
    # 科技
    "AAPL", "MSFT", "NVDA", "GOOGL", "META", "AMZN", "TSLA",
    # 金融
    "JPM", "BAC", "GS", "WFC",
    # 医疗
    "JNJ", "UNH", "PFE", "LLY",
    # 能源
    "XOM", "CVX", "COP",
    # 消费
    "WMT", "PG", "COST", "KO",
    # 工业
    "CAT", "BA", "GE", "HON",
    # 半导体
    "AVGO", "AMD", "INTC",
]


def market_breadth(period: str = "1y") -> dict | None:
    """市场宽度：%above MA50/MA200 + 新高/新低 + 涨跌家数。

    市场顶底核心指标：
    - %above MA200 >60% 健康，<40% 弱势，<20% 恐慌底
    - 新高/新低比 >3 多头，<0.3 空头
    - 涨跌比 >2 强势，<0.5 弱势
    """
    loader = DataLoader()
    data = loader.load_batch(BREADTH_UNIVERSE, period=period)
    above_ma200 = above_ma50 = new_highs = new_lows = 0
    total = 0
    advancers = decliners = 0
    for t, df in data.items():
        if df.empty or len(df) < 200:
            continue
        close = df["close"]
        total += 1
        ma200 = close.rolling(200).mean().iloc[-1]
        ma50 = close.rolling(50).mean().iloc[-1]
        if close.iloc[-1] > ma200:
            above_ma200 += 1
        if close.iloc[-1] > ma50:
            above_ma50 += 1
        # 52 周高低（数据不足 252 用全部）
        lookback = min(252, len(close))
        if close.iloc[-1] >= close.iloc[-lookback:].max() * 0.98:
            new_highs += 1
        if close.iloc[-1] <= close.iloc[-lookback:].min() * 1.02:
            new_lows += 1
        # 今日涨跌
        if len(close) >= 2:
            if close.iloc[-1] > close.iloc[-2]:
                advancers += 1
            elif close.iloc[-1] < close.iloc[-2]:
                decliners += 1
    if not total:
        return None
    pct_ma200 = round(above_ma200 / total * 100, 1)
    breadth_health = "健康(>60%)" if pct_ma200 > 60 else \
                     ("弱势(40-60%)" if pct_ma200 > 40 else ("恐慌底(<20%)" if pct_ma200 < 20 else "偏弱(20-40%)"))
    return {
        "universe_size": total,
        "pct_above_ma200": pct_ma200,
        "pct_above_ma50": round(above_ma50 / total * 100, 1),
        "breadth_health": breadth_health,
        "new_52w_highs": new_highs,
        "new_52w_lows": new_lows,
        "high_low_ratio": round(new_highs / max(new_lows, 1), 2),
        "advancers": advancers, "decliners": decliners,
        "ad_ratio": round(advancers / max(decliners, 1), 2),
    }
