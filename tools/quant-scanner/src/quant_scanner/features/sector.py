"""sector.py — 板块相对强度（RS）+ 轮动检测 + 启动信号

服务"找板块机会 / beta 板块启动"场景。回答：
- 现在哪个板块相对大盘最强（资金流向）？
- 哪些板块 RS 在加速上升（轮动入场）？
- 哪些板块正在突破+放量+RS升（启动信号）？

核心方法：
- 板块 RS = 行业ETF价格 / SPY价格（标准化），RS 上升=跑赢大盘
- 轮动 = RS 的 1月/3月变化（正值=资金流入该板块）
- 启动信号 = ETF 创 20 日新高 + 放量 + RS 1月上升（三重确认）

用法：
    from quant_scanner.features.sector import compute_sector_rs
    ranking = compute_sector_rs()  # 返回按 RS_3m 降序的板块列表
"""
from __future__ import annotations

import logging

import pandas as pd

from ..data.loader import DataLoader
from ..stats.events import SignalEvent

log = logging.getLogger(__name__)

# SPDR 11 大行业 + 常用细分 ETF
SECTOR_ETFS: dict[str, str] = {
    "XLK": "科技", "XLY": "可选消费", "XLP": "必选消费", "XLE": "能源",
    "XLF": "金融", "XLV": "医疗保健", "XLI": "工业", "XLB": "原材料",
    "XLRE": "房地产", "XLU": "公用事业", "XLC": "通信服务",
    # 细分（可选，覆盖热门赛道）
    "SMH": "半导体", "KWEB": "中概互联网", "XBI": "生物科技",
    "GDX": "金矿", "ITA": "航天国防", "IBB": "生物(广)",
    "XME": "钢铁矿业", "VNQ": "REITs",
}
BENCHMARK = "SPY"


def is_breakout_20d(close: pd.Series, strict: bool = True, frac: float = 0.98) -> bool:
    """20 日突破判定（纯函数，可独立测试）。

    - strict=True（默认）：今天 > 前 20 日（不含今天）最高。语义上才是真正"创新高"
    - strict=False：今天 >= 最近 20 日（含今天）最高 × frac。含当日导致 close[-1] 永远
      ≤ high_20，只要没跌超 (1-frac) 就成立

    ⚠️ 回测结论（5y 全样本 18 ETF，详见 HANDOFF）：板块 ETF 层面 strict/loose 突破
    都没有可靠预测力（horizon 5/20/60 胜率 42-52%，EV 接近零或负）。但 strict 一致
    略优于 loose（所有 horizon 下 loose ≤ strict），且语义更准确，故 compute_sector_rs
    默认用 strict——是"更精准的描述"，不是"有效的预测信号"。

    Args:
        close: 收盘价序列（PIT 切片，iloc[-1] 为当日）
        strict: True=严格突破，False=宽松（接近新高）
        frac: 宽松阈值（默认 0.98）
    """
    if strict:
        if len(close) < 21:
            return False
        return float(close.iloc[-1]) > float(close.iloc[-21:-1].max())
    if len(close) < 20:
        return False
    return float(close.iloc[-1]) >= float(close.iloc[-20:].max()) * frac


def compute_market_regime(spy_df: pd.DataFrame) -> dict:
    """基于 SPY MA50/MA200 算当前市场 regime（仅作状态描述）。

    ⚠️ 10y 全样本 default 置信结论：板块 ETF 的 launch 信号（突破+放量+RS升任何组合）
    在 bull_strong / bull_correction / bear 所有 regime 下均无可靠预测力（3y/5y/10y
    三轮扩样本互相推翻 = 信号是噪音，详见 HANDOFF）。regime 在此仅描述市场所处阶段，
    **不附交易断言**——launch_signal 不能作为预测信号做交易。

    Returns:
        {regime, close, ma50, ma200, warning}
        warning 为中性状态提示（非交易信号），bull_strong 时为 None
    """
    if spy_df is None or spy_df.empty or len(spy_df) < 200:
        return {"regime": "warmup", "close": None, "ma50": None, "ma200": None,
                "warning": "MA200 未形成（数据 <200 日），regime 不定"}
    close = float(spy_df["close"].iloc[-1])
    ma50 = float(spy_df["close"].rolling(50).mean().iloc[-1])
    ma200 = float(spy_df["close"].rolling(200).mean().iloc[-1])
    base = {"close": close, "ma50": ma50, "ma200": ma200}
    if close > ma50 > ma200:
        return {**base, "regime": "bull_strong", "warning": None}
    if close > ma200:
        return {**base, "regime": "bull_correction",
                "warning": "SPY 跌破 MA50 但 >MA200（牛市回调），市场短期调整中"}
    return {**base, "regime": "bear",
            "warning": "SPY <MA200（熊市），市场下行趋势中"}


def compute_sector_rs(period: str = "1y", etfs: dict[str, str] | None = None) -> list[dict]:
    """计算所有板块 ETF 相对 SPY 的强度 + 轮动 + 启动信号。

    Args:
        period: 历史数据周期（默认 1y，至少需要 66 个交易日算 3 月变化）
        etfs: 自定义 ETF 池 {代码: 名称}，默认用 SECTOR_ETFS

    Returns:
        list[dict]，按 rs_3m_chg 降序。每项：
        {etf, name, rs_ratio, rs_1m_chg, rs_3m_chg, trend, breakout_20d,
         volume_surge, launch_signal, etf_chg_1m, spy_chg_1m}
    """
    etfs = etfs or SECTOR_ETFS
    loader = DataLoader()
    spy = loader.load(BENCHMARK, period=period)
    if spy.empty:
        return []

    spy_close = spy["close"]
    spy_chg_1m = (spy_close.iloc[-1] / spy_close.iloc[-22] - 1) * 100 if len(spy_close) > 22 else 0

    results: list[dict] = []
    for etf, name in etfs.items():
        df = loader.load(etf, period=period)
        if df.empty or len(df) < 66:
            continue
        # 对齐 ETF 与 SPY 收盘
        aligned = pd.concat([df["close"].rename("etf"), spy_close.rename("spy")], axis=1).dropna()
        if len(aligned) < 66:
            continue
        rs = aligned["etf"] / aligned["spy"]
        rs_now = rs.iloc[-1]
        rs_1m = rs.iloc[-22] if len(rs) > 22 else rs.iloc[0]
        rs_3m = rs.iloc[-66] if len(rs) > 66 else rs.iloc[0]
        rs_1m_chg = (rs_now / rs_1m - 1) * 100
        rs_3m_chg = (rs_now / rs_3m - 1) * 100

        # 趋势：RS vs 其 20 日均线
        rs_ma20 = rs.rolling(20).mean()
        trend = "↑走强" if rs.iloc[-1] > rs_ma20.iloc[-1] else "↓走弱"

        # ETF 自身动量
        etf_close = df["close"]
        etf_vol = df["volume"]
        etf_chg_1m = (etf_close.iloc[-1] / etf_close.iloc[-22] - 1) * 100 if len(etf_close) > 22 else 0
        # 严格突破：5y 全样本下 strict(48.5%) ≥ loose(44.5%)，均不显著但 strict 不更差且语义更准
        # ⚠️ 板块 ETF launch 信号整体预测力弱，breakout_20d 在此是"描述"非"预测信号"
        breakout_20d = is_breakout_20d(etf_close, strict=True)
        vol_ma5 = etf_vol.iloc[-6:-1].mean()
        volume_surge = bool(etf_vol.iloc[-1] > vol_ma5 * 1.5) if vol_ma5 else False

        # 启动信号 = 突破 + 放量 + RS 上升（三重确认）
        launch = bool(breakout_20d and volume_surge and rs_1m_chg > 0)

        results.append({
            "etf": etf, "name": name,
            "rs_ratio": round(rs_now, 3),
            "rs_1m_chg": round(rs_1m_chg, 2),
            "rs_3m_chg": round(rs_3m_chg, 2),
            "trend": trend,
            "etf_chg_1m": round(etf_chg_1m, 2),
            "spy_chg_1m": round(spy_chg_1m, 2),
            "breakout_20d": breakout_20d,
            "volume_surge": volume_surge,
            "launch_signal": launch,
        })

    results.sort(key=lambda x: x["rs_3m_chg"], reverse=True)
    return results


def ticker_to_sector(ticker: str) -> str | None:
    """查个股所属板块（yfinance info.sector）。失败返回 None。"""
    loader = DataLoader()
    try:
        info = loader.yf.Ticker(ticker).info
        return info.get("sector")
    except Exception:
        return None


def sector_summary(ranking: list[dict]) -> dict:
    """对 ranking 做摘要：强势/弱势/启动/轮动。

    Returns:
        {strongest, weakening, launching, rotating_in, rotating_out}
    """
    if not ranking:
        return {}
    strongest = [r for r in ranking[:5]]  # RS 3月最强 top5
    launching = [r for r in ranking if r["launch_signal"]]
    rotating_in = [r for r in ranking if r["rs_1m_chg"] > 2 and r["trend"] == "↑走强"]
    rotating_out = [r for r in ranking if r["rs_1m_chg"] < -2 and r["trend"] == "↓走弱"]
    weakening = [r for r in ranking[-3:]]  # RS 最弱 bottom3
    return {
        "strongest": strongest,
        "launching": launching,
        "rotating_in": rotating_in,
        "rotating_out": rotating_out,
        "weakest": weakening,
    }


# =====================================================================
# 板块启动信号历史回测（PIT 事件采集）
# =====================================================================

# 现有 sector.py 的 breakout_20d 用「最近 20 日（含当日）最高 × 0.98」，由于含当日，
# close[-1] 永远 ≤ high_20，导致只要当天没跌超 2% 就判 True——并非真正「突破 20 日新高」。
# 回测里同时采集严格版（突破前 20 日最高，不含今天）和宽松版（现有定义）做对比，
# 让数据回答哪种定义更有预测力。
LAUNCH_EVENT_TYPES: tuple[str, ...] = (
    "LAUNCH_STRICT",    # 严格突破 + 放量 + RS 上升（三重确认，严格版）
    "LAUNCH_LOOSE",     # 宽松突破(0.98) + 放量 + RS 上升（对齐 compute_sector_rs 现有定义）
    "BREAKOUT",         # 仅严格突破 20 日新高（单条件基线）
    "BREAKOUT_VOL",     # 严格突破 + 放量（无 RS，隔离 RS 的边际贡献）
    "VOLUME_SURGE",     # 仅放量（单条件基线）
    "RS_RISING",        # 仅 RS 1 月上升（单条件基线）
)


def collect_sector_launch_events(
    etf: str,
    df: pd.DataFrame,
    spy: pd.DataFrame,
    cooldown_days: int = 20,
    min_history: int = 66,
    breakout_frac: float = 0.98,
    vol_surge_mult: float = 1.5,
) -> list[SignalEvent]:
    """PIT 遍历 ETF 历史，采集板块启动信号事件（含条件拆解）。

    复用 stats 基建：返回标准 SignalEvent，可直接喂 compute_forward_labels / aggregate。

    对每个交易日 t（从 min_history 起，保证有 3 月 RS 数据）：
    - 切片 iloc[:t+1]（PIT，不含未来）
    - PIT 计算 breakout_strict / breakout_loose / volume_surge / rs_1m_chg
    - 按 LAUNCH_EVENT_TYPES 的 6 种条件组合各自判定，命中的生成事件
    - 同 etf × event_type 受 cooldown_days 去重（相邻事件至少隔 N 个交易日）

    Args:
        etf: ETF 代码（写入 event.ticker）
        df: ETF OHLCV（完整历史，索引为交易日）
        spy: SPY OHLCV（完整历史）
        cooldown_days: 同 event_type 相邻事件最小间隔（交易日）
        min_history: 起始遍历位置（需 ≥66 以算 RS 3m，此处主要保证 RS 1m/突破窗口）
        breakout_frac: 宽松突破阈值（对齐 compute_sector_rs 的 0.98）
        vol_surge_mult: 放量倍数（对齐 1.5）

    Returns:
        SignalEvent 列表（signal_name="sector_launch", direction="long"）
    """
    if df is None or df.empty or spy is None or spy.empty:
        return []
    if min_history < 66:
        log.warning(
            "min_history=%d < 66，breakout(需 20 日)/RS(需 22 日) 窗口可能不完整，建议 ≥66",
            min_history,
        )

    # 对齐 ETF 与 SPY 收盘（dropna 后两者共有交易日），volume 按对齐后 index 取
    # 显式 sort_index：entry_date=idx[t+1] 依赖索引按时间升序（pandas4 concat 默认排序将变）
    # PIT 注：aligned 用完整历史定"哪些交易日有效"，日期集合层面有极轻微 listing bias
    # （SPY 单日缺失极少见）；价格层面由下方 iloc[:t+1] 严格 PIT。接受此 trade-off 换 O(n) 性能。
    aligned = pd.concat([df["close"].rename("etf"), spy["close"].rename("spy")], axis=1, sort=False).sort_index().dropna()
    if len(aligned) < min_history:
        return []
    aligned_vol = df["volume"].reindex(aligned.index)
    rs_series = aligned["etf"] / aligned["spy"]
    idx = aligned.index

    events: list[SignalEvent] = []
    last_fired: dict[str, int] = {}  # event_type → 上次触发的 t（positional）

    for t in range(min_history, len(aligned)):
        close_up_to = aligned["etf"].iloc[: t + 1]
        vol_up_to = aligned_vol.iloc[: t + 1]
        rs_up_to = rs_series.iloc[: t + 1]
        date = idx[t]

        close_now = float(close_up_to.iloc[-1])

        # 严格 / 宽松突破（统一走 is_breakout_20d；宽松保留作回测对比基线，预测力弱于严格）
        breakout_strict = is_breakout_20d(close_up_to, strict=True)
        breakout_loose = is_breakout_20d(close_up_to, strict=False, frac=breakout_frac)

        # 放量：今天量 > 前 5 日均量 × mult
        if len(vol_up_to) >= 6:
            vol_ma5 = float(vol_up_to.iloc[-6:-1].mean())
            volume_surge = bool(vol_ma5 > 0 and float(vol_up_to.iloc[-1]) > vol_ma5 * vol_surge_mult)
        else:
            volume_surge = False

        # RS 1 月变化
        if len(rs_up_to) > 22:
            rs_now = float(rs_up_to.iloc[-1])
            rs_1m = float(rs_up_to.iloc[-22])
            rs_1m_chg = (rs_now / rs_1m - 1) * 100
        else:
            rs_1m_chg = 0.0
        rs_rising = rs_1m_chg > 0

        # 6 种条件组合
        combos: list[tuple[str, bool]] = [
            ("LAUNCH_STRICT", breakout_strict and volume_surge and rs_rising),
            ("LAUNCH_LOOSE", breakout_loose and volume_surge and rs_rising),
            ("BREAKOUT", breakout_strict),
            ("BREAKOUT_VOL", breakout_strict and volume_surge),
            ("VOLUME_SURGE", volume_surge),
            ("RS_RISING", rs_rising),
        ]

        # entry_date = 下一个共有交易日（ETF∩SPY）；行业 ETF 与 SPY 交易日重合度极高，
        # 且 compute_forward_labels 对缺失日期有 fallback，差别可忽略
        entry_date = idx[t + 1] if t + 1 < len(aligned) else None

        for event_type, hit in combos:
            if not hit:
                continue
            last_t = last_fired.get(event_type)
            if last_t is not None and (t - last_t) < cooldown_days:
                continue
            last_fired[event_type] = t
            events.append(SignalEvent(
                ticker=etf,
                signal_name="sector_launch",
                event_type=event_type,
                direction="long",
                confirmed_date=pd.Timestamp(date),
                entry_date=pd.Timestamp(entry_date) if entry_date is not None else None,
                event_kind="confirmation",
                signal_value=1.0,
                details_whitelist={
                    "rs_1m_chg": round(rs_1m_chg, 2),
                    "breakout_strict": breakout_strict,
                    "volume_surge": volume_surge,
                    "close": round(close_now, 2),
                },
            ))

    return events
