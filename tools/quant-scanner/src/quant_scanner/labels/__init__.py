"""标签生成（Labeling）模块

基于 López de Prado《Advances in Financial Machine Learning》(2018) 的标签生成方法：
- Triple-Barrier：止盈+止损+时间三道屏障
- Meta-Labeling：基于 triple-barrier 输出的二级分类器（任务 #96）
- Fixed-Horizon：传统固定 horizon 标签（基线对比）

为什么需要：
- 传统 fixed-horizon（前瞻 N 日收益正负）标签在波动率变化时不稳定
- Triple-Barrier 自适应波动率（用 ATR 设置屏障宽度），生成更稳定的训练标签
- 是 AfML 全套 ML 流水线（特征→标签→模型→CV）的基础
"""
from .triple_barrier import (
    TripleBarrierLabeler,
    add_atr_barriers,
    triple_barrier_labels,
)

__all__ = [
    "TripleBarrierLabeler",
    "add_atr_barriers",
    "triple_barrier_labels",
]
