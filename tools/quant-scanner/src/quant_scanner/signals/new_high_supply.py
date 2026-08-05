"""New High + Supply/Demand Signal — O'Neil N+S 字母独立精确版

来源：《笑傲股市》第 6 章（N 新催化）+ 第 3 章（S 供需）。

抽出为独立信号的原因：
1. can_slim.py 内部已有简化 N/S 评估，但缺内部人 cluster buying、回购、催化剂等维度
2. N+S 字母对短线择时关键（创新高+供需紧张=强势股主升浪）
3. 单独可调用，便于非 CAN SLIM 场景使用

精确规则（O'Neil 原书 + skill E 段）：
- **N 字母**（新高）：
  - 52 周新高（close == 252 日 high）→ 1.0
  - 距高 ≤ 5% → 0.7；≤ 15% → 0.4；> 15% → 0.0
  - 催化剂加分（数据源限制，简化为 0.2 占位；生产可手工注入）
- **S 字母**（供需）：
  - 流通股分级：< 2.5 亿 → 1.0；2.5-5 亿 → 0.7；5-10 亿 → 0.5；> 10 亿 → 0.3
  - 内部人 cluster buying（近 90 天 Form 4 ≥ 3 笔）→ +0.2
  - 回购执行（yfinance buyback 非零）→ +0.1

输出 SignalResult.value = 0.5*N + 0.5*S。
"""
from __future__ import annotations

import logging
from typing import Optional

import pandas as pd

from .base import BaseSignal, SignalResult
from ..data.loader import DataLoader

log = logging.getLogger(__name__)


class NewHighSupplySignal(BaseSignal):
    name = "new_high_supply"
    threshold = 0.7

    # N 字母阈值
    near_high_threshold: float = 0.05      # 距 52 周高 ≤ 5% → 0.7
    mid_high_threshold: float = 0.15       # 距 52 周高 ≤ 15% → 0.4
    lookback_weeks: int = 52

    # S 字母阈值（流通股分级）
    shares_tier_1: float = 250e6           # < 2.5 亿 → 1.0
    shares_tier_2: float = 500e6           # 2.5-5 亿 → 0.7
    shares_tier_3: float = 1000e6          # 5-10 亿 → 0.5

    # 内部人 cluster buying
    insider_cluster_min_count: int = 3
    insider_lookback_days: int = 90

    def __init__(
        self,
        threshold: Optional[float] = None,
        loader: DataLoader | None = None,
        catalyst_bonus: float = 0.0,
        **kwargs,
    ):
        """
        Args:
            loader: 数据加载器（必须，用于基本面和 EDGAR）
            catalyst_bonus: 手工注入的催化剂加分（0-0.2）。
                            生产中通常由用户/上游策略注入（财报缺口、产品发布等）。
        """
        super().__init__(threshold=threshold, **kwargs)
        self._loader = loader or DataLoader()
        self.catalyst_bonus = max(0.0, min(0.2, catalyst_bonus))

    # -------------------------- N 字母 --------------------------

    def _evaluate_n(self, df: pd.DataFrame) -> tuple[float, dict]:
        """N 字母：距 52 周新高评分"""
        details: dict = {}
        if len(df) < 60:
            return 0.0, {"error": "数据不足"}

        lookback = min(self.lookback_weeks * 5, len(df))  # 52 周 ≈ 252 交易日
        window = df["close"].tail(lookback)
        high = float(window.max())
        last_close = float(df["close"].iloc[-1])
        dist_pct = (last_close - high) / high if high > 0 else 0.0
        details["high_52w"] = high
        details["last_close"] = last_close
        details["dist_from_high_pct"] = dist_pct * 100

        # 5 日内是否创新高（>= 52 周高 * 0.999）
        recent_5d_high = float(df["close"].tail(5).max())
        details["new_high_5d"] = bool(recent_5d_high >= high * 0.999)

        if recent_5d_high >= high * 0.999:
            n_score = 1.0
        elif abs(dist_pct) <= self.near_high_threshold:
            n_score = 0.7
        elif abs(dist_pct) <= self.mid_high_threshold:
            n_score = 0.4
        else:
            n_score = 0.0

        # 催化剂加分（外部注入）
        if self.catalyst_bonus > 0:
            n_score = min(1.0, n_score + self.catalyst_bonus)
            details["catalyst_bonus"] = self.catalyst_bonus

        details["n_score"] = n_score
        return n_score, details

    # -------------------------- S 字母 --------------------------

    def _evaluate_s(self, ticker: str) -> tuple[float, dict]:
        """S 字母：流通股 + 内部人 cluster + 回购"""
        details: dict = {}
        fund = self._loader.load_fundamentals(ticker) or {}
        shares = fund.get("shares_outstanding")
        details["shares_outstanding"] = shares

        if shares is None:
            return 0.5, details  # 数据缺失返回中性

        # 流通股分级
        if shares < self.shares_tier_1:
            s_score = 1.0
        elif shares < self.shares_tier_2:
            s_score = 0.7
        elif shares < self.shares_tier_3:
            s_score = 0.5
        else:
            s_score = 0.3
        details["base_s_score"] = s_score

        # 内部人 cluster buying（EDGAR Form 4 计数 ≥ 3 笔/90 天）
        if hasattr(self._loader, "load_insider_form4_count"):
            try:
                form4_count = self._loader.load_insider_form4_count(ticker, days=self.insider_lookback_days)
                if isinstance(form4_count, int):
                    details["insider_form4_count"] = form4_count
                    if form4_count >= self.insider_cluster_min_count:
                        s_score = min(1.0, s_score + 0.2)
                        details["insider_cluster"] = True
                    else:
                        details["insider_cluster"] = False
            except Exception as e:
                log.debug(f"[S letter] Form 4 计数失败: {e}")

        # 回购执行（yfinance fund 无 buyback 字段时跳过）
        buyback = fund.get("buyback") or fund.get("share_repurchase")
        if buyback:
            s_score = min(1.0, s_score + 0.1)
            details["buyback_active"] = True
        else:
            details["buyback_active"] = False

        details["s_score"] = s_score
        return s_score, details

    # -------------------------- 主评估 --------------------------

    def evaluate(self, ticker: str, df: pd.DataFrame, pit_date: Optional[pd.Timestamp] = None) -> SignalResult:
        """评估 N+S 字母

        Args:
            ticker: 股票代码
            df: 日线数据
            pit_date: 可选 PIT 日期（本信号以 df 截断为准，pit_date 仅作标记）
        """
        reasons: list[str] = []
        details: dict = {}

        # N 字母
        n_score, n_details = self._evaluate_n(df)
        details["n"] = n_details  # 整体存为 n 子字典
        details["n_score"] = n_score
        if "error" in n_details:
            return SignalResult(
                ticker=ticker, signal_name=self.name, value=0.0, passed=False,
                details=details, reasons=[n_details["error"]],
            )
        dist_pct = n_details.get("dist_from_high_pct", 0)
        new_high = n_details.get("new_high_5d", False)
        reasons.append(
            f"N 距 52 周高 {dist_pct:.1f}%（{'5 日内创新高 ✓' if new_high else ''}）"
            f"→ N={n_score:.2f}"
        )

        # S 字母（需 loader；PIT 模式跳过，返回中性）
        if pit_date is not None:
            s_score = 0.5
            details["s"] = {"skipped": "PIT 模式跳过基本面"}
            details["s_score"] = s_score
            reasons.append("S 字母（PIT）中性 0.5")
        else:
            try:
                s_score, s_details = self._evaluate_s(ticker)
                details["s"] = s_details
                details["s_score"] = s_score
                shares = s_details.get("shares_outstanding")
                cluster = s_details.get("insider_cluster", False)
                buyback = s_details.get("buyback_active", False)
                reasons.append(
                    f"S 流通股 {shares/1e6:.0f}M" if shares else "S 流通股缺失"
                )
                if cluster:
                    reasons.append(f"  内部人 cluster buying（Form 4 ≥ {self.insider_cluster_min_count} 笔）+0.2")
                if buyback:
                    reasons.append("  回购执行 +0.1")
                reasons.append(f"→ S={s_score:.2f}")
            except Exception as e:
                log.warning(f"[new_high_supply] S 字母失败: {e}")
                s_score = 0.5
                details["s"] = {"error": str(e)}
                details["s_score"] = s_score
                reasons.append(f"S 评估失败（{e}）→ 中性 0.5")

        # 综合：N+S 等权
        score = 0.5 * n_score + 0.5 * s_score
        details["letters"] = {"N": float(n_score), "S": float(s_score)}
        passed = score >= self.threshold

        return SignalResult(
            ticker=ticker, signal_name=self.name,
            value=float(score), passed=bool(passed),
            details=details, reasons=reasons,
        ).clamp()
