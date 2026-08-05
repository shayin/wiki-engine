"""CAN SLIM 信号 — O'Neil 精确版

来源：William O'Neil《笑傲股市》— 对 1953-1993 年 500 只最佳表现股票的回溯研究。

7 个字母的精确阈值（基于 O'Neil 第 2 版原书 + IBD 50 年实践）：
- C: Current quarterly EPS — 最近季度 EPS 同比 ≥ 25%（满分 ≥ 40%+ 且连续 3 季度加速）
- A: Annual EPS growth — 3 年年化增长率 ≥ 25%，ROE ≥ 17%
- N: New product/catalyst — 距 52 周高 ≤ 15%（满分：5 日内创新高）
- S: Supply & demand — 流通股 < 500M（小盘更好）+ 机构买入 + 回购
- L: Leader or laggard — 12 月 + 60 日双层 RS（vs SPX）
- I: Institutional sponsorship — 机构持仓 ≥ 30%（满分：最近 4 季度机构增持）
- M: Market direction — FTD + Distribution Day 简化状态机

每个字母权重 1/7 ≈ 14.3%。

**数据源优先级**（C/A 字母精修）：
1. SEC EDGAR（GAAP 精确）：`loader.load_edgar_quarterly_eps_yoy` 等
2. yfinance（fallback）：`loader.load_eps_history`

M 字母：调用 `MarketDirectionSignal`（统一接口，不再重复实现）。
"""
from __future__ import annotations

import logging
from typing import Optional

import pandas as pd

from .base import BaseSignal, SignalResult
from .rs_rating import RSRatingSignal
from .market_direction import MarketDirectionSignal
from ..data.loader import DataLoader

log = logging.getLogger(__name__)


class CANSLIMSignal(BaseSignal):
    name = "can_slim"
    threshold = 0.7  # 7 个字母至少 5 个通过

    # C 字母（季度 EPS）
    min_quarterly_eps_yoy: float = 0.25   # ≥ 25%
    strong_quarterly_eps_yoy: float = 0.40  # 满分线 ≥ 40%

    # A 字母（年度 EPS）
    min_annual_eps_cagr: float = 0.25     # 3 年年化 ≥ 25%
    min_roe: float = 0.17                 # ROE ≥ 17% 加分校验
    roe_penalty_below: float = 0.10       # ROE < 10% 扣分

    # N 字母（距 52 周高）
    min_dist_from_high: float = -0.15     # 距 52 周高 ≤ 15%

    # S 字母（流通盘）
    max_shares_outstanding: float = 500e6  # 5 亿股以下满分

    # I 字母（机构持仓）
    min_institutional_pct: float = 0.30   # ≥ 30%
    min_institutional_holders: int = 10   # 机构数 ≥ 10 视为良好赞助
    moderate_holders: int = 5             # 机构数 5-10 视为中等赞助

    # L 字母（相对强度）
    min_rs_60d: float = 0.10              # 60 日 vs SPX ≥ 10%
    min_rs_12m: float = 0.0               # 12 月 vs SPX > 0%

    market_ticker: str = "^GSPC"

    def __init__(self, threshold: Optional[float] = None, loader: DataLoader | None = None,
                 skip_fundamentals: bool = False, rs_rating: Optional[RSRatingSignal] = None,
                 auto_inject_rs: bool = True,
                 market_direction: Optional[MarketDirectionSignal] | None = None):
        """
        Args:
            threshold: 自定义阈值
            loader: 数据加载器
            skip_fundamentals: True 时跳过基本面拉取（用于回测 PIT 合规），
                               此时 C/A/I/S 字母返回中性分 0.5
            rs_rating: 注入 RSRatingSignal 则 L 字母用 O'Neil 全市场百分位（精确版）；
                       None + auto_inject_rs=True → 自动注入默认 RSRatingSignal（SP500 宇宙）
            auto_inject_rs: True（生产默认）= rs_rating=None 时自动注入；
                            False = 保持向后兼容降级 vs SPX 双层 RS
            market_direction: 注入 MarketDirectionSignal 则 M 字母调用之；
                              None → 自动实例化（统一 M 字母实现）
        """
        super().__init__(threshold=threshold)
        self._loader = loader or DataLoader()
        self._market_cache: pd.DataFrame | None = None
        self.skip_fundamentals = skip_fundamentals
        # 生产默认：未显式注入则自动用 SP500 宇宙的 RSRatingSignal
        if rs_rating is None and auto_inject_rs:
            rs_rating = RSRatingSignal(loader=self._loader, use_sp500=True)
        self._rs_rating = rs_rating
        # M 字母统一走 MarketDirectionSignal（去除旧的 _evaluate_market_direction 内置实现）
        self._market_direction = market_direction or MarketDirectionSignal(loader=self._loader)

    def _load_market(self, pit_date: Optional[pd.Timestamp] = None) -> pd.DataFrame:
        """加载市场数据，回测时按 pit_date 截断（PIT 合规）。"""
        if self._market_cache is None or self._market_cache.empty:
            self._market_cache = self._loader.load(self.market_ticker, period="10y")
        market = self._market_cache
        if pit_date is not None and not market.empty:
            market = market.loc[market.index <= pit_date]
        return market

    # ==================== L 字母降级路径（vs SPX 双层 RS）====================

    def _evaluate_relative_strength(self, df: pd.DataFrame, pit_date: Optional[pd.Timestamp] = None) -> tuple[float, str, dict]:
        """双层相对强度：12 月 + 60 日 vs SPX（仅当 rs_rating 未注入时降级使用）"""
        try:
            market = self._load_market(pit_date=pit_date)
            if market.empty or len(df) < 60 or len(market) < 60:
                return 0.0, "数据不足", {}

            stock_close = df["close"]
            market_close = market["close"]

            # 60 日 RS
            stock_ret_60d = stock_close.iloc[-1] / stock_close.iloc[-60] - 1
            market_ret_60d = market_close.iloc[-1] / market_close.iloc[-60] - 1
            rs_60d = stock_ret_60d - market_ret_60d

            # 12 月 RS（约 252 交易日）
            if len(stock_close) >= 252 and len(market_close) >= 252:
                stock_ret_12m = stock_close.iloc[-1] / stock_close.iloc[-252] - 1
                market_ret_12m = market_close.iloc[-1] / market_close.iloc[-252] - 1
                rs_12m = stock_ret_12m - market_ret_12m
            else:
                n = min(len(stock_close), len(market_close)) - 1
                stock_ret_12m = stock_close.iloc[-1] / stock_close.iloc[-n] - 1
                market_ret_12m = market_close.iloc[-1] / market_close.iloc[-n] - 1
                rs_12m = stock_ret_12m - market_ret_12m

            s_60d = max(0.0, min(1.0, rs_60d / self.min_rs_60d)) if self.min_rs_60d > 0 else 0
            s_12m = max(0.0, min(1.0, (rs_12m + 0.10) / 0.20))
            score = 0.5 * s_60d + 0.5 * s_12m

            ok = rs_60d >= self.min_rs_60d and rs_12m >= self.min_rs_12m
            reason = f"60 日 RS {rs_60d*100:+.1f}%，12 月 RS {rs_12m*100:+.1f}% {'✓' if ok else '✗'}"
            details = {
                "rs_60d_pct": float(rs_60d * 100),
                "rs_12m_pct": float(rs_12m * 100),
                "rs_60d_score": float(s_60d),
                "rs_12m_score": float(s_12m),
            }
            return score, reason, details
        except Exception as e:
            return 0.0, f"评估错误: {e}", {}

    # ==================== C 字母（季度 EPS 同比 + 加速度）====================

    def _evaluate_letter_c(self, ticker: str, fund: dict, eps_history: dict) -> tuple[float, str, dict]:
        """C 字母：优先 EDGAR，失败回退 yfinance"""
        details: dict = {}
        # 优先 EDGAR（确认 loader 有该方法 + 返回真实 dict）
        if hasattr(self._loader, "load_edgar_quarterly_eps_yoy"):
            try:
                edgar_yoy = self._loader.load_edgar_quarterly_eps_yoy(ticker)
                if isinstance(edgar_yoy, dict) and edgar_yoy:
                    latest_yoy = float(list(edgar_yoy.values())[-1])
                    accelerating = bool(self._loader.load_edgar_eps_acceleration(ticker)) \
                        if hasattr(self._loader, "load_edgar_eps_acceleration") else False
                    details["c_eps_source"] = "edgar"
                    return self._score_letter_c(latest_yoy, accelerating, details), \
                        f"EDGAR 最近季 EPS 同比 {latest_yoy*100:+.1f}%", details
            except Exception as e:
                log.debug(f"[C letter] EDGAR 失败，回退 yfinance: {e}")

        # yfinance 回退路径
        q_yoy = eps_history.get("quarterly_yoy") if eps_history else None
        accelerating = eps_history.get("quarterly_accelerating") if eps_history else False
        if q_yoy and len(q_yoy) >= 1:
            latest_yoy = list(q_yoy.values())[-1]
            details["c_eps_source"] = "yfinance"
            return self._score_letter_c(latest_yoy, accelerating, details), \
                f"yfinance 最近季 EPS 同比 {latest_yoy*100:+.1f}%", details

        # 最后降级：quarterly_eps_growth 单点代理
        qg = fund.get("quarterly_eps_growth")
        details["c_eps_source"] = "proxy"
        if qg is not None:
            score = 1.0 if qg >= self.min_quarterly_eps_yoy else max(0.0, qg / self.min_quarterly_eps_yoy)
            return score, f"C 季度 EPS 同比（代理）{qg*100:+.1f}%", details
        return 0.0, "C 数据缺失 ✗", details

    def _score_letter_c(self, latest_yoy: float, accelerating: bool, details: dict) -> float:
        """C 字母评分核心算法（EDGAR/yfinance 共用）"""
        details["quarterly_eps_yoy"] = float(latest_yoy)
        details["eps_accelerating"] = bool(accelerating)
        if latest_yoy >= self.strong_quarterly_eps_yoy:
            score = 1.0
        elif latest_yoy >= self.min_quarterly_eps_yoy:
            score = 0.7
        else:
            score = max(0.0, latest_yoy / self.min_quarterly_eps_yoy)
        if accelerating and latest_yoy >= self.min_quarterly_eps_yoy:
            score = min(1.0, score + 0.2)
        return score

    # ==================== A 字母（年度 EPS CAGR + ROE + 利润率）====================

    def _evaluate_letter_a(self, ticker: str, fund: dict, eps_history: dict) -> tuple[float, str, dict]:
        """A 字母：3 年 CAGR + ROE/利润率趋势加分校验"""
        details: dict = {}
        # 优先 EDGAR CAGR（确认 loader 有方法 + 返回真实 float）
        cagr: float | None = None
        if hasattr(self._loader, "load_edgar_annual_eps_cagr"):
            try:
                val = self._loader.load_edgar_annual_eps_cagr(ticker, years=3)
                if isinstance(val, (int, float)):
                    cagr = float(val)
                    details["a_eps_source"] = "edgar"
            except Exception as e:
                log.debug(f"[A letter] EDGAR CAGR 失败，回退: {e}")

        if cagr is None:
            cagr = eps_history.get("annual_3y_cagr") if eps_history else None
            if cagr is not None:
                details["a_eps_source"] = "yfinance"

        if cagr is None:
            # 最后代理：forward vs trailing
            trail = fund.get("current_eps") or 0
            fwd = fund.get("forward_eps") or 0
            if trail and fwd and trail > 0:
                cagr = (fwd - trail) / abs(trail)
                details["a_eps_source"] = "proxy"
            else:
                details["a_eps_source"] = "missing"
                return 0.0, "A 数据缺失 ✗", details

        details["annual_eps_3y_cagr"] = float(cagr)
        score = 1.0 if cagr >= self.min_annual_eps_cagr else max(0.0, cagr / self.min_annual_eps_cagr)

        # ROE 校验（仅 EDGAR）
        if hasattr(self._loader, "load_edgar_roe"):
            try:
                roe = self._loader.load_edgar_roe(ticker)
                if isinstance(roe, (int, float)):
                    details["roe"] = float(roe)
                    if roe >= self.min_roe:
                        score = min(1.0, score + 0.05)
                    elif roe < self.roe_penalty_below:
                        score = max(0.0, score - 0.2)
                        details["roe_warning"] = f"ROE {roe*100:.1f}% < {self.roe_penalty_below*100:.0f}% 扣 0.2"
            except Exception as e:
                log.debug(f"[A letter] ROE 拉取失败: {e}")

        # 利润率趋势校验（仅 EDGAR）
        if hasattr(self._loader, "load_edgar_operating_margin_trend"):
            try:
                trend = self._loader.load_edgar_operating_margin_trend(ticker, n_years=3)
                if isinstance(trend, tuple) and len(trend) == 2:
                    margin, rising = trend
                    details["operating_margin"] = float(margin)
                    details["margin_rising"] = bool(rising)
                    if not rising:
                        score = max(0.0, score - 0.1)
                        details["margin_warning"] = "利润率 3 年下行扣 0.1"
            except Exception as e:
                log.debug(f"[A letter] 利润率趋势失败: {e}")

        return score, f"A CAGR {cagr*100:+.1f}%（来源: {details.get('eps_source', '?')}）", details

    # ==================== I 字母（机构持仓 + 季度净增持）====================

    def _evaluate_letter_i(self, ticker: str, fund: dict) -> tuple[float, str, dict]:
        """I 字母：优先 yfinance institutional_holders 详细数据（机构数 + 净增持方向），
        回退到 fund['institutional_pct']（仅百分比）。

        评分规则（O'Neil《笑傲股市》第 3 章）：
        - 机构数 ≥ 10 且 净增持方向（net_inc ≥ net_dec）→ 1.0
        - 机构数 ≥ 10                                  → 0.7
        - 机构数 ≥ 5                                   → 0.5
        - 机构数 < 5                                   → 0.0
        - 净增持 +0.2（clamp 1.0）；净减持 -0.1
        - 回退到 institutional_pct ≥ 30% → 0.7，线性映射
        """
        details: dict = {}

        # 优先详细机构数据
        if hasattr(self._loader, "load_institutional_holders"):
            try:
                inst = self._loader.load_institutional_holders(ticker)
                if isinstance(inst, dict) and inst and inst.get("holder_count", 0) > 0:
                    count = int(inst.get("holder_count", 0))
                    net_inc = int(inst.get("net_increasing", 0))
                    net_dec = int(inst.get("net_decreasing", 0))
                    total_chg = float(inst.get("total_change_shares", 0.0))
                    details["i_source"] = "yfinance_holders"
                    details["inst_holder_count"] = count
                    details["inst_net_increasing"] = net_inc
                    details["inst_net_decreasing"] = net_dec
                    if inst.get("latest_report_date"):
                        details["inst_latest_report_date"] = inst["latest_report_date"]

                    # 基础分（按机构数分级）
                    if count >= self.min_institutional_holders:
                        base = 0.7
                    elif count >= self.moderate_holders:
                        base = 0.5
                    else:
                        base = 0.0

                    # 方向调整
                    if net_inc >= net_dec and net_inc > 0:
                        base = min(1.0, base + 0.2)
                        details["inst_direction"] = "increasing"
                    elif net_dec > net_inc:
                        base = max(0.0, base - 0.1)
                        details["inst_direction"] = "decreasing"
                    else:
                        details["inst_direction"] = "neutral"

                    reason = f"机构数 {count}（净增持 {net_inc}/净减持 {net_dec}）"
                    return base, reason, details
            except Exception as e:
                log.debug(f"[I letter] institutional_holders 失败，回退 institutional_pct: {e}")

        # 回退：institutional_pct（heldPercentInstitutions）
        inst_pct = fund.get("institutional_pct") if fund else None
        if inst_pct is not None:
            details["i_source"] = "yfinance_pct"
            details["institutional_pct"] = float(inst_pct)
            if inst_pct >= self.min_institutional_pct:
                score = 1.0 if inst_pct >= 0.50 else 0.7
            else:
                score = max(0.0, float(inst_pct) / self.min_institutional_pct)
            return score, f"机构持仓占比 {inst_pct*100:.1f}%", details

        details["i_source"] = "missing"
        return 0.0, "I 数据缺失 ✗", details

    # ==================== 主评估入口 ====================

    def evaluate(self, ticker: str, df: pd.DataFrame, pit_date: Optional[pd.Timestamp] = None) -> SignalResult:
        """评估 CAN SLIM 7 字母

        Args:
            ticker: 股票代码
            df: 日线数据
            pit_date: **回测 PIT 日期**（推荐显式传入）。None 时：
                     - 走启发式（df 距今 >30 天 = 回测），并打 WARNING 日志
                     - 实时扫描必须显式传 None 或不传
        """
        reasons: list[str] = []
        details: dict = {}
        letter_scores: dict[str, float] = {}

        # PIT 判定
        df_last_date = df.index[-1] if not df.empty else None
        if pit_date is None and df_last_date is not None:
            today = pd.Timestamp.now().normalize()
            heuristic_age = (today - df_last_date).days
            if heuristic_age > 30:
                pit_date = df_last_date
                log.warning(
                    f"CANSLIMSignal PIT 启发式触发：df 截至 {df_last_date.date()}，"
                    f"距今 {heuristic_age} 天 >30 阈值，自动进入回测模式。"
                    f"建议回测引擎显式传 pit_date 参数避免误判。"
                )
        is_backtest = pit_date is not None
        skip_fund = self.skip_fundamentals or is_backtest

        if skip_fund:
            if is_backtest and not self.skip_fundamentals:
                reasons.append(f"⚠️ PIT 回测模式（数据截至 {pit_date.date()}）：C/A/S/I 用中性分 0.5 避免未来泄露")
            else:
                reasons.append("回测模式：C/A/S/I 字母使用中性分 0.5")
            fund = {}
            eps_history = {}
        else:
            fund = self._loader.load_fundamentals(ticker)
            if not fund:
                return SignalResult(
                    ticker=ticker, signal_name=self.name, value=0.0, passed=False,
                    details={"error": "基本面数据不可用"},
                    reasons=["yfinance 基本面数据不可用"],
                )
            eps_history = self._loader.load_eps_history(ticker)

        # ============ C: 当季 EPS 同比 + 加速度（EDGAR 精确版）============
        if skip_fund:
            c_score = 0.5
            reasons.append("C 字母（回测）中性 0.5")
        else:
            c_score, c_reason, c_details = self._evaluate_letter_c(ticker, fund, eps_history)
            latest_yoy = c_details.get("quarterly_eps_yoy")
            if latest_yoy is not None:
                ok_c = latest_yoy >= self.min_quarterly_eps_yoy
                accel = c_details.get("eps_accelerating", False)
                reasons.append(
                    f"C {c_reason} {'✓' if ok_c else '✗'}"
                    f"（要求 ≥ {self.min_quarterly_eps_yoy*100:.0f}%{'，加速 ✓' if accel else ''}）"
                )
            else:
                reasons.append(f"C {c_reason}")
            details.update(c_details)
        letter_scores["C"] = c_score

        # ============ A: 年度 EPS CAGR + ROE + 利润率 ============
        if skip_fund:
            a_score = 0.5
            reasons.append("A 字母（回测）中性 0.5")
        else:
            a_score, a_reason, a_details = self._evaluate_letter_a(ticker, fund, eps_history)
            cagr = a_details.get("annual_eps_3y_cagr")
            ok_a = cagr is not None and cagr >= self.min_annual_eps_cagr
            reasons.append(
                f"A {a_reason} {'✓' if ok_a else '✗'}"
                f"（要求 CAGR ≥ {self.min_annual_eps_cagr*100:.0f}%）"
            )
            details.update(a_details)
        letter_scores["A"] = a_score

        # ============ N: 新催化（距 52 周新高代理）============
        if len(df) >= 252:
            window = df["close"].tail(252)
        else:
            window = df["close"]
        high_52w = window.max()
        last_close = df["close"].iloc[-1]
        dist_from_high = (last_close - high_52w) / high_52w if high_52w > 0 else 0

        recent_5d_high = df["close"].tail(5).max()
        recent_252_high = df["close"].tail(252).max() if len(df) >= 252 else df["close"].max()
        new_high_5d = bool(recent_5d_high >= recent_252_high * 0.999)

        if new_high_5d:
            n_score = 1.0
            reasons.append(f"N 距 52 周高 {dist_from_high*100:.1f}% ✓（5 日内创新高）")
        elif dist_from_high >= self.min_dist_from_high:
            n_score = 0.7
            reasons.append(f"N 距 52 周高 {dist_from_high*100:.1f}% ✓（要求 ≥ {self.min_dist_from_high*100:.0f}%）")
        else:
            n_score = max(0.0, 1.0 + dist_from_high)
            reasons.append(f"N 距 52 周高 {dist_from_high*100:.1f}% ✗")
        letter_scores["N"] = n_score
        details["dist_from_high_pct"] = float(dist_from_high * 100)
        details["new_high_5d"] = new_high_5d

        # ============ S: 流通盘 + 供需 ============
        if skip_fund:
            s_score = 0.5
            shares = None
            reasons.append("S 字母（回测）中性 0.5")
        else:
            shares = fund.get("shares_outstanding") if fund else None
            if shares:
                if shares <= self.max_shares_outstanding:
                    s_score = 1.0 if shares <= 200e6 else 0.7
                else:
                    s_score = 0.3
                reasons.append(
                    f"S 流通股 {shares/1e6:.0f}M {'✓' if shares <= self.max_shares_outstanding else '✗'}"
                    f"（要求 ≤ {self.max_shares_outstanding/1e6:.0f}M）"
                )
            else:
                s_score = 0.0
                reasons.append("S 流通股数据缺失 ✗")
        letter_scores["S"] = s_score
        details["shares_outstanding"] = shares

        # ============ L: 相对强度 ============
        if self._rs_rating is not None:
            l_res = self._rs_rating.evaluate(ticker, df, pit_date=pit_date)
            l_score = l_res.value
            l_details = l_res.details
            rs_val = l_details.get("rs_rating")
            l_reason = f"RS Rating {rs_val}（全市场百分位）" + (f" [{l_res.reasons[0]}]" if l_res.reasons else "")
        else:
            l_score, l_reason, l_details = self._evaluate_relative_strength(df, pit_date=pit_date)
            l_reason = f"（降级 vs SPX）{l_reason}"
        letter_scores["L"] = l_score
        reasons.append(f"L {l_reason}")
        details.update(l_details)

        # ============ I: 机构持仓 + 季度净增持方向（升级）============
        if skip_fund:
            i_score = 0.5
            inst_pct = None
            reasons.append("I 字母（回测）中性 0.5")
        else:
            i_score, i_reason, i_details = self._evaluate_letter_i(ticker, fund)
            inst_pct = i_details.get("institutional_pct")
            details.update(i_details)
            src = i_details.get("i_source", "?")
            ok_i = i_score >= 0.7
            reasons.append(
                f"I {i_reason} {'✓' if ok_i else '✗'}"
                f"（来源: {src}）"
            )
        letter_scores["I"] = i_score
        if inst_pct is not None:
            details["institutional_pct"] = inst_pct

        # ============ M: 大盘方向（调用 MarketDirectionSignal）============
        market_df = self._load_market(pit_date=pit_date)
        m_res = self._market_direction.evaluate(self.market_ticker, df=market_df)
        m_score = m_res.value
        m_state = m_res.details.get("state", "")
        letter_scores["M"] = m_score
        reasons.append(f"M 大盘方向：{m_state}（{m_score:.2f}）")
        details["market_direction"] = m_res.details

        # ============ 综合 ============
        score = sum(letter_scores.values()) / 7.0
        # M 字母 fail = 禁止买入（O'Neil 铁律）
        m_fail_block = m_score < 0.3
        passed = score >= self.threshold and not m_fail_block
        if m_fail_block:
            reasons.append("⚠️ M 字母 fail，强制不买入（O'Neil 铁律）")
        details["letters"] = {k: float(v) for k, v in letter_scores.items()}
        if fund:
            details["sector"] = fund.get("sector")
            details["industry"] = fund.get("industry")
            details["market_cap"] = fund.get("market_cap")

        return SignalResult(
            ticker=ticker, signal_name=self.name, value=float(score), passed=bool(passed),
            details=details, reasons=reasons,
        ).clamp()
