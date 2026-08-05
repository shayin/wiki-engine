"""SignalEvent 采集 runner（独立日频采集，不复用 engine 调仓日）

辩论共识 R8/R9：
- engine 周频调仓会导致事件样本随 rebalance_freq 失真（形态在周三确认下周一才看到）
- stats 采用独立日频（stats_eval_freq="D"）PIT 评估
- engine 闭环与 stats 各跑一遍，分别披露
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import pandas as pd

from quant_scanner.data.loader import DataLoader
from quant_scanner.signals.base import BaseSignal
from quant_scanner.stats.events import SignalEvent, SignalEventCollector
from quant_scanner.stats.pit_adapter import evaluate_with_pit

log = logging.getLogger(__name__)


@dataclass
class StatsRunConfig:
    """采集配置。"""

    start: str = "2020-01-01"
    end: str = "2025-12-31"
    cooldown_days: int = 20
    # whitelist_keys 默认在 Collector 里设置


class SignalEventRunner:
    """独立日频事件采集 runner。

    用法：
        runner = SignalEventRunner(signals, universe, loader)
        events = runner.run(start="2024-01-01", end="2024-12-31")

    review High 4：market_direction 等以市场指数为输入的 signal，runner 会单独
    加载 ^GSPC 数据并按 pit_date 截断后传入；其他 signal 用 ticker 自己的 OHLCV。
    """

    # 这些 signal 需要市场指数而非个股数据
    MARKET_INDEX_SIGNALS: set[str] = {"market_direction"}
    MARKET_TICKER: str = "^GSPC"

    def __init__(
        self,
        signals: list[BaseSignal],
        universe: list[str],
        loader: Optional[DataLoader] = None,
        collector: Optional[SignalEventCollector] = None,
        market_ticker: str = MARKET_TICKER,
    ):
        if not signals:
            raise ValueError("signals 不能为空")
        self.signals = signals
        self.universe = list(universe)
        self.loader = loader or DataLoader()
        self.collector = collector or SignalEventCollector()
        self.market_ticker = market_ticker

    def run(
        self,
        start: str = "2020-01-01",
        end: str = "2025-12-31",
        cooldown_days: Optional[int] = None,
    ) -> list[SignalEvent]:
        """日频采集事件。

        Args:
            start: 起始日（含）
            end: 结束日（含）
            cooldown_days: 可覆盖 collector 构造时的冷却期

        Returns:
            所有采集到的事件列表
        """
        if cooldown_days is not None:
            self.collector.cooldown_days = cooldown_days

        log.info(
            "[stats-runner] universe=%d signals=%s %s~%s",
            len(self.universe),
            [s.name for s in self.signals],
            start, end,
        )

        # 1. 拉取每个 ticker 的完整日线数据
        data: dict[str, pd.DataFrame] = {}
        for ticker in self.universe:
            df = self.loader.load(
                ticker,
                start=start,
                end=end,
                cache_key=f"{ticker}_{start}_{end}",
            )
            if df is None or df.empty:
                log.warning("[stats-runner] 跳过 %s：无数据", ticker)
                continue
            df = df.sort_index()
            data[ticker] = df

        if not data:
            log.error("[stats-runner] 所有 ticker 均无数据")
            return []

        # review High 4：market_direction 单独加载市场指数
        market_df: pd.DataFrame | None = None
        if any(sig.name in self.MARKET_INDEX_SIGNALS for sig in self.signals):
            market_df = self.loader.load(
                self.market_ticker,
                start=start,
                end=end,
                cache_key=f"{self.market_ticker}_{start}_{end}",
            )
            if market_df is None or market_df.empty:
                log.warning("[stats-runner] 市场指数 %s 无数据，相关 signal 将跳过", self.market_ticker)
            else:
                market_df = market_df.sort_index()

        # 2. 对每个 ticker 逐日 PIT 评估 + 采集
        start_ts = pd.Timestamp(start)
        end_ts = pd.Timestamp(end)

        for ticker, df in data.items():
            idx = pd.to_datetime(df.index)
            sim_dates = idx[(idx >= start_ts) & (idx <= end_ts)].sort_values()
            log.info("[stats-runner] %s: %d 个交易日", ticker, len(sim_dates))

            for current_date in sim_dates:
                # PIT 截断
                df_up_to = df.loc[df.index <= current_date]
                if df_up_to.empty:
                    continue
                # 对每个 signal 评估
                signal_results = []
                for sig in self.signals:
                    try:
                        # review High 4：市场指数 signal 用 market_df
                        # 市场数据缺失/截断后空 → 跳过该 signal（绝不回退到个股 df）
                        if sig.name in self.MARKET_INDEX_SIGNALS:
                            if market_df is None or market_df.empty:
                                continue
                            market_up_to = market_df.loc[market_df.index <= current_date]
                            if market_up_to.empty:
                                continue
                            sr = evaluate_with_pit(sig, self.market_ticker, market_up_to, current_date)
                            # sr.ticker 改回 ticker 维度（按 ticker 切片用）
                            sr.ticker = ticker
                        else:
                            sr = evaluate_with_pit(sig, ticker, df_up_to, current_date)
                    except Exception as e:
                        log.warning(
                            "[stats-runner] %s.%s 在 %s 抛异常: %s",
                            ticker, sig.name, current_date, e,
                        )
                        continue
                    signal_results.append(sr)
                if not signal_results:
                    continue
                # 交给 collector
                self.collector.on_daily_eval(
                    ticker=ticker,
                    date=current_date,
                    signal_results=signal_results,
                    trading_dates=sim_dates,
                )

        log.info("[stats-runner] 共采集 %d 个事件", len(self.collector.events))
        return self.collector.events
