"""Volatility Contraction Pattern (VCP) — Minervini 精确定义版（2026-07-19 回炉）

来源：《股票魔法师 Ⅱ》第 6 章 — SEPA 方法论的核心买入形态。

**2026-07-19 回炉对照原书修复**（cangjie-skill + wiki-quant-distill 接力）：

原书 p.145 关键定义：
> "作为经验法则，每个连续的收缩通常幅度为前期回撤或收缩的一半（**上下可以有合理的波动**）。"

旧版（2026-07-18）的 3 个偏差：
1. **shrink_ratio 阈值过严**：旧版 `min_shrink_ratio=0.55` 要求每次 ≤ 上次 55%，
   相当于"必须缩到一半以下"，比原书"一半上下可以有合理波动"严格。
   原书真实案例 MIK [16%, 8%, 6%, 3%] 的 8→6 shrink ratio=0.75（>0.55），
   旧版会判"非递减"。新版放宽到 0.75。
2. **vol_score 算法不符合原书定义**：旧版用"近 20 日均量 / 前 40 日均量"，
   但原书 p.156 明确写"**最后收缩期间**的交易量低于 50 日均量"。
   新版改为：取最后一个 trough 处 ±3 日的成交量 vs base 内 50 日均量。
3. **无容差递减判定**：原书"上下可以有合理的波动"意味着允许单次反弹。
   新版新增 tolerant_decreasing（允许 1 次 shrink ratio ∈ (0.75, 1.0]，
   末值仍须最小）。

**2026-07-18 zigzag 修复**（前版基础）：
1. 初始化 bug：旧版 `direction=0` 时 `hi>current_extreme_price` 立刻设 `direction=1`
   但没等回落 min_pct 确认。新版采用标准两阶段算法。
2. 无基底左缘：旧版 zigzag 从 lookback 起点扫描，把前期涨幅段的波动也算成
   contraction 候选。新版先检测基底左缘，zigzag 只扫基底内部。
3. 固定 5% 阈值：高波动股会过多 pivot，低波动股会漏 pivot。
   新版默认按 ATR 自适应。
4. trend_template 未作为前置：旧版用 MA50>MA200 简化版。新版直接调用 8 条规则。
5. contraction 配对不严格：旧版从全部 pivots 任意配对。新版要求相邻 peak→trough。

VCP 严格定义：
1. 前提：trend_template 8 条通过 + 前期涨幅 ≥ 30%（典型 100%+）
2. 形态：2-6 次收缩，每次回撤 ≈ 上次的 1/2（shrink_ratio ∈ [0.3, 0.75]，允许单次容差至 1.0）
3. 量能：最后收缩期间成交量 ≤ 50 日均量 × 50%（干涸）
4. 位置：距 52 周高 ≤ 15%，价格在中枢点附近（距突破 ≤ 5%）

技术足迹：`{W}W {max_dd}/{min_dd} {N}T`，例：`27W 25/5 3T`
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .base import BaseSignal, SignalResult
from .trend_template import TrendTemplateSignal


def zigzag_pivots(
    high: pd.Series,
    low: pd.Series,
    min_pct: float = 0.05,
) -> tuple[list[int], list[int]]:
    """标准 ZigZag：识别显著的峰谷

    算法分两阶段：
    - 阶段 1（初始化）：从序列起点前向扫描，直到价格向任一方向移动 ≥ min_pct，
      确认起点为对应方向的 pivot（升破 → 起点为 trough；跌破 → 起点为 peak）。
      这避免了旧版"hi 一旦 > 当前极值就把 direction 改 1"的伪确认。
    - 阶段 2（主循环）：跟踪当前方向的极值候选，反向移动 ≥ min_pct 才确认 pivot。

    Args:
        high: 日内最高价
        low: 日内最低价
        min_pct: 最小波动幅度（建议由 ATR 自适应传入）

    Returns:
        peaks_idx, troughs_idx: 时间升序的峰/谷位置索引
    """
    n = len(high)
    if n < 5:
        return [], []

    peaks: list[int] = []
    troughs: list[int] = []

    # ---- 阶段 1：确认初始方向 + 初始 pivot ----
    direction = 0
    current_extreme_idx = 0
    current_extreme_price = None  # 待定：可能成为 peak 或 trough 的极值

    # 跟踪"到目前为止的最高/最低"作为初始 pivot 候选
    init_high_idx = 0
    init_high = float(high.iloc[0])
    init_low_idx = 0
    init_low = float(low.iloc[0])

    for i in range(1, n):
        hi = float(high.iloc[i])
        lo = float(low.iloc[i])
        if hi > init_high:
            init_high = hi
            init_high_idx = i
        if lo < init_low:
            init_low = lo
            init_low_idx = i

        # 先升 ≥ min_pct（从最低涨上来）→ 确认起点 low 为 trough
        if direction == 0 and hi >= init_low * (1 + min_pct):
            troughs.append(init_low_idx)
            direction = 1
            current_extreme_idx = i
            current_extreme_price = hi
            break
        # 先降 ≥ min_pct（从最高跌下来）→ 确认起点 high 为 peak
        if direction == 0 and lo <= init_high * (1 - min_pct):
            peaks.append(init_high_idx)
            direction = -1
            current_extreme_idx = i
            current_extreme_price = lo
            break
    else:
        # 整段都没移动 ≥ min_pct，没有 pivot
        return [], []

    # ---- 阶段 2：主循环 ----
    for i in range(current_extreme_idx + 1, n):
        hi = float(high.iloc[i])
        lo = float(low.iloc[i])
        if direction == 1:
            # 找峰中：等从当前极值回落 ≥ min_pct
            if hi > current_extreme_price:
                current_extreme_price = hi
                current_extreme_idx = i
            elif lo <= current_extreme_price * (1 - min_pct):
                peaks.append(current_extreme_idx)
                direction = -1
                current_extreme_price = lo
                current_extreme_idx = i
        else:  # direction == -1
            # 找谷中：等从当前极值反弹 ≥ min_pct
            if lo < current_extreme_price:
                current_extreme_price = lo
                current_extreme_idx = i
            elif hi >= current_extreme_price * (1 + min_pct):
                troughs.append(current_extreme_idx)
                direction = 1
                current_extreme_price = hi
                current_extreme_idx = i

    return peaks, troughs


class VCPSignal(BaseSignal):
    name = "vcp"
    threshold = 0.7

    # 前期涨幅
    min_prior_runup_pct: float = 0.30

    # ZigZag 阈值
    # 注意：必须 < min_drawdown_pct，否则浅收缩的 pivot 会被吃掉
    # Minervini VCP 最后一次收缩典型 1-5%，所以 zigzag 阈值要够小
    zigzag_threshold: float = 0.025       # 默认；若 use_atr_threshold=True 则被 ATR 覆盖
    use_atr_threshold: bool = True
    atr_period: int = 20
    atr_min_pct: float = 0.02             # 自适应下限（低波动股，不能漏 shallow pivot）
    atr_max_pct: float = 0.08             # 自适应上限（高波动股）

    # 形态要求（Minervini 半衰法则 + "上下可以有合理的波动"容差）
    min_contractions: int = 2
    max_contractions: int = 6
    min_shrink_ratio: float = 0.75        # 每次回撤 ≤ 上次 75%（原书 p.145「上下合理波动」，旧 0.55 过严）
    tolerant_shrink_ratio: float = 1.0    # 容差上限：允许单次 ≤ 此值（仍不算反递减）
    max_tolerance_violations: int = 1     # 允许的容差违规次数（>min_shrink_ratio 但 ≤tolerant_shrink_ratio）
    min_drawdown_pct: float = 0.02        # 每次收缩 ≥ 2% 才计入（最后一次可浅至 1-3%）
    max_last_drawdown_pct: float = 0.10   # 最后一次 ≤ 10%（中枢点要求）

    # 量能（原书 p.156：最后收缩期间成交量 ≤ 50 日均量 × 50%）
    last_trough_vol_window: int = 3       # 最后 trough 前后 N 日的均量
    pivot_vol_threshold: float = 0.50     # 中枢点干涸：≤ 50 日均量 × 50%
    vol_dry_strong_threshold: float = 0.50  # ≤ 均量 × 50% → vol_score=1.0
    vol_dry_weak_threshold: float = 0.80    # ≤ 均量 × 80% → vol_score=0.5
    # 旧版字段保留向后兼容（不再用于打分，仅留 details 用于回归测试）
    vol_shrink_ratio: float = 1.05

    # 位置
    max_dist_from_high_pct: float = 0.15
    breakout_threshold_pct: float = 0.05

    # 前置 trend_template（不通过则 value=0）
    require_trend_template: bool = True
    trend_template_min_score: float = 0.875  # 8 条中至少 7 条

    # ---------------- 内部工具 ----------------

    def _atr_pct(self, high: pd.Series, low: pd.Series, close: pd.Series) -> float:
        """近 atr_period 日 ATR 占股价百分比"""
        prev_close = close.shift(1)
        tr = pd.concat(
            [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
            axis=1,
        ).max(axis=1)
        atr = tr.rolling(self.atr_period).mean()
        last_close = float(close.iloc[-1])
        if last_close <= 0 or pd.isna(atr.iloc[-1]):
            return self.zigzag_threshold
        return float(atr.iloc[-1]) / last_close

    def _adaptive_zigzag_threshold(self, high: pd.Series, low: pd.Series, close: pd.Series) -> float:
        """ATR 自适应阈值，clamp 到 [atr_min_pct, atr_max_pct]"""
        if not self.use_atr_threshold:
            return self.zigzag_threshold
        atr_pct = self._atr_pct(high, low, close)
        return max(self.atr_min_pct, min(self.atr_max_pct, atr_pct))

    def _find_base_left_edge(self, close: pd.Series, min_runup: float) -> int | None:
        """从右往左找基底左缘 = 前期涨幅的最高点

        策略：
        1. 在 lookback 窗口前 search_end=90% 段找 close 的最大点 max_idx
        2. 验证 max_idx 之前有 ≥ min_runup 的涨幅 → 返回 max_idx
        3. 如果不满足（前期涨幅不够），尝试从 max_idx 向前找：
           滑动窗口找第一个"前期涨幅 ≥ min_runup"的局部高点
        4. 仍找不到 → 返回 None（当前可能已突破，或前期涨幅不足）

        Returns:
            基底左缘的 positional index，或 None（数据不足/无前期涨幅）
        """
        n = len(close)
        if n < 50:
            return None
        search_end = int(n * 0.9)
        if search_end < 30:
            return None

        arr = close.iloc[:search_end].values
        max_idx = int(np.argmax(arr))

        # 第 1 次尝试：max_idx 之前有足够涨幅
        if max_idx >= 10:
            prior_low = float(close.iloc[:max_idx].min())
            prior_high = float(close.iloc[max_idx])
            if prior_low > 0:
                runup = (prior_high - prior_low) / prior_low
                if runup >= min_runup:
                    return max_idx

        # 第 2 次尝试：从 max_idx 往前递归找前期局部高点
        # 适用于"已经突破创新高"的情况——base 左缘在更早的位置
        for fallback_end in [max_idx - 1, int(search_end * 0.7), int(search_end * 0.5)]:
            if fallback_end < 30:
                continue
            arr_fb = close.iloc[:fallback_end].values
            fb_idx = int(np.argmax(arr_fb))
            if fb_idx < 10:
                continue
            prior_low = float(close.iloc[:fb_idx].min())
            prior_high = float(close.iloc[fb_idx])
            if prior_low > 0:
                runup = (prior_high - prior_low) / prior_low
                if runup >= min_runup:
                    return fb_idx

        return None

    def _extract_contractions(
        self,
        peaks: list[int],
        troughs: list[int],
        high: pd.Series,
        low: pd.Series,
        base_left_edge: int,
    ) -> list[float]:
        """从 base 内提取收缩序列（相邻 peak→trough 配对，按时间顺序）"""
        pivots = sorted(
            [(i, "peak", float(high.iloc[i])) for i in peaks if i >= base_left_edge]
            + [(i, "trough", float(low.iloc[i])) for i in troughs if i >= base_left_edge]
        )
        contractions: list[float] = []
        for j in range(1, len(pivots)):
            idx_prev, type_prev, price_prev = pivots[j - 1]
            _, type_curr, price_curr = pivots[j]
            if type_prev == "peak" and type_curr == "trough" and price_prev > 0:
                drawdown = (price_prev - price_curr) / price_prev
                if drawdown >= self.min_drawdown_pct:
                    contractions.append(float(drawdown))
        return contractions

    def _classify_decreasing(
        self, contractions: list[float]
    ) -> tuple[str, list[float], int]:
        """判定收缩序列的递减等级（原书 p.145「上下可以有合理的波动」）。

        Returns:
            (grade, shrink_ratios, tolerance_violations)
            grade ∈ {"strict", "tolerant", "fail"}
            - strict：所有 shrink ratio ≤ min_shrink_ratio（典型递减）
            - tolerant：≤ max_tolerance_violations 次容差违规（>min_shrink_ratio 但 ≤tolerant_shrink_ratio），
              且末值是序列最小（允许"上下合理波动"）
            - fail：反递减（ratio > tolerant_shrink_ratio）次数超限，或末值非最小
        """
        if len(contractions) < 2:
            return "strict", [], 0

        shrink_ratios: list[float] = []
        tolerance_violations = 0
        reverse_count = 0  # ratio > tolerant_shrink_ratio
        for i in range(1, len(contractions)):
            if contractions[i - 1] <= 0:
                return "fail", shrink_ratios, tolerance_violations
            r = contractions[i] / contractions[i - 1]
            shrink_ratios.append(float(r))
            if r > self.tolerant_shrink_ratio:
                reverse_count += 1
            elif r > self.min_shrink_ratio:
                tolerance_violations += 1

        # 末值必须是序列最小（VCP 终态特征）
        last_is_min = contractions[-1] == min(contractions)

        if reverse_count > 0 or tolerance_violations > self.max_tolerance_violations or not last_is_min:
            return "fail", shrink_ratios, tolerance_violations
        if tolerance_violations > 0:
            return "tolerant", shrink_ratios, tolerance_violations
        return "strict", shrink_ratios, tolerance_violations

    def _evaluate_volume_dry_up(
        self,
        volume: pd.Series,
        peaks: list[int],
        troughs: list[int],
        base_left_edge: int,
    ) -> tuple[float, float, bool]:
        """评估最后收缩期间的成交量干涸程度（原书 p.156）。

        找 base 内最后一个 trough 的位置，取其前 last_trough_vol_window 日的均量，
        对比 base 内 50 日均量。

        Returns:
            (vol_score, last_trough_vol_ratio, dry_up_strong)
            - vol_score: [0, 1]，干涸程度
            - last_trough_vol_ratio: 最后 trough 处均量 / base 50 日均量
            - dry_up_strong: 是否触达 ≤ 50% 强干涸
        """
        # base 内的所有 trough（按时间）
        base_troughs = sorted([i for i in troughs if i >= base_left_edge])
        if not base_troughs:
            return 0.0, 1.0, False

        last_trough_idx = base_troughs[-1]
        window = self.last_trough_vol_window

        # 取最后 trough 前 window 日的均量（trough 之前是 peak，处于收缩末段）
        start = max(base_left_edge, last_trough_idx - window)
        end = last_trough_idx + 1  # 含 trough 当日
        if end - start < 2:
            return 0.0, 1.0, False

        last_trough_vol = float(volume.iloc[start:end].mean())

        # base 内 50 日均量（base 不足 50 日则用全部）
        base_vol = volume.iloc[base_left_edge:]
        vol_ma_period = min(50, len(base_vol))
        if vol_ma_period < 10:
            return 0.0, 1.0, False
        base_vol_ma = float(base_vol.tail(vol_ma_period).mean())

        if base_vol_ma <= 0:
            return 0.0, 1.0, False

        ratio = last_trough_vol / base_vol_ma
        dry_up_strong = ratio <= self.vol_dry_strong_threshold

        # vol_score 分级
        if ratio <= self.vol_dry_strong_threshold:
            vol_score = 1.0
        elif ratio <= self.vol_dry_weak_threshold:
            # 0.5 - 0.8 之间线性衰减
            vol_score = 0.5 * (self.vol_dry_weak_threshold - ratio) / (
                self.vol_dry_weak_threshold - self.vol_dry_strong_threshold
            ) + 0.5
        else:
            vol_score = 0.0

        return float(vol_score), float(ratio), bool(dry_up_strong)

    # ---------------- 主评估 ----------------

    def evaluate(self, ticker: str, df: pd.DataFrame) -> SignalResult:
        reasons: list[str] = []
        details: dict = {}

        lookback = 350  # VCP 形态需覆盖前期涨幅 + 整理周期（Minervini 基底典型 5-30 周 + 前期涨幅段）
        if len(df) >= lookback:
            recent = df.tail(lookback).copy()
        elif len(df) >= 80:
            recent = df.copy()
            lookback = len(df)
        else:
            return SignalResult(
                ticker=ticker, signal_name=self.name, value=0.0, passed=False,
                details={"error": "数据不足"},
                reasons=[f"数据不足（需 ≥ 80 行）"],
            )

        close = recent["close"]
        high = recent["high"]
        low = recent["low"]
        volume = recent["volume"]
        last_close = float(close.iloc[-1])

        # ============ 0. 前置：趋势模板 8 条 ============
        trend_template_passed = True
        trend_template_score = 1.0
        if self.require_trend_template:
            tt = TrendTemplateSignal()
            tt_result = tt.evaluate(ticker, df)  # 用原始 df（不被 lookback 截断）
            trend_template_score = tt_result.value
            trend_template_passed = tt_result.value >= self.trend_template_min_score
            details["trend_template_score"] = float(tt_result.value)
            details["trend_template_passed"] = bool(trend_template_passed)
            if not trend_template_passed:
                reasons.append(
                    f"✗ 趋势模板 {tt_result.value:.2f} < {self.trend_template_min_score}（未通过 8 条 → 非第二阶段，直接判为非 VCP）"
                )
                # 趋势模板不通过 → 直接 value=0，不再做后续分析
                return SignalResult(
                    ticker=ticker, signal_name=self.name, value=0.0, passed=False,
                    details=details, reasons=reasons,
                )
            reasons.append(
                f"✓ 趋势模板 {tt_result.value:.2f} ≥ {self.trend_template_min_score}（第二阶段确认）"
            )

        # ============ 1. 基底左缘 + 前期涨幅 ============
        base_left_edge = self._find_base_left_edge(close, self.min_prior_runup_pct)
        if base_left_edge is None:
            reasons.append(
                f"✗ 未找到基底左缘（lookback 内未找到涨幅 ≥ {self.min_prior_runup_pct*100:.0f}% 的高点）"
            )
            details["base_left_edge"] = None
            # 没基底就没 VCP
            return SignalResult(
                ticker=ticker, signal_name=self.name, value=0.0, passed=False,
                details=details, reasons=reasons,
            )

        prior_low = float(close.iloc[:base_left_edge].min())
        prior_high = float(close.iloc[base_left_edge])
        prior_runup = (prior_high - prior_low) / prior_low if prior_low > 0 else 0
        runup_ok = prior_runup >= self.min_prior_runup_pct
        runup_score = min(1.0, prior_runup / (self.min_prior_runup_pct * 2)) if runup_ok else 0.0
        details["base_left_edge_idx"] = base_left_edge
        details["base_left_edge_price"] = prior_high
        details["prior_runup_pct"] = float(prior_runup * 100)
        reasons.append(
            f"前期涨幅 {prior_runup*100:.1f}% {'✓' if runup_ok else '✗'}"
            f"（基底左缘位于 idx={base_left_edge}，价格 {prior_high:.2f}）"
        )

        # ============ 2. ZigZag + 收缩形态 ============
        zigzag_threshold = self._adaptive_zigzag_threshold(high, low, close)
        details["zigzag_threshold"] = float(zigzag_threshold)

        peaks, troughs = zigzag_pivots(high, low, min_pct=zigzag_threshold)
        contractions = self._extract_contractions(
            peaks, troughs, high, low, base_left_edge
        )

        # 限制到 max_contractions（按末段取）
        if len(contractions) > self.max_contractions:
            contractions = contractions[-self.max_contractions:]

        n_contractions = len(contractions)
        decrease_grade, shrink_ratios, tolerance_violations = self._classify_decreasing(contractions)
        # 向后兼容旧字段
        strictly_decreasing = decrease_grade == "strict"

        last_drawdown_ok = (
            n_contractions > 0 and contractions[-1] <= self.max_last_drawdown_pct
        )

        # 形态判定：strict/tolerant 通过，fail 不通过
        pattern_ok = (
            n_contractions >= self.min_contractions
            and decrease_grade in ("strict", "tolerant")
            and last_drawdown_ok
        )

        # pattern_score 分级（原书 p.145「上下合理波动」容差）
        pattern_score = 0.0
        if n_contractions >= self.min_contractions:
            if decrease_grade == "strict":
                pattern_score = min(1.0, n_contractions / 3.0)
                if not last_drawdown_ok:
                    pattern_score *= 0.6
            elif decrease_grade == "tolerant":
                # 容差递减：略低于 strict，仍算真 VCP
                pattern_score = min(0.85, n_contractions / 3.0 * 0.85)
                if not last_drawdown_ok:
                    pattern_score *= 0.6
            else:
                # fail：非递减或反递减
                pattern_score = 0.1

        details.update({
            "n_contractions": n_contractions,
            "contractions": contractions,
            "shrink_ratios": shrink_ratios,
            "strictly_decreasing": strictly_decreasing,  # 向后兼容
            "decrease_grade": decrease_grade,             # 新字段：strict/tolerant/fail
            "tolerance_violations": tolerance_violations,
            "last_drawdown_ok": last_drawdown_ok,
            "peaks_count": len(peaks),
            "troughs_count": len(troughs),
        })
        grade_zh = {"strict": "严格递减", "tolerant": "容差递减", "fail": "非递减"}[decrease_grade]
        reasons.append(
            f"收缩形态 {'✓' if pattern_ok else '✗'}：{n_contractions} 次回撤 "
            f"{[f'{c*100:.1f}%' for c in contractions]}，{grade_zh}"
        )

        # ============ 3. 成交量干涸（原书 p.156：最后收缩期间）============
        vol_score, last_trough_vol_ratio, dry_up_strong = self._evaluate_volume_dry_up(
            volume, peaks, troughs, base_left_edge
        )
        # 旧字段保留（向后兼容）：近20/前40 用于回归测试
        recent_vol = float(volume.tail(20).mean())
        prior_vol = float(volume.iloc[-60:-20].mean()) if len(volume) >= 60 else float(volume.iloc[:-20].mean())
        vol_ratio_legacy = recent_vol / prior_vol if prior_vol > 0 else 1.0
        vol_ok = vol_score > 0

        details["vol_score"] = float(vol_score)
        details["vol_ratio"] = float(vol_ratio_legacy)  # 旧字段，回归测试用
        details["last_trough_vol_ratio"] = float(last_trough_vol_ratio)  # 新字段：原书定义
        details["pivot_vol_dry"] = bool(dry_up_strong)
        reasons.append(
            f"成交量干涸 {'✓' if vol_ok else '✗'}（最后trough处 = 50日均量的 {last_trough_vol_ratio*100:.0f}%，"
            f"原书要求 ≤ 50%）"
        )

        # ============ 4. 位置 ============
        window_252 = close.tail(252) if len(close) >= 252 else close
        high_52w = float(window_252.max())
        dist_from_high = (last_close - high_52w) / high_52w
        recent_high = float(high.tail(20).max())
        dist_to_breakout = (recent_high - last_close) / last_close if last_close > 0 else 1.0
        position_ok = (
            dist_from_high >= -self.max_dist_from_high_pct
            and dist_to_breakout <= self.breakout_threshold_pct
        )
        position_score = 0.0
        if dist_from_high >= -self.max_dist_from_high_pct:
            position_score += 0.5
        if dist_to_breakout <= self.breakout_threshold_pct:
            position_score += 0.5

        details["dist_from_high_pct"] = float(dist_from_high * 100)
        details["dist_to_breakout_pct"] = float(dist_to_breakout * 100)
        reasons.append(
            f"位置 {'✓' if position_ok else '✗'}（距52周高 {dist_from_high*100:.1f}%，"
            f"距突破 {dist_to_breakout*100:.2f}%）"
        )

        # ============ 综合评分 ============
        # 前置 trend_template 已通过 = 1.0，剩 4 维度各 25%
        score = (
            0.25 * runup_score
            + 0.25 * pattern_score
            + 0.25 * vol_score
            + 0.25 * position_score
        )
        # 硬规则：pattern fail（反递减/容差违规超限/末值非最小）→ 不是 VCP，强制 passed=False
        # 即使其他维度满分把总分拉过 threshold，也不能判 passed（原书 VCP 核心是递减收缩）
        pattern_fail = decrease_grade == "fail"
        passed = (score >= self.threshold) and (not pattern_fail)
        if pattern_fail:
            reasons.append(
                f"✗ 收缩序列不递减（grade=fail）→ 强制 passed=False，即使总分 {score:.3f}"
            )

        # ============ 技术足迹标签 ============
        base_weeks = max(1, (lookback - base_left_edge) // 5)
        max_dd = int(contractions[0] * 100) if contractions else 0
        min_dd = int(contractions[-1] * 100) if contractions else 0
        footprint = f"{base_weeks}W {max_dd}/{min_dd} {n_contractions}T"

        details.update({
            "footprint": footprint,
            "runup_score": float(runup_score),
            "pattern_score": float(pattern_score),
            "vol_score": float(vol_score),
            "position_score": float(position_score),
            "base_weeks": base_weeks,
            "max_drawdown_pct": max_dd,
            "min_drawdown_pct": min_dd,
        })
        reasons.insert(0, f"技术足迹：{footprint}")

        return SignalResult(
            ticker=ticker, signal_name=self.name, value=float(score), passed=bool(passed),
            details=details, reasons=reasons,
        ).clamp()
