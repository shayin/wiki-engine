"""Meta-Labeling — López de Prado AfML Ch 4

来源：
- Marcos López de Prado (2018) *Advances in Financial Machine Learning*, Ch 4
- 原始论文：López de Prado (2013) "The Probability of Backtest Overfitting"

**核心思想（二级分类器）**：

传统单一模型既预测方向（买/卖）又预测仓位大小，容易过拟合。
Meta-labeling 把决策拆成两个独立问题：

| 模型 | 问题 | 输出 |
|------|------|------|
| Primary（一级） | **方向**：何时买/卖？ | side ∈ {+1, -1} |
| Secondary（meta） | **信心**：这次下注能赚吗？ | bet ∈ {0, 1} |

最终仓位 = side × bet（方向 × 信心）。

**训练流程**：

1. **Primary 模型**：用 triple-barrier labels 训练，预测 side
   - side ∈ {+1, -1}（做多 / 做空）
   - 可以是任意 ML 模型（随机森林、GBM、逻辑回归）

2. **重新生成 meta labels**：
   - 用 primary 预测的 side 重新跑 triple-barrier
   - 得到新的 label：primary 方向对 → +1，错 → -1
   - 二分类：meta_target = (label == +1).astype(int)

3. **Secondary 模型**：训练在 features → meta_target
   - 输出 P(方向正确) ∈ [0, 1]
   - 高 P → 重仓；低 P → 轻仓/不押

**为什么有效**：
- 拆解"方向"和"信心"两个正交问题，减少过拟合
- 信心低的信号被自动过滤 → 提升 precision
- 不同信心的下注大小不同 → 提升 sharpe

**实践意义**：
- 替代"硬阈值决策"（如 IC > 0.05 买）
- 让 ML 学会"什么时候相信 primary"
- 与 triple-barrier（任务 #91）配合使用

**典型用法**：
```python
from quant_scanner.labels.meta_labeling import MetaLabeler
from quant_scanner.labels.triple_barrier import triple_barrier_labels

# 1. 训练 primary model（已有 alpha 因子 + triple-barrier labels）
primary_side = pd.Series(..., index=df.index)  # +1 / -1 序列

# 2. 准备 meta labels
labeler = MetaLabeler(tp_atr_mult=2.0, sl_atr_mult=2.0, vertical_barrier_bars=10)
meta_target = labeler.prepare_meta_labels(df, primary_side=primary_side)

# 3. 训练 secondary model（用 sklearn 等）
# secondary_model.fit(features, meta_target)
# confidence = secondary_model.predict_proba(features)[:, 1]
# position = primary_side * confidence
```
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .triple_barrier import (
    TripleBarrierLabeler,
    triple_barrier_labels,
)


class MetaLabeler:
    """Meta-Labeling 标签生成器

    用法：
    ```python
    labeler = MetaLabeler(tp_atr_mult=2.0, sl_atr_mult=2.0, vertical_barrier_bars=10)
    meta_target = labeler.prepare_meta_labels(df, primary_side=side_series)
    ```
    """

    def __init__(
        self,
        tp_atr_mult: float = 2.0,
        sl_atr_mult: float = 2.0,
        atr_window: int = 20,
        vertical_barrier_bars: int = 10,
    ) -> None:
        self.tp_atr_mult = tp_atr_mult
        self.sl_atr_mult = sl_atr_mult
        self.atr_window = atr_window
        self.vertical_barrier_bars = vertical_barrier_bars

    def prepare_meta_labels(
        self,
        df: pd.DataFrame,
        primary_side: pd.Series | int = 1,
    ) -> pd.DataFrame:
        """生成 meta-labeling 的二分类目标

        流程：
        1. 用 primary_side 作为 triple-barrier 的 side 入参
        2. 跑 triple-barrier 得到每笔的 label（+1=方向对，-1=方向错，0=平）
        3. 二值化：label==+1 → 1，否则 0

        Args:
            df: OHLCV DataFrame
            primary_side: primary 模型预测的方向序列（与 df 等长）

        Returns:
            DataFrame，含列：
            - t_in, t_out, entry_price, exit_price
            - triple_label: triple-barrier 原始 label (+1/0/-1)
            - meta_target: 二分类目标（1 = primary 方向正确）
            - ret, side
        """
        # 用 primary_side 作为 triple-barrier 的 side
        tb = triple_barrier_labels(
            df,
            tp_atr_mult=self.tp_atr_mult,
            sl_atr_mult=self.sl_atr_mult,
            atr_window=self.atr_window,
            vertical_barrier_bars=self.vertical_barrier_bars,
            side=primary_side,
        )

        # meta target = 1 if triple-barrier label == +1 else 0
        # （label==+1 意味着 tp 先碰 → primary 方向正确）
        tb["triple_label"] = tb["label"]
        tb["meta_target"] = (tb["label"] == 1).astype(int)
        return tb

    def compute_position_size(
        self,
        primary_side: int | pd.Series,
        confidence: float | pd.Series,
        max_position: float = 1.0,
    ) -> float | pd.Series:
        """根据 primary side + secondary confidence 计算仓位大小

        position = side × confidence × max_position

        Args:
            primary_side: +1 / -1
            confidence: P(primary 方向正确) ∈ [0, 1]
            max_position: 最大仓位上限（默认 1.0 = 满仓）

        Returns:
            仓位大小（带符号：+做多 / -做空）
        """
        if isinstance(primary_side, pd.Series) or isinstance(confidence, pd.Series):
            # 对齐索引
            if isinstance(primary_side, pd.Series):
                side_s = primary_side
            else:
                side_s = pd.Series(primary_side, index=confidence.index)
            if isinstance(confidence, pd.Series):
                conf_s = confidence
            else:
                conf_s = pd.Series(confidence, index=side_s.index)
            return side_s * conf_s * max_position
        return primary_side * confidence * max_position


def meta_label_summary(meta_labels: pd.DataFrame) -> dict:
    """Meta-labeling 摘要

    Returns:
        {
            "total": N,
            "primary_correct": primary 方向正确次数,
            "primary_wrong": primary 方向错误次数,
            "precision": primary_correct / total,
            "by_barrier": {tp/sl/vb 各自占比},
        }
    """
    total = len(meta_labels)
    if total == 0:
        return {
            "total": 0,
            "primary_correct": 0,
            "primary_wrong": 0,
            "precision": 0.0,
            "by_barrier": {},
        }
    correct = int(meta_labels["meta_target"].sum())
    wrong = total - correct
    by_barrier = meta_labels["barrier_hit"].value_counts(normalize=True).to_dict()
    return {
        "total": total,
        "primary_correct": correct,
        "primary_wrong": wrong,
        "precision": correct / total,
        "by_barrier": {k: float(v) for k, v in by_barrier.items()},
    }
