"""screener.py — 选股扫描（趋势股 / 突破股 / 短线动量）

从一篮子股票（watchlist）中，按策略筛出符合的。复用 indicators + 现有信号。
三种策略：
- 突破股：创 50 日新高 + 放量（趋势启动）
- 趋势股：Minervini 趋势模板第二阶段（主升浪）
- 短线动量：RSI 强（50-70）+ 放量 + 近 1 月涨 >5%

用法：
    from quant_scanner.features.screener import screen_breakout, screen_trend, screen_momentum
    hits = screen_breakout(["QCOM","NVDA","AAPL",...])
"""
from __future__ import annotations

import logging

import pandas as pd

from ..data.loader import DataLoader
from .indicators import rsi, volume_ratio
from ..signals.trend_template import TrendTemplateSignal
from ..utils.position_sizing import TradeSetup, position_size

log = logging.getLogger(__name__)


def screen_breakout(tickers: list[str], period: str = "6mo") -> list[dict]:
    """突破股：今日接近/创 50 日新高 + 放量（>1.5x 5日均量）"""
    loader = DataLoader()
    out: list[dict] = []
    for t in tickers:
        df = loader.load(t, period=period)
        if df.empty or len(df) < 60:
            continue
        close, vol = df["close"], df["volume"]
        high_50 = close.iloc[-50:].max()
        breakout = close.iloc[-1] >= high_50 * 0.98
        vol_ma5 = vol.iloc[-6:-1].mean()
        vol_surge = bool(vol.iloc[-1] > vol_ma5 * 1.5) if vol_ma5 else False
        if breakout and vol_surge:
            out.append({
                "ticker": t, "close": round(close.iloc[-1], 2),
                "rsi": round(rsi(close).iloc[-1], 1),
                "vol_ratio": round(vol.iloc[-1] / vol_ma5, 2),
                "dist_from_50d_high": round((close.iloc[-1] / high_50 - 1) * 100, 2),
                "strategy": "突破+放量",
            })
    return out


def screen_trend(tickers: list[str], period: str = "1y") -> list[dict]:
    """趋势股：Minervini 趋势模板第二阶段（8 条满足）"""
    loader = DataLoader()
    sig = TrendTemplateSignal()
    out: list[dict] = []
    for t in tickers:
        df = loader.load(t, period=period)
        if df.empty:
            continue
        sr = sig.evaluate(t, df)
        if sr.passed:
            out.append({
                "ticker": t, "close": round(df["close"].iloc[-1], 2),
                "trend_score": round(sr.value, 2),
                "strategy": "趋势(第二阶段)",
            })
    out.sort(key=lambda x: x["trend_score"], reverse=True)
    return out


def screen_momentum(tickers: list[str], period: str = "3mo") -> list[dict]:
    """短线动量：RSI 50-70（强但未超买）+ 量比>1.3 + 近 1 月涨 >5%"""
    loader = DataLoader()
    out: list[dict] = []
    for t in tickers:
        df = loader.load(t, period=period)
        if df.empty or len(df) < 30:
            continue
        close, vol = df["close"], df["volume"]
        rsi_v = rsi(close).iloc[-1]
        vr = volume_ratio(vol).iloc[-1]
        chg_1m = (close.iloc[-1] / close.iloc[-22] - 1) * 100 if len(close) > 22 else 0
        if 50 < rsi_v < 75 and vr > 1.3 and chg_1m > 5:
            out.append({
                "ticker": t, "close": round(close.iloc[-1], 2),
                "rsi": round(rsi_v, 1), "vol_ratio": round(vr, 2),
                "chg_1m": round(chg_1m, 1),
                "strategy": "短线动量",
            })
    out.sort(key=lambda x: x["chg_1m"], reverse=True)
    return out


# trend_template@60d 回测胜率（SP500 × 5y default 置信 n=1553：胜率 60.3% / 盈亏比 1.98 / EV+3.84%）
# 用于 screen_trend_plan 的期望值估算。详见 HANDOFF「个股信号初测」节。
TREND_WIN_RATE_60D = 0.603
HOLD_60D_EV_PCT = 4.21  # trade_sim 组合回测（SP500×5y default）：纯持有 60d 实际 EV


def _atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    """ATR（Average True Range，平均真实波幅）— 用简单 MA 近似 Wilder 法。

    衡量单日价格波动幅度，用于设止损（2×ATR 是常用止损距离）。
    """
    h, l, c = df["high"], df["low"], df["close"]
    prev_c = c.shift(1)
    tr = pd.concat([(h - l), (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean()


def screen_trend_plan(
    tickers: list[str],
    period: str = "1y",
    account: float | None = None,
    risk_pct: float = 0.02,
    atr_mult: float = 4.0,
    target_R: float = 3.0,
) -> list[dict]:
    """趋势模板中线选股 + 可执行交易计划（基于 trend_template@60d 回测锚点）。

    回测锚点（SP500 × 5y default 置信 n=1553，trade_sim 组合回测）：
    - **纯持有 60d 最优**：胜率 61.4% / EV +4.21% / 盈亏比 2.12
    - 2×ATR 紧止损有害：60% 交易被洗出，EV 暴跌到 +0.34%
    - 止损越宽越好（4×ATR/3:1 是带止损配置里相对优的：+2.34%/1.52，仍不及 hold）
    → 默认 atr_mult=4 / target_R=3（宽止损给趋势空间），exit_note 提示 hold 60d 更优

    Args:
        tickers: 候选股池
        period: 趋势模板评估周期（默认 1y）
        account: 账户权益（USD），None 则不算股数
        risk_pct: 单笔风险占比（Murphy 2% 铁律默认 0.02）
        atr_mult: 止损 = entry - atr_mult×ATR（默认 4，回测显示 2×ATR 太紧）
        target_R: 盈亏比（默认 3:1，给赢家空间）

    Returns:
        list[dict]，每个含 entry/stop/target/payoff/expectancy/holding/exit_note + 可选 shares
    """
    hits = screen_trend(tickers, period=period)
    loader = DataLoader()
    out: list[dict] = []
    for h in hits:
        t = h["ticker"]
        df = loader.load(t, period="6mo")  # 6mo 足够算 ATR
        if df.empty or len(df) < 15:
            continue
        atr = float(_atr(df).iloc[-1])
        if atr <= 0 or pd.isna(atr):
            continue
        entry = h["close"]
        risk = atr_mult * atr
        stop = round(entry - risk, 2)
        target = round(entry + target_R * risk, 2)  # target_R:1 盈亏比
        setup = TradeSetup(
            entry_price=entry, stop_loss=stop, target_price=target,
            win_rate=TREND_WIN_RATE_60D,
        )
        plan = {
            **h,
            "atr": round(atr, 2),
            "stop": stop,
            "target": target,
            "risk_pct": round(setup.risk_pct * 100, 2),
            "reward_pct": round(setup.reward_pct * 100, 2),
            "payoff": round(setup.payoff_ratio, 2),
            "backtest_ev_hold": HOLD_60D_EV_PCT,  # 实际回测 EV（纯持有60d，default置信），非理论期望
            "holding": "60 交易日",
            "exit_note": "回测：纯持有60d最优(EV+4.21%/盈亏比2.12)，紧止损有害。止损作风控上限，目标参考",
        }
        if account and account > 0:
            shares = position_size(account, setup, risk_budget_pct=risk_pct, atr=atr)
            plan["shares"] = int(shares)
            plan["position_value"] = round(shares * entry, 0)
            plan["account_risk"] = round(account * risk_pct, 0)
        out.append(plan)
    return out


# 默认热门观察池（可被 watchlist 文件覆盖）
DEFAULT_WATCHLIST = [
    # 科技龙头
    "AAPL", "MSFT", "NVDA", "GOOGL", "META", "AMZN", "TSLA", "AVGO", "AMD",
    # 半导体
    "QCOM", "TXN", "INTC", "MU", "AMAT", "LRCX",
    # AI/云
    "PLTR", "CRM", "NOW", "SNOW", "DDOG", "NFLX",
    # 金融/周期
    "JPM", "GS", "XOM", "CVX",
    # 消费/医疗
    "COST", "UNH", "LLY",
]
