"""回测引擎

事件驱动回测框架，与现有 Signal 体系集成。

核心约束：
- Point-in-time：信号评估只用截止当日数据
- 等权分配资金（每笔买入占用 1/max_positions 的当前权益）
- 每笔交易扣 commission + slippage
- 一只股票同时只能有一笔未平仓 trade
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from quant_scanner.data.loader import DataLoader
from quant_scanner.signals.base import BaseSignal

log = logging.getLogger(__name__)


# =====================================================================
# 数据类
# =====================================================================


@dataclass
class TradeRecord:
    """单笔交易记录"""

    ticker: str
    entry_date: pd.Timestamp
    entry_price: float  # 已含滑点的实际成交价
    shares: float = 0.0
    exit_date: Optional[pd.Timestamp] = None
    exit_price: Optional[float] = None  # 已含滑点的实际成交价
    return_pct: Optional[float] = None  # 含 commission + slippage 的净收益
    exit_reason: str = ""  # signal_exit / stop_loss / time_limit / end_of_data

    def close(
        self,
        exit_date: pd.Timestamp,
        raw_exit_price: float,
        exit_reason: str,
        commission_pct: float,
        slippage_pct: float,
    ) -> None:
        """平仓并计算净收益

        Args:
            exit_date: 平仓日
            raw_exit_price: 平仓日收盘价（未含滑点）
            exit_reason: 平仓原因
            commission_pct: 单边佣金比例
            slippage_pct: 单边滑点比例（卖出方向为负向滑点，价格 × (1 - slippage)）
        """
        # 卖出滑点：成交价低于收盘价
        executed = raw_exit_price * (1.0 - slippage_pct)
        # 净收益：(卖出净额 / 买入净额) - 1
        # 买入净额 = entry_price × (1 + commission)
        # 卖出净额 = executed × (1 - commission)
        buy_notional = self.entry_price * (1.0 + commission_pct)
        sell_notional = executed * (1.0 - commission_pct)
        self.exit_date = exit_date
        self.exit_price = executed
        self.return_pct = (sell_notional / buy_notional) - 1.0 if buy_notional > 0 else 0.0
        self.exit_reason = exit_reason


@dataclass
class BacktestReport:
    """回测结果"""

    start: str
    end: str
    trades: list[TradeRecord] = field(default_factory=list)
    equity_curve: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))
    # 元信息（用于报告渲染，不影响计算）
    _max_positions: int = 0
    _signals: list[str] = field(default_factory=list)

    # ----------------------------- 派生指标 -----------------------------

    @property
    def n_trades(self) -> int:
        return len(self.trades)

    @property
    def closed_trades(self) -> list[TradeRecord]:
        return [t for t in self.trades if t.return_pct is not None]

    @property
    def win_rate(self) -> float:
        closed = self.closed_trades
        if not closed:
            return 0.0
        wins = sum(1 for t in closed if (t.return_pct or 0) > 0)
        return wins / len(closed)

    @property
    def avg_return(self) -> float:
        closed = self.closed_trades
        if not closed:
            return 0.0
        return float(np.mean([t.return_pct or 0.0 for t in closed]))

    @property
    def total_return(self) -> float:
        """净值曲线总收益"""
        if self.equity_curve.empty:
            return 0.0
        return float(self.equity_curve.iloc[-1] / self.equity_curve.iloc[0] - 1.0)

    @property
    def max_drawdown(self) -> float:
        """最大回撤（负数，如 -0.15 表示 -15%）"""
        if self.equity_curve.empty:
            return 0.0
        eq = self.equity_curve
        running_max = eq.cummax()
        dd = eq / running_max - 1.0
        return float(dd.min())

    @property
    def sharpe(self) -> float:
        """年化夏普（无风险利率 0），基于净值日收益率"""
        if len(self.equity_curve) < 2:
            return 0.0
        rets = self.equity_curve.pct_change().dropna()
        if rets.std() == 0 or len(rets) < 2:
            return 0.0
        # 252 交易日年化
        return float(rets.mean() / rets.std() * np.sqrt(252))

    @property
    def profit_factor(self) -> float:
        """总盈利 / 总亏损绝对值"""
        closed = self.closed_trades
        gains = sum(t.return_pct for t in closed if (t.return_pct or 0) > 0)
        losses = sum(-t.return_pct for t in closed if (t.return_pct or 0) < 0)
        if losses == 0:
            return float("inf") if gains > 0 else 0.0
        return float(gains / losses)

    def summary_dict(self) -> dict:
        """所有指标打包"""
        return {
            "start": self.start,
            "end": self.end,
            "n_trades": self.n_trades,
            "n_closed": len(self.closed_trades),
            "n_open": self.n_trades - len(self.closed_trades),
            "win_rate": self.win_rate,
            "avg_return": self.avg_return,
            "total_return": self.total_return,
            "max_drawdown": self.max_drawdown,
            "sharpe": self.sharpe,
            "profit_factor": self.profit_factor,
        }

    def per_ticker_stats(self) -> pd.DataFrame:
        """按 ticker 分组的统计"""
        closed = self.closed_trades
        if not closed:
            return pd.DataFrame(
                columns=["ticker", "n_trades", "win_rate", "avg_return", "total_return"]
            )
        rows = []
        tickers = sorted({t.ticker for t in closed})
        for tk in tickers:
            tk_trades = [t for t in closed if t.ticker == tk]
            returns = [t.return_pct or 0.0 for t in tk_trades]
            wins = sum(1 for r in returns if r > 0)
            rows.append({
                "ticker": tk,
                "n_trades": len(tk_trades),
                "win_rate": wins / len(tk_trades) if tk_trades else 0.0,
                "avg_return": float(np.mean(returns)) if returns else 0.0,
                "total_return": float(np.sum(returns)) if returns else 0.0,
            })
        return pd.DataFrame(rows)


# =====================================================================
# 回测引擎
# =====================================================================


class OpenPosition:
    """运行中的持仓（内部使用）"""

    def __init__(self, trade: TradeRecord):
        self.trade = trade


class Backtester:
    """事件驱动回测引擎"""

    def __init__(
        self,
        signals: list[BaseSignal],
        universe: list[str],
        loader: Optional[DataLoader] = None,
        entry_threshold: float = 0.7,
        exit_threshold: float = 0.3,
        stop_loss_pct: float = 0.08,
        time_limit_days: int = 60,
        commission_pct: float = 0.001,
        slippage_pct: float = 0.001,
        max_positions: int = 5,
        weights: Optional[dict[str, float]] = None,
    ):
        if not signals:
            raise ValueError("signals 不能为空")
        if max_positions < 1:
            raise ValueError("max_positions 必须 ≥ 1")

        self.signals = signals
        self.universe = list(universe)
        self.loader = loader or DataLoader()
        self.entry_threshold = entry_threshold
        self.exit_threshold = exit_threshold
        self.stop_loss_pct = stop_loss_pct
        self.time_limit_days = time_limit_days
        self.commission_pct = commission_pct
        self.slippage_pct = slippage_pct
        self.max_positions = max_positions

        # 权重归一化
        if weights is None:
            w = 1.0 / len(signals)
            self.weights = {s.name: w for s in signals}
        else:
            total = sum(weights.values())
            if total <= 0:
                raise ValueError("weights 之和必须 > 0")
            self.weights = {k: v / total for k, v in weights.items()}

    # ----------------------------- 公开入口 -----------------------------

    def run(
        self,
        start: str = "2020-01-01",
        end: str = "2025-12-31",
        rebalance_freq: str = "W",
    ) -> BacktestReport:
        """运行回测

        Args:
            start: YYYY-MM-DD
            end: YYYY-MM-DD
            rebalance_freq: "W"=周频（每周一）, "D"=日频
        """
        log.info(
            f"[backtest] universe={len(self.universe)} signals={[s.name for s in self.signals]} "
            f"{start}~{end} freq={rebalance_freq}"
        )

        # 1. 拉取所有 ticker 的历史数据
        data: dict[str, pd.DataFrame] = {}
        for ticker in self.universe:
            df = self.loader.load(
                ticker,
                start=start,
                end=end,
                cache_key=f"{ticker}_{start}_{end}",
            )
            if df is None or df.empty:
                log.warning(f"[backtest] 跳过 {ticker}：无数据")
                continue
            df = df.sort_index()
            data[ticker] = df

        if not data:
            log.error("[backtest] 所有 ticker 均无数据")
            return BacktestReport(start=start, end=end)

        # 2. 生成调仓日序列
        rebalance_dates = self._build_rebalance_dates(data, start, end, rebalance_freq)
        log.info(f"[backtest] {len(rebalance_dates)} 个调仓日")

        # 3. 逐日推进
        return self._simulate(data, rebalance_dates, start, end)

    # ----------------------------- 内部实现 -----------------------------

    def _build_rebalance_dates(
        self,
        data: dict[str, pd.DataFrame],
        start: str,
        end: str,
        freq: str,
    ) -> list[pd.Timestamp]:
        # 取所有 ticker 的并集交易日索引
        union_idx: pd.DatetimeIndex | None = None
        for df in data.values():
            idx = pd.to_datetime(df.index)
            union_idx = idx if union_idx is None else union_idx.union(idx)
        if union_idx is None or len(union_idx) == 0:
            return []

        start_ts = pd.Timestamp(start)
        end_ts = pd.Timestamp(end)
        trading_days = union_idx[(union_idx >= start_ts) & (union_idx <= end_ts)].sort_values()
        if len(trading_days) == 0:
            return []

        if freq.upper() == "D":
            return list(trading_days)

        # 周频：每周第一个交易日
        rebalance: list[pd.Timestamp] = []
        seen_weeks: set[tuple[int, int]] = set()
        for ts in trading_days:
            iso = ts.isocalendar()  # .year, .week
            key = (iso[0], iso[1])
            if key not in seen_weeks:
                seen_weeks.add(key)
                rebalance.append(ts)
        return rebalance

    def _simulate(
        self,
        data: dict[str, pd.DataFrame],
        rebalance_dates: list[pd.Timestamp],
        start: str,
        end: str,
    ) -> BacktestReport:
        # 持仓：ticker -> OpenPosition
        open_positions: dict[str, OpenPosition] = {}
        all_trades: list[TradeRecord] = []

        # 权益追踪：等权分仓
        equity = 1.0  # 归一化为 1.0 起始
        equity_records: list[tuple[pd.Timestamp, float]] = []

        # 构建每日收盘价查找表
        close_lookup: dict[str, pd.Series] = {}
        for tk, df in data.items():
            s = df["close"].copy()
            s.index = pd.to_datetime(s.index)
            close_lookup[tk] = s

        # 全交易日并集（用于逐日 mark-to-market）
        all_dates: pd.DatetimeIndex | None = None
        for df in data.values():
            idx = pd.to_datetime(df.index)
            all_dates = idx if all_dates is None else all_dates.union(idx)
        all_dates = all_dates.sort_values() if all_dates is not None else pd.DatetimeIndex([])

        start_ts = pd.Timestamp(start)
        end_ts = pd.Timestamp(end)
        sim_dates = all_dates[(all_dates >= start_ts) & (all_dates <= end_ts)]

        rebalance_set = set(rebalance_dates)
        per_position_value = 1.0 / self.max_positions  # 每个仓位初始目标权重

        for current_date in sim_dates:
            # ---- a) 先处理已持仓的止损/超时（用当日收盘价）----
            for tk in list(open_positions.keys()):
                pos = open_positions[tk]
                trade = pos.trade
                price_today = self._safe_price(close_lookup, tk, current_date)
                if price_today is None:
                    continue

                # 止损：含滑点的净买入价为基准
                # 触发条件：当日收盘价已使净价较 entry 跌幅 ≥ stop_loss_pct
                # 简化：用 (price_today / entry_executed - 1) ≤ -stop_loss_pct
                # 注意 entry_price 已含滑点，不含 commission
                raw_loss = price_today / trade.entry_price - 1.0 if trade.entry_price > 0 else 0.0
                if raw_loss <= -self.stop_loss_pct:
                    trade.close(
                        exit_date=current_date,
                        raw_exit_price=price_today,
                        exit_reason="stop_loss",
                        commission_pct=self.commission_pct,
                        slippage_pct=self.slippage_pct,
                    )
                    all_trades.append(trade)
                    del open_positions[tk]
                    continue

                # 超时
                holding_days = (current_date - trade.entry_date).days
                if holding_days > self.time_limit_days:
                    trade.close(
                        exit_date=current_date,
                        raw_exit_price=price_today,
                        exit_reason="time_limit",
                        commission_pct=self.commission_pct,
                        slippage_pct=self.slippage_pct,
                    )
                    all_trades.append(trade)
                    del open_positions[tk]
                    continue

            # ---- b) 调仓日处理 ----
            if current_date in rebalance_set:
                # 先评估所有 ticker 当日综合分
                scores: dict[str, float] = {}
                for tk, df in data.items():
                    df_up_to = df.loc[df.index <= current_date]
                    if df_up_to.empty or len(df_up_to) < 30:
                        continue
                    try:
                        score = self._composite_score(tk, df_up_to)
                    except Exception as e:
                        log.debug(f"[score error] {tk} @ {current_date.date()}: {e}")
                        continue
                    scores[tk] = score

                # b.1 已持仓：信号退出
                for tk in list(open_positions.keys()):
                    score = scores.get(tk)
                    if score is not None and score < self.exit_threshold:
                        price_today = self._safe_price(close_lookup, tk, current_date)
                        if price_today is None:
                            continue
                        pos = open_positions[tk]
                        pos.trade.close(
                            exit_date=current_date,
                            raw_exit_price=price_today,
                            exit_reason="signal_exit",
                            commission_pct=self.commission_pct,
                            slippage_pct=self.slippage_pct,
                        )
                        all_trades.append(pos.trade)
                        del open_positions[tk]

                # b.2 新开仓
                slots = self.max_positions - len(open_positions)
                if slots > 0:
                    candidates = [
                        (tk, sc)
                        for tk, sc in scores.items()
                        if tk not in open_positions and sc >= self.entry_threshold
                    ]
                    candidates.sort(key=lambda x: -x[1])
                    for tk, sc in candidates[:slots]:
                        price_today = self._safe_price(close_lookup, tk, current_date)
                        if price_today is None or price_today <= 0:
                            continue
                        # 买入滑点：成交价高于收盘价
                        entry_executed = price_today * (1.0 + self.slippage_pct)
                        trade = TradeRecord(
                            ticker=tk,
                            entry_date=current_date,
                            entry_price=entry_executed,
                            shares=per_position_value,  # 占总权益的比例
                        )
                        open_positions[tk] = OpenPosition(trade)

            # ---- c) 每日 mark-to-market ----
            # 当前权益 = 现金部分（空仓比例） + 各持仓按当日收盘价计算的浮动盈亏
            used_slots = len(open_positions)
            cash_weight = (self.max_positions - used_slots) * per_position_value
            positions_value = 0.0
            for tk, pos in open_positions.items():
                price_today = self._safe_price(close_lookup, tk, current_date)
                if price_today is None:
                    # 用上一个已知价
                    price_today = pos.trade.entry_price
                # 单仓位价值 = 投入比例 × (今日价 / 买入价)
                # 这里 entry_price 是含滑点的执行价，再用 commission 调整名义
                buy_notional = pos.trade.entry_price * (1.0 + self.commission_pct)
                cur_notional = price_today * (1.0 - self.commission_pct)  # 假如现在卖
                if buy_notional > 0:
                    positions_value += per_position_value * (cur_notional / buy_notional)
                else:
                    positions_value += per_position_value
            day_equity = cash_weight + positions_value
            equity_records.append((current_date, float(day_equity)))

        # ---- d) 回测结束：强制平仓所有持仓（end_of_data）----
        for tk in list(open_positions.keys()):
            pos = open_positions[tk]
            price_today = self._safe_price(close_lookup, tk, sim_dates[-1]) if len(sim_dates) else None
            if price_today is None:
                price_today = pos.trade.entry_price  # 退化
            pos.trade.close(
                exit_date=sim_dates[-1] if len(sim_dates) else pos.trade.entry_date,
                raw_exit_price=price_today,
                exit_reason="end_of_data",
                commission_pct=self.commission_pct,
                slippage_pct=self.slippage_pct,
            )
            all_trades.append(pos.trade)
            del open_positions[tk]

        # 构建权益曲线
        if equity_records:
            idx = pd.DatetimeIndex([r[0] for r in equity_records])
            vals = [r[1] for r in equity_records]
            equity_curve = pd.Series(vals, index=idx)
            # 归一化起点为 1.0
            if equity_curve.iloc[0] != 0:
                equity_curve = equity_curve / equity_curve.iloc[0]
        else:
            equity_curve = pd.Series(dtype=float)

        return BacktestReport(
            start=start,
            end=end,
            trades=all_trades,
            equity_curve=equity_curve,
            _max_positions=self.max_positions,
            _signals=[s.name for s in self.signals],
        )

    # ----------------------------- 辅助函数 -----------------------------

    def _composite_score(self, ticker: str, df_up_to: pd.DataFrame) -> float:
        """加权综合信号得分"""
        weighted_sum = 0.0
        for sig in self.signals:
            sr = sig.evaluate(ticker, df_up_to)
            weighted_sum += self.weights.get(sig.name, 0.0) * sr.value
        # clamp 到 [-1, 1]
        return max(-1.0, min(1.0, weighted_sum))

    @staticmethod
    def _safe_price(
        close_lookup: dict[str, pd.Series],
        ticker: str,
        date: pd.Timestamp,
    ) -> Optional[float]:
        s = close_lookup.get(ticker)
        if s is None or s.empty:
            return None
        if date in s.index:
            v = s.loc[date]
            return float(v) if v == v else None  # NaN check
        # 找最近的前一个交易日（防止数据稀疏）
        try:
            prior = s.loc[:date]
            if prior.empty:
                return None
            v = prior.iloc[-1]
            return float(v) if v == v else None
        except Exception:
            return None
