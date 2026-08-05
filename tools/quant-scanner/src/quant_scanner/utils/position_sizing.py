"""期望值 + 二换一 + 50/80 法则 + 2% 风控 — Minervini & Murphy 仓位管理数学

来源：
- 《股票魔法师 Ⅱ》第 3、5、8 章 — 风险管理数学（期望值、二换一、50/80）
- 《金融市场技术分析》Ch10 — capital-management-2pct-rule（2% 单笔风险 + 组合级风控）

核心公式：
    E = (P_win × Avg_win) - (P_loss × Avg_loss)

    要求 E > 0 才交易。
    加仓要求：潜在收益 ≥ 2 × 潜在风险（"二换一原则"）。

**2% 风控铁律**（Murphy）：
- 单笔交易最大风险 = 账户权益 × 2%
- 总风险（持仓加总）≤ 账户权益 × 6%
- 总仓位 ≤ 账户 80%（保留 20% 现金缓冲）
- 同行业集中度 ≤ 40%
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# 2% 风控默认参数（Murphy Ch10）
DEFAULT_RISK_PER_TRADE_PCT = 0.02     # 单笔风险 2%
DEFAULT_MAX_TOTAL_RISK_PCT = 0.06     # 总风险 6%
DEFAULT_MAX_POSITION_PCT = 0.80       # 总仓位 ≤ 80%
DEFAULT_MAX_SECTOR_PCT = 0.40         # 单行业 ≤ 40%

# ATR 自适应阈值
ATR_HIGH_PCT = 0.04                   # ATR/price > 4% = 高波动
ATR_LOW_PCT = 0.015                   # ATR/price < 1.5% = 低波动
ATR_HIGH_REDUCE = 0.80                # 高波动减仓 20%（×0.8）
ATR_LOW_BOOST = 1.10                  # 低波动加仓 10%（×1.1）

# VIX 阈值（max_positions_by_volatility）
VIX_HIGH_THRESHOLD = 25               # VIX > 25 = 高波动市场
VIX_NORMAL_RANGE = (15, 25)           # 正常区间
MAX_POSITIONS_HIGH_VIX = 5
MAX_POSITIONS_NORMAL_VIX = 8
MAX_POSITIONS_LOW_VIX = 10

# 因子 IC → 仓位权重映射（任务 #84：因子联动仓位管理）
# 学术标准：|IC| > 0.03 有信号 / > 0.05 有效 / > 0.08 强信号 / > 0.10 警惕过拟合
IC_STRONG = 0.08      # |IC| ≥ 0.08 → 强因子，加仓 50%
IC_EFFECTIVE = 0.05   # |IC| ≥ 0.05 → 有效因子，加仓 20%
IC_WEAK = 0.03        # |IC| ≥ 0.03 → 弱信号，保持基础仓位
# |IC| < 0.03 → 无信号，减仓 30%（避免开仓无统计结构的标的）

FACTOR_WEIGHT_STRONG = 1.5
FACTOR_WEIGHT_EFFECTIVE = 1.2
FACTOR_WEIGHT_BASE = 1.0
FACTOR_WEIGHT_WEAK = 0.7


def ic_to_factor_weight(ic: float) -> tuple[float, str]:
    """把 alpha 因子 IC 值转换为仓位权重调整系数

    学术标准映射：
    - |IC| ≥ 0.08 → 1.5（强因子加仓 50%）
    - |IC| ≥ 0.05 → 1.2（有效因子加仓 20%）
    - |IC| ≥ 0.03 → 1.0（弱信号保持基础仓位）
    - |IC| < 0.03 → 0.7（无信号减仓 30%）

    Args:
        ic: Alpha 因子的 IC 值（Information Coefficient，信息系数）

    Returns:
        (factor_weight, reason)
    """
    abs_ic = abs(ic)
    if abs_ic >= IC_STRONG:
        return FACTOR_WEIGHT_STRONG, f"强因子（|IC|={abs_ic:.3f} ≥ {IC_STRONG}），仓位 ×{FACTOR_WEIGHT_STRONG}"
    if abs_ic >= IC_EFFECTIVE:
        return FACTOR_WEIGHT_EFFECTIVE, f"有效因子（|IC|={abs_ic:.3f} ≥ {IC_EFFECTIVE}），仓位 ×{FACTOR_WEIGHT_EFFECTIVE}"
    if abs_ic >= IC_WEAK:
        return FACTOR_WEIGHT_BASE, f"弱信号（|IC|={abs_ic:.3f} ≥ {IC_WEAK}），仓位保持基础"
    return FACTOR_WEIGHT_WEAK, f"无信号（|IC|={abs_ic:.3f} < {IC_WEAK}），仓位 ×{FACTOR_WEIGHT_WEAK}"


@dataclass
class TradeSetup:
    """单笔交易的输入参数"""
    entry_price: float       # 当前价 / 计划买入价
    stop_loss: float         # 止损价
    target_price: float      # 目标价
    win_rate: float = 0.5    # 胜率估计（0-1）

    @property
    def risk_per_share(self) -> float:
        return self.entry_price - self.stop_loss

    @property
    def reward_per_share(self) -> float:
        return self.target_price - self.entry_price

    @property
    def risk_pct(self) -> float:
        return -self.risk_per_share / self.entry_price

    @property
    def reward_pct(self) -> float:
        return self.reward_per_share / self.entry_price

    @property
    def payoff_ratio(self) -> float:
        if self.risk_per_share <= 0:
            return float("inf") if self.reward_per_share > 0 else 0.0
        return self.reward_per_share / self.risk_per_share

    def expectancy(self) -> float:
        """期望值（百分比形式）

        E = P_win × Avg_win - P_loss × Avg_loss
        """
        p_loss = 1.0 - self.win_rate
        return self.win_rate * self.reward_pct + p_loss * self.risk_pct  # risk_pct 已是负数


def should_trade(setup: TradeSetup) -> tuple[bool, str]:
    """是否应该交易（E > 0）"""
    e = setup.expectancy()
    if e > 0:
        return True, f"E = +{e*100:.2f}%，可交易"
    return False, f"E = {e*100:.2f}%，期望值为负，放弃"


def should_add_position(setup: TradeSetup) -> tuple[bool, str]:
    """是否应该加仓（二换一：收益 ≥ 2 × 风险）"""
    ratio = setup.payoff_ratio
    if ratio >= 2.0:
        return True, f"盈亏比 {ratio:.2f} ≥ 2.0，可加仓（E={setup.expectancy()*100:+.2f}%）"
    return False, f"盈亏比 {ratio:.2f} < 2.0，不满足二换一原则，不加仓"


def atr_adjusted_risk_pct(
    base_risk_pct: float,
    atr: float,
    price: float,
) -> tuple[float, str]:
    """根据 ATR 自适应调整单笔风险百分比

    高波动股（ATR/price > 4%）→ 减仓 20%
    低波动股（ATR/price < 1.5%）→ 加仓 10%

    Args:
        base_risk_pct: 基础风险百分比（通常 0.02）
        atr: 该股 ATR 值
        price: 当前价格

    Returns:
        (adjusted_pct, reason)
    """
    if price <= 0 or atr <= 0:
        return base_risk_pct, "ATR/价格无效，保持基础值"
    atr_pct = atr / price
    if atr_pct > ATR_HIGH_PCT:
        adj = base_risk_pct * ATR_HIGH_REDUCE
        return adj, f"高波动（ATR/price={atr_pct*100:.1f}% > {ATR_HIGH_PCT*100:.0f}%），减仓 {(1-ATR_HIGH_REDUCE)*100:.0f}% → {adj*100:.2f}%"
    if atr_pct < ATR_LOW_PCT:
        adj = base_risk_pct * ATR_LOW_BOOST
        return adj, f"低波动（ATR/price={atr_pct*100:.1f}% < {ATR_LOW_PCT*100:.0f}%），加仓 {(ATR_LOW_BOOST-1)*100:.0f}% → {adj*100:.2f}%"
    return base_risk_pct, f"正常波动（ATR/price={atr_pct*100:.1f}%），保持 {base_risk_pct*100:.2f}%"


def position_size(
    account_equity: float,
    setup_or_risk: "TradeSetup | float",
    maybe_setup: Optional["TradeSetup"] = None,
    risk_budget_pct: Optional[float] = None,
    atr: Optional[float] = None,
    factor_weight: float = 1.0,
) -> float:
    """基于风险的仓位计算

    支持两种调用签名（向后兼容）：
        position_size(account_equity, setup, risk_budget_pct=0.02, atr=None)  # 新（推荐）
        position_size(account_equity, risk_budget_pct, setup)                  # 旧）

    Args:
        account_equity: 账户总权益
        setup_or_risk: 新签名传 TradeSetup；旧签名传 risk_budget_pct (float)
        maybe_setup: 旧签名时为 TradeSetup；新签名时忽略
        risk_budget_pct: 新签名关键字参数；默认 0.02（Murphy 2% 铁律）
        atr: 可选 ATR 值；提供则启用波动率自适应调整
        factor_weight: 因子权重系数（默认 1.0）；通过 `ic_to_factor_weight()` 计算
                      - 1.5 = 强因子加仓 50%（|IC| ≥ 0.08）
                      - 0.7 = 无信号减仓 30%（|IC| < 0.03）
                      ⚠️ 加权后仍受 Murphy 2% 单笔上限封顶（不超 risk_budget_pct × 1.5）

    Returns:
        建议持仓股数（按风险预算法）
    """
    # 兼容旧签名
    if isinstance(setup_or_risk, TradeSetup):
        setup = setup_or_risk
        risk_pct = risk_budget_pct if risk_budget_pct is not None else DEFAULT_RISK_PER_TRADE_PCT
    elif isinstance(maybe_setup, TradeSetup):
        # 旧签名：position_size(account, 0.01, setup)
        setup = maybe_setup
        risk_pct = float(setup_or_risk)
    else:
        raise TypeError("position_size 参数无法识别（需要 TradeSetup）")

    if setup.risk_per_share <= 0:
        return 0.0
    if atr is not None and atr > 0:
        risk_pct, _ = atr_adjusted_risk_pct(risk_pct, atr, setup.entry_price)
    # 因子权重调整（任务 #84）
    # ⚠️ 加权后仍受 Murphy 单笔风险上限封顶：不超 base × FACTOR_WEIGHT_STRONG
    weighted_risk_pct = risk_pct * max(0.5, min(factor_weight, FACTOR_WEIGHT_STRONG))
    risk_amount = account_equity * weighted_risk_pct
    shares = risk_amount / setup.risk_per_share
    return shares


# ============ 组合级风控（Murphy Ch10）============

@dataclass
class Position:
    """持仓项"""
    ticker: str
    entry_price: float
    stop_loss: float
    shares: int
    sector: str = "unknown"

    @property
    def market_value(self) -> float:
        return self.entry_price * self.shares

    @property
    def risk_per_share(self) -> float:
        return self.entry_price - self.stop_loss

    @property
    def position_risk_amount(self) -> float:
        """该仓位若止损的损失金额"""
        return abs(self.risk_per_share) * self.shares


@dataclass
class PortfolioCheckResult:
    """组合级风控检查结果"""
    passed: bool
    violations: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    total_exposure_pct: float = 0.0
    total_risk_pct: float = 0.0
    sector_exposure: dict[str, float] = field(default_factory=dict)


def portfolio_risk_check(
    positions: list[Position],
    account_equity: float,
    max_total_exposure_pct: float = DEFAULT_MAX_POSITION_PCT,
    max_total_risk_pct: float = DEFAULT_MAX_TOTAL_RISK_PCT,
    max_sector_pct: float = DEFAULT_MAX_SECTOR_PCT,
) -> PortfolioCheckResult:
    """检查组合级风控约束

    Args:
        positions: 当前持仓列表
        account_equity: 账户总权益
        max_total_exposure_pct: 总仓位上限（默认 80%）
        max_total_risk_pct: 总风险上限（默认 6%）
        max_sector_pct: 单行业上限（默认 40%）

    Returns:
        PortfolioCheckResult，含 violations（违反）和 warnings（警告）
    """
    if account_equity <= 0:
        return PortfolioCheckResult(passed=False, violations=["账户权益必须 > 0"])

    violations: list[str] = []
    warnings: list[str] = []

    total_mv = sum(p.market_value for p in positions)
    total_risk = sum(p.position_risk_amount for p in positions)
    total_exposure_pct = total_mv / account_equity
    total_risk_pct = total_risk / account_equity

    # 行业集中度
    sector_mv: dict[str, float] = {}
    for p in positions:
        sector_mv[p.sector] = sector_mv.get(p.sector, 0) + p.market_value
    sector_exposure = {s: mv / account_equity for s, mv in sector_mv.items()}

    # 约束检查
    if total_exposure_pct > max_total_exposure_pct:
        violations.append(
            f"总仓位 {total_exposure_pct*100:.1f}% > 上限 {max_total_exposure_pct*100:.0f}%（Murphy Ch10）"
        )
    if total_risk_pct > max_total_risk_pct:
        violations.append(
            f"总风险 {total_risk_pct*100:.1f}% > 上限 {max_total_risk_pct*100:.0f}%"
        )
    for s, pct in sector_exposure.items():
        if pct > max_sector_pct:
            violations.append(
                f"行业 {s} 集中度 {pct*100:.1f}% > 上限 {max_sector_pct*100:.0f}%（同行业分散不够）"
            )

    # 警告（未违反但接近）
    if total_exposure_pct > max_total_exposure_pct * 0.85:
        warnings.append(f"总仓位接近上限（{total_exposure_pct*100:.1f}%）")
    if total_risk_pct > max_total_risk_pct * 0.7:
        warnings.append(f"总风险接近上限（{total_risk_pct*100:.1f}%）")

    return PortfolioCheckResult(
        passed=len(violations) == 0,
        violations=violations,
        warnings=warnings,
        total_exposure_pct=total_exposure_pct,
        total_risk_pct=total_risk_pct,
        sector_exposure=sector_exposure,
    )


def max_positions_by_volatility(vix: float) -> tuple[int, str]:
    """根据 VIX 决定最大持仓数（市场波动率分档）

    Args:
        vix: VIX 指数（恐慌指数）

    Returns:
        (max_positions, regime_label)
    """
    if vix > VIX_HIGH_THRESHOLD:
        return MAX_POSITIONS_HIGH_VIX, f"高波动市场（VIX {vix:.0f} > {VIX_HIGH_THRESHOLD}）"
    if vix < VIX_NORMAL_RANGE[0]:
        return MAX_POSITIONS_LOW_VIX, f"低波动市场（VIX {vix:.0f} < {VIX_NORMAL_RANGE[0]}）"
    return MAX_POSITIONS_NORMAL_VIX, f"正常波动（VIX {vix:.0f}）"


def trailing_stop_updater(current_price: float, entry_price: float, current_stop: float) -> tuple[float, str]:
    """Minervini 交错止损（持仓后逐步抬高）

    - 涨 5%：移到盈亏平衡
    - 涨 10%：移到 +5%
    - 涨 20%：移到 +10%
    """
    gain_pct = (current_price - entry_price) / entry_price

    if gain_pct >= 0.20:
        new_stop = entry_price * 1.10
        if new_stop > current_stop:
            return new_stop, f"涨 {gain_pct*100:.1f}%，止损上调至 +10%（{new_stop:.2f}）"
    if gain_pct >= 0.10:
        new_stop = entry_price * 1.05
        if new_stop > current_stop:
            return new_stop, f"涨 {gain_pct*100:.1f}%，止损上调至 +5%（{new_stop:.2f}）"
    if gain_pct >= 0.05:
        new_stop = entry_price  # 盈亏平衡
        if new_stop > current_stop:
            return new_stop, f"涨 {gain_pct*100:.1f}%，止损上调至盈亏平衡（{new_stop:.2f}）"
    return current_stop, "无需调整"


# ============ 50/80 法则参考 ============

RULE_50_80 = {
    "pullback_prob": 0.50,    # 50% 突破后会回踩突破点
    "pullback_5d_prob": 0.80,  # 80% 突破后 5 天内会回调
    "alarm_threshold_pct": -0.05,  # 回踩 > -5% + 放量 = 警报
    "interpretation": (
        "突破后回踩是常态（50% 概率），不必恐慌。"
        "80% 的突破在 5 天内会有所回调，不要立即卖。"
        "但回踩超过 -5% + 放量 = 不是正常回踩，要警惕。"
    ),
}


def rule_50_80_advice(days_since_breakout: int, drawdown_pct: float, volume_ratio: float) -> str:
    """根据 50/80 法则给出持有/卖出建议"""
    if drawdown_pct <= -0.05 and volume_ratio > 1.5:
        return "⚠️ 回踩超过 -5% + 放量，不是正常回踩，考虑弱势卖出"
    if days_since_breakout <= 5 and -0.05 < drawdown_pct < 0:
        return "📈 80% 法则：5 天内回调是常态，不必恐慌"
    if 0 < drawdown_pct < 0.03:
        return "📊 50% 法则：可能回踩突破点，观察量能"
    if drawdown_pct >= 0:
        return "✅ 突破后走强，正常持有"
    return "⚠️ 回踩接近警戒线，密切关注"
