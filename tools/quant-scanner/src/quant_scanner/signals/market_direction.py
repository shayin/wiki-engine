"""大盘方向信号（Market Direction）— O'Neil 精确版独立信号

来源：《笑傲股市》第 7 章 — CAN SLIM 中 M 字母的独立实现。

抽出为独立信号的原因：
1. 大盘方向不只服务 CAN SLIM，所有个股策略都应在大盘择时过滤下运行
2. FTD（Follow-Through Day）+ Distribution Day 是 O'Neil 50 年实践的核心状态机
3. can_slim.py 内部已实现，此模块提供单独调用的入口

精确规则（O'Neil 原书）：
- **Distribution Day**：指数单日跌 ≥ 0.5% 且（放量 or 位于 50 日均量之上）
  - 25 个交易日内 ≥ 4 个 = 大盘承压
  - 5 个交易日内出现"失效"反弹 = 取消
- **FTD（Follow-Through Day）**：
  - 通常在尝试反弹的第 4-7 个交易日出现
  - 当日指数涨幅 ≥ 1.3%
  - 当日成交量 > 前日成交量 + 大于 50 日均量

状态机四档：
- 确认上涨（FTD + MA50>MA200）→ score = 1.0
- 健康上涨（MA50>MA200，无近期 FTD）→ score = 0.8
- 尝试反弹（FTD 但 MA50<MA200）→ score = 0.5
- 压力（4+ 分布日无 FTD）→ score = 0.0
- 下跌（其他）→ score = 0.2
"""
from __future__ import annotations

import logging
from typing import Optional

import pandas as pd

from .base import BaseSignal, SignalResult
from ..data.loader import DataLoader

log = logging.getLogger(__name__)


class MarketDirectionSignal(BaseSignal):
    name = "market_direction"
    threshold = 0.70

    # Distribution Day 阈值
    distribution_drop_pct: float = -0.005  # SPX 单日跌幅 ≤ -0.5%
    distribution_window_days: int = 25
    distribution_alarm_count: int = 4

    # FTD 阈值
    ftd_min_day_offset: int = 4   # 尝试反弹的第 4 日后
    ftd_gain_pct: float = 0.013   # 涨幅 ≥ 1.3%
    ftd_vol_threshold: float = 1.0  # 量 ≥ 50 日均量

    market_ticker: str = "^GSPC"

    def __init__(self, threshold: Optional[float] = None, loader: DataLoader | None = None,
                 market_ticker: str | None = None):
        super().__init__(threshold=threshold)
        self._loader = loader or DataLoader()
        self._market_cache: pd.DataFrame | None = None
        if market_ticker:
            self.market_ticker = market_ticker

    def _load_market(self, period: str = "2y") -> pd.DataFrame:
        if self._market_cache is None or self._market_cache.empty:
            self._market_cache = self._loader.load(self.market_ticker, period=period)
        return self._market_cache

    def evaluate(self, ticker: str = "^GSPC", df: pd.DataFrame | None = None) -> SignalResult:
        """评估大盘方向

        Args:
            ticker: 通常忽略。如果 df 为 None，从 loader 拉市场数据
            df: 可选，传入市场数据避免重复拉取
        """
        market = df if df is not None else self._load_market()
        if market.empty or len(market) < 60:
            return SignalResult(
                ticker=self.market_ticker, signal_name=self.name, value=0.5, passed=False,
                details={"error": "数据不足"}, reasons=["市场数据不足 60 bar"],
            )

        score, state, details = self._evaluate_state(market)
        reasons = [f"大盘状态：{state}（score={score:.2f}）"]

        # 计算关键指标
        dist_count = details.get("distribution_days_count", 0)
        ftd_recent = details.get("ftd_recent", False)
        ma_aligned = details.get("ma50_vs_ma200")

        if dist_count >= self.distribution_alarm_count:
            reasons.append(f"⚠️ 分布日 {dist_count} 个（≥ {self.distribution_alarm_count}）= 承压")
        if ftd_recent:
            reasons.append("✓ 近期出现 FTD 信号")
        if ma_aligned is not None:
            reasons.append(f"MA50 {'>' if ma_aligned else '<'} MA200")

        passed = score >= self.threshold
        return SignalResult(
            ticker=self.market_ticker, signal_name=self.name,
            value=float(score), passed=bool(passed),
            details=details, reasons=reasons,
        ).clamp()

    def _evaluate_state(self, market: pd.DataFrame) -> tuple[float, str, dict]:
        """核心状态机"""
        try:
            close = market["close"]
            volume = market.get("volume", pd.Series(dtype=float))

            recent_close = close.tail(self.distribution_window_days + 1)
            daily_ret = recent_close.pct_change()

            # 分布日计数
            dist_days = []
            for i in range(1, len(daily_ret)):
                if daily_ret.iloc[i] <= self.distribution_drop_pct:
                    if not volume.empty and len(volume) > i:
                        vol_ma50 = volume.rolling(50).mean()
                        idx_in_vol = len(volume) - len(daily_ret) + i
                        if 0 <= idx_in_vol < len(vol_ma50):
                            vol_ma_val = vol_ma50.iloc[idx_in_vol]
                            if not pd.isna(vol_ma_val) and volume.iloc[idx_in_vol] > vol_ma_val:
                                dist_days.append(recent_close.index[i])
                        else:
                            dist_days.append(recent_close.index[i])
                    else:
                        dist_days.append(recent_close.index[i])
            dist_count = len(dist_days)

            # FTD 信号：最近 6 个交易日内是否有"放量 ≥ 1.3%"的反弹日
            last6 = close.tail(6)
            last6_ret = last6.pct_change().fillna(0)
            vol_ma50 = volume.rolling(50).mean().iloc[-1] if not volume.empty else None

            ftd_signal = False
            ftd_date = None
            if not volume.empty and vol_ma50 and not pd.isna(vol_ma50):
                last6_vol = volume.tail(6)
                for i in range(1, len(last6)):
                    if last6_ret.iloc[i] >= self.ftd_gain_pct:
                        if last6_vol.iloc[i] >= vol_ma50:
                            ftd_signal = True
                            ftd_date = last6.index[i]
                            break

            # MA50 vs MA200
            ma50 = close.rolling(50).mean().iloc[-1]
            ma200 = close.rolling(200).mean().iloc[-1] if len(close) >= 200 else None
            ma_aligned = bool(ma50 > ma200) if ma200 is not None and not pd.isna(ma50) and not pd.isna(ma200) else None

            # 状态判定
            if dist_count >= self.distribution_alarm_count and not ftd_signal:
                score = 0.0
                state = "压力（分布日堆积，无 FTD）"
            elif ftd_signal and (ma200 is None or ma50 > ma200):
                score = 1.0
                state = "确认上涨（FTD 触发 + MA 排列）"
            elif ftd_signal:
                score = 0.5
                state = "尝试反弹（FTD 待 MA 确认）"
            elif ma200 is not None and ma50 > ma200:
                score = 0.8
                state = "健康上涨（MA50>MA200）"
            else:
                score = 0.2
                state = "下跌/弱势（MA50<MA200）"

            details = {
                "distribution_days_count": int(dist_count),
                "distribution_days": [str(d.date()) for d in dist_days[-5:]],
                "ftd_recent": bool(ftd_signal),
                "ftd_date": str(ftd_date.date()) if ftd_date else None,
                "ma50": float(ma50) if not pd.isna(ma50) else None,
                "ma200": float(ma200) if ma200 is not None and not pd.isna(ma200) else None,
                "ma50_vs_ma200": ma_aligned,
                "state": state,
                "score": float(score),
            }
            return score, state, details
        except Exception as e:
            log.warning(f"市场方向状态机失败: {e}")
            return 0.5, f"评估错误: {e}", {"error": str(e)}
