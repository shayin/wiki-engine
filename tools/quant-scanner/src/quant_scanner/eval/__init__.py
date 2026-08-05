"""Eval 模块：模型评估工具

- Purged K-Fold CV（López de Prado AfML Ch 7）
- OOS（Out-of-Sample）IC 评估
- OOS Deflated IC（多重检验校正 × purged CV）
"""
from .purged_kfold import (
    PurgedKFold,
    purged_kfold_indices,
    purged_cv_ic,
    purged_cv_deflated_ic,
    oos_summary,
)

__all__ = [
    "PurgedKFold",
    "purged_kfold_indices",
    "purged_cv_ic",
    "purged_cv_deflated_ic",
    "oos_summary",
]
