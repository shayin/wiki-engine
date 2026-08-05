"""RS Rating — O'Neil 全市场 12 月相对强度百分位

来源：William O'Neil《笑傲股市》第 5 章 L 字母 + 第 14 章 100 案例验证。
RS Rating = 个股过去 12 个月（252 交易日）价格变动率在"宇宙"中的百分位（1-99）。
O'Neil 原版用全市场（S&P 1500 / Russell 3000）；本实现宇宙可配置，默认从
`data/watchlist.txt` 读，可传入更大列表（如 S&P 500）做更接近全市场的百分位。

阈值（O'Neil 经典）：
- RS Rating ≥ 85 = 领涨股（满分）
- 70-84 = 候选
- < 70 = 强制不买（即使 CAN SLIM 其他 6 字母满分）

历史验证：1953-1993 年 500 只最佳表现股票中，3/4 在主升浪启动时 RS Rating ≥ 87。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from .base import BaseSignal, SignalResult
from ..data.loader import DataLoader
from ..data.sp500 import load_sp500_tickers
from ..utils.config import PROJECT_ROOT

log = logging.getLogger(__name__)

DEFAULT_WATCHLIST = PROJECT_ROOT / "data" / "watchlist.txt"


def load_watchlist(path: Path = DEFAULT_WATCHLIST) -> list[str]:
    """读 watchlist.txt，跳过 # 注释和空行。"""
    if not path.exists():
        log.warning(f"[rs_rating] watchlist 不存在: {path}")
        return []
    tickers: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            tickers.append(line.upper())
    return tickers


class RSRatingSignal(BaseSignal):
    """O'Neil RS Rating：12 月价格变动率在宇宙中的百分位（1-99）。"""

    name = "rs_rating"
    threshold = 0.85  # value ≥ 0.85 ≈ RS Rating ≥ 85 = 领涨股

    lookback: int = 252              # 12 月 ≈ 252 交易日
    min_universe: int = 10           # 宇宙 < 10 只时百分位不可靠，降级中性
    strong_rating: int = 85          # O'Neil 领涨线
    passing_rating: int = 70         # O'Neil 不买线（< 70 强制不买）

    def __init__(
        self,
        universe: Optional[list[str]] = None,
        loader: DataLoader | None = None,
        use_sp500: bool = True,
    ):
        """
        Args:
            universe: 显式宇宙 ticker 列表（最高优先级）。
            loader: 数据加载器（测试可注入 mock）。
            use_sp500: universe=None 时是否默认拉 S&P 500（True=生产默认）。
                       False = 回退到 data/watchlist.txt。
        """
        super().__init__()
        self._loader = loader or DataLoader()
        self._universe = universe
        self._use_sp500 = use_sp500
        # 宇宙 12 月收益缓存（按 pit_date 区分，避免回测重复拉取）
        self._universe_returns: Optional[dict[str, float]] = None
        self._universe_cache_key: object = None

    # -------------------------- 宇宙管理 --------------------------

    def _get_universe(self) -> list[str]:
        if self._universe is not None:
            return self._universe
        if self._use_sp500:
            sp500 = load_sp500_tickers()
            if sp500:
                return sp500
            log.warning("[rs_rating] SP500 拉取失败，回退 watchlist")
        return load_watchlist()

    def _load_universe_returns(
        self, pit_date: Optional[pd.Timestamp] = None
    ) -> dict[str, float]:
        """计算宇宙内所有 ticker 的 12 月收益（带缓存）。

        pit_date 非 None 时按 PIT 截断（回测合规，不含未来数据）。
        """
        cache_key = pit_date.date() if pit_date is not None else "latest"
        if self._universe_returns is not None and self._universe_cache_key == cache_key:
            return self._universe_returns

        uni = self._get_universe()
        returns: dict[str, float] = {}
        for t in uni:
            try:
                if pit_date is not None:
                    end = pit_date.strftime("%Y-%m-%d")
                    # lookback*1.5 日历日 ≈ 覆盖 252 交易日（含周末节假日余量）
                    start = (pit_date - pd.Timedelta(days=int(self.lookback * 1.6))).strftime("%Y-%m-%d")
                    # PIT 模式禁用磁盘缓存：避免历史切片污染 {TICKER}.parquet 实时缓存
                    # 内存缓存（_universe_returns）由本方法自己管理
                    df = self._loader.load(t, start=start, end=end, use_cache=False)
                else:
                    df = self._loader.load(t, period="2y")
                if df is None or df.empty or len(df) < self.lookback:
                    continue
                close = df["close"]
                returns[t] = float(close.iloc[-1] / close.iloc[-self.lookback] - 1.0)
            except Exception as e:
                log.debug(f"[rs_universe] {t} 拉取失败: {e}")
                continue

        self._universe_returns = returns
        self._universe_cache_key = cache_key
        return returns

    # -------------------------- RS Rating 计算 --------------------------

    def compute_rs_rating(
        self, ticker_return: float, universe_returns: dict[str, float]
    ) -> int:
        """算 RS Rating（1-99 百分位）。

        百分位 = 宇宙中 12 月收益 ≤ ticker 的比例（含 ticker 自身）。
        """
        all_returns = list(universe_returns.values()) + [ticker_return]
        n = len(all_returns)
        rank = sum(1 for r in all_returns if r <= ticker_return)
        rating = int(round(rank / n * 99))
        return max(1, min(99, rating))

    # -------------------------- 主评估入口 --------------------------

    def evaluate(self, ticker: str, df: pd.DataFrame, pit_date: Optional[pd.Timestamp] = None) -> SignalResult:
        """评估 RS Rating

        Args:
            ticker: 股票代码
            df: 日线数据
            pit_date: **回测 PIT 日期**（推荐显式传入）。None 时走启发式（df 距今 >30 天 = 回测）。
        """
        reasons: list[str] = []
        details: dict = {}

        if len(df) < self.lookback:
            return SignalResult(
                ticker=ticker, signal_name=self.name, value=0.0, passed=False,
                details={"error": f"数据不足（需 ≥{self.lookback} 行）"},
                reasons=[f"数据不足（需 ≥{self.lookback} 行，当前 {len(df)}）"],
            )

        # PIT 判定（优先级：显式 pit_date > 启发式 > 默认实时）
        if pit_date is None:
            df_last_date = df.index[-1] if not df.empty else None
            if df_last_date is not None:
                today = pd.Timestamp.now().normalize()
                heuristic_age = (today - df_last_date).days
                if heuristic_age > 30:
                    pit_date = df_last_date
                    log.warning(
                        f"RSRatingSignal PIT 启发式触发：df 截至 {df_last_date.date()}，"
                        f"距今 {heuristic_age} 天 >30 阈值，自动进入回测模式。"
                    )
        is_backtest = pit_date is not None

        # ticker 的 12 月收益
        close = df["close"]
        stock_ret = float(close.iloc[-1] / close.iloc[-self.lookback] - 1.0)

        # 宇宙 12 月收益（回测模式按 pit_date 截断，实时模式拉最新）
        uni_returns = self._load_universe_returns(
            pit_date=pit_date if is_backtest else None
        )

        details["stock_12m_return_pct"] = stock_ret * 100
        details["universe_size"] = len(uni_returns)

        # 宇宙太小 → 百分位不可靠，降级中性
        if len(uni_returns) < self.min_universe:
            reasons.append(
                f"⚠️ 宇宙仅 {len(uni_returns)} 只（< {self.min_universe}），"
                f"RS Rating 不可靠，返回中性 0.5。建议 universe ≥ 50 或传入 S&P 500 列表"
            )
            return SignalResult(
                ticker=ticker, signal_name=self.name, value=0.5, passed=False,
                details=details, reasons=reasons,
            )

        rs_rating = self.compute_rs_rating(stock_ret, uni_returns)
        score = rs_rating / 99.0
        passed = rs_rating >= self.strong_rating

        # verdict（O'Neil 三档）
        if rs_rating >= self.strong_rating:
            verdict = "领涨股（≥85）"
        elif rs_rating >= self.passing_rating:
            verdict = f"候选（{self.passing_rating}-{self.strong_rating - 1}）"
        else:
            verdict = f"弱势（<{self.passing_rating}，强制不买）"

        uni_median = float(np.median(list(uni_returns.values()))) if uni_returns else 0.0
        details.update({
            "rs_rating": int(rs_rating),
            "universe_median_return_pct": uni_median * 100,
            "verdict": verdict,
            "is_backtest": is_backtest,
        })
        reasons.append(
            f"RS Rating {rs_rating}（12 月 {stock_ret * 100:+.1f}% vs 宇宙 {len(uni_returns)} 只，"
            f"中位数 {uni_median * 100:+.1f}%）→ {verdict} {'✓' if passed else '✗'}"
            f"（要求 ≥ {self.strong_rating}）"
        )

        return SignalResult(
            ticker=ticker, signal_name=self.name, value=float(score), passed=bool(passed),
            details=details, reasons=reasons,
        ).clamp()
