"""ML 模块：López de Prado AfML 流水线下游模型

- SecondaryModel：Meta-Labeling 二级分类器（Ch 4）
- run_meta_labeling_pipeline：端到端 primary → meta → secondary → position
- cross_validate_secondary：Purged K-Fold 评估 secondary model
- compare_ml_models：多 ML 模型对比（Gu-Kelly-Xiu 2020 JFE，任务 #100）
"""
from .secondary_model import (
    SecondaryModel,
    build_features_from_alphas,
    run_meta_labeling_pipeline,
    cross_validate_secondary,
    pipeline_summary,
)
from .deep_factor import (
    compare_ml_models,
    run_model_comparison_pipeline,
    make_models,
    ComparisonResult,
    ModelComparison,
)

__all__ = [
    "SecondaryModel",
    "build_features_from_alphas",
    "run_meta_labeling_pipeline",
    "cross_validate_secondary",
    "pipeline_summary",
    "compare_ml_models",
    "run_model_comparison_pipeline",
    "make_models",
    "ComparisonResult",
    "ModelComparison",
]
