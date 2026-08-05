"""Secondary Model — Meta-Labeling 二级分类器（AfML Ch 4 完整闭环）

来源：
- Marcos López de Prado (2018) *Advances in Financial Machine Learning*, Ch 4

**这是 AfML 流水线的最后一块**：
- 任务 #91 Triple-Barrier：产出 label
- 任务 #96 Meta-Labeler：产出 meta_target（1=primary 方向对，0=错）
- 任务 #97（本模块）：训练 sklearn 二级分类器 → 输出 P(primary 对)
- 最终仓位 = side × confidence × max_position

**为什么闭环**：
传统单一模型既预测方向又预测仓位，容易过拟合。Meta-Labeling 拆开：
- Primary（一级，已有 alpha）：方向 → side ∈ {+1, -1}
- Secondary（本模块，sklearn）：信心 → P(primary 对) ∈ [0, 1]

最终决策从二值（买/不买）变成连续（买多少），让 ML 学会"什么时候相信 primary"。

**典型用法**：
```python
from quant_scanner.ml import run_meta_labeling_pipeline
from quant_scanner.factors.alpha101 import Alpha101
from quant_scanner.data.loader import DataLoader

df = DataLoader().load("NVDA", period="2y")
alpha = Alpha101()
primary_alpha = alpha.compute(df, "alpha_42")  # 已知最好的方向预测
features = alpha.compute_all(df)               # 所有 alpha 作 feature

result = run_meta_labeling_pipeline(
    df, primary_alpha=primary_alpha, feature_alphas=features,
    tp_atr_mult=2.0, sl_atr_mult=2.0, vertical_barrier_bars=10,
)

print(f"Primary precision: {result['primary_precision']:.1%}")
print(f"Secondary OOS precision: {result['oos_precision']:.1%}")
print(f"Position (OOS): {result['position_oos'].tail()}")
```
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import numpy as np
import pandas as pd


# ============================================================
# SecondaryModel：二分类器包装
# ============================================================

def _clone_clf(clf: object | None) -> object | None:
    """sklearn 标准 clone（每折重建，避免状态污染）"""
    if clf is None:
        return None
    try:
        from sklearn.base import clone
        return clone(clf)
    except Exception:
        return clf  # 非 sklearn 对象，直接返回

@dataclass
class SecondaryModel:
    """Meta-Labeling 二级分类器

    Args:
        clf: sklearn 二分类器（默认 LogisticRegression）
        threshold: confidence ≥ threshold 视为"信"primary（默认 0.5）
        feature_names: 训练时的特征列顺序（predict 时对齐）
    """
    clf: object | None = None
    threshold: float = 0.5
    feature_names: list[str] = field(default_factory=list)
    is_fitted: bool = False

    def __post_init__(self) -> None:
        if self.clf is None:
            from sklearn.linear_model import LogisticRegression
            self.clf = LogisticRegression(
                max_iter=1000,
                solver="liblinear",  # 小样本 + L2 正则，liblinear 比 lbfgs 稳
                random_state=42,
            )

    def fit(self, features: pd.DataFrame, meta_target: pd.Series) -> "SecondaryModel":
        """训练 secondary model

        Args:
            features: feature DataFrame（每行一个入场点）
            meta_target: 0/1 二分类 target（1 = primary 方向正确）
        """
        if not isinstance(features, pd.DataFrame):
            features = pd.DataFrame(features)
        if not isinstance(meta_target, pd.Series):
            meta_target = pd.Series(meta_target)

        # 对齐 + 清 NaN
        y = meta_target.rename("__y__")
        aligned = pd.concat([features, y], axis=1).dropna()
        if len(aligned) < 20:
            raise ValueError(f"训练样本太少（{len(aligned)}），至少需要 20")

        self.feature_names = list(features.columns)
        X = aligned[self.feature_names].values
        y_arr = aligned["__y__"].astype(int).values

        # 至少要有两类（全 0 或全 1 时 sklearn 会报错）
        if len(np.unique(y_arr)) < 2:
            raise ValueError(f"meta_target 只有一类 ({np.unique(y_arr)})，无法训练")

        self.clf.fit(X, y_arr)
        self.is_fitted = True
        return self

    def predict_confidence(self, features: pd.DataFrame) -> pd.Series:
        """输出 P(primary 方向对) ∈ [0, 1]"""
        if not self.is_fitted:
            raise RuntimeError("SecondaryModel 未训练，先 fit()")
        if not isinstance(features, pd.DataFrame):
            features = pd.DataFrame(features)
        # 按训练时列顺序对齐，缺失列填 0
        X = features.reindex(columns=self.feature_names).fillna(0).values
        proba = self.clf.predict_proba(X)
        # class=1 在第几列（防御 classes_ = [0, 1] / [1] 两种情况）
        classes = list(self.clf.classes_)
        if 1 in classes:
            col_idx = classes.index(1)
        else:
            col_idx = len(classes) - 1  # 退化为取最后一列
        return pd.Series(proba[:, col_idx], index=features.index, name="confidence")

    def compute_position(
        self,
        side: pd.Series,
        features: pd.DataFrame,
        max_position: float = 1.0,
    ) -> pd.Series:
        """position = side × confidence × max_position"""
        confidence = self.predict_confidence(features)
        # 对齐索引
        aligned = pd.concat([side.rename("side"), confidence], axis=1).fillna(0)
        return (aligned["side"] * aligned["confidence"] * max_position).rename("position")


# ============================================================
# Feature builder
# ============================================================

def build_features_from_alphas(
    df: pd.DataFrame,
    alpha_names: list[str] | None = None,
    alpha_engine: object | None = None,
) -> pd.DataFrame:
    """从 Alpha101（或任意 alpha 引擎）构建 feature DataFrame

    Args:
        df: OHLCV
        alpha_names: 指定子集；None 则用引擎全部输出
        alpha_engine: Alpha101 实例（可传入 mock）

    Returns:
        DataFrame，每列一个 alpha feature
    """
    if alpha_engine is None:
        from quant_scanner.factors.alpha101 import Alpha101
        alpha_engine = Alpha101()
    all_alphas = alpha_engine.compute_all(df)
    if alpha_names is not None:
        all_alphas = {k: v for k, v in all_alphas.items() if k in alpha_names}
    feats = pd.DataFrame(all_alphas)
    # 稳定性：winsorize 极值 + z-score + fillna（防 sklearn overflow）
    # 1. 用 1%/99% 分位数裁剪极值
    for col in feats.columns:
        s = feats[col]
        if s.notna().sum() < 10:
            continue
        lo, hi = s.quantile(0.01), s.quantile(0.99)
        if hi > lo:
            feats[col] = s.clip(lo, hi)
    # 2. z-score 标准化
    means = feats.mean()
    stds = feats.std().replace(0, np.nan)
    feats = (feats - means) / stds
    # 3. 兜底 fillna（仍可能有 inf/nan）
    feats = feats.replace([np.inf, -np.inf], np.nan).fillna(0)
    # 最后再裁一次，确保无极端值
    feats = feats.clip(-10, 10)
    return feats


# ============================================================
# 端到端 pipeline
# ============================================================

@dataclass
class PipelineResult:
    """run_meta_labeling_pipeline 的结构化结果"""
    # 核心输出
    model: SecondaryModel
    meta_labels: pd.DataFrame
    confidence: pd.Series
    position: pd.Series
    # 切分信息
    train_size: int
    test_size: int
    split_date: pd.Timestamp | None
    # 性能指标
    primary_precision: float        # baseline：primary 原始命中率
    secondary_oos_precision: float  # secondary 过滤后的 OOS 命中率
    secondary_oos_filter_rate: float  # secondary 过滤掉的比例
    # 评估
    oos_predictions: pd.Series      # 测试集 confidence
    oos_actual: pd.Series           # 测试集实际 meta_target


def run_meta_labeling_pipeline(
    df: pd.DataFrame,
    primary_alpha: pd.Series,
    feature_alphas: dict[str, pd.Series] | pd.DataFrame | None = None,
    tp_atr_mult: float = 2.0,
    sl_atr_mult: float = 2.0,
    atr_window: int = 20,
    vertical_barrier_bars: int = 10,
    test_size: float = 0.3,
    max_position: float = 1.0,
    min_train_samples: int = 50,
    clf: object | None = None,
) -> PipelineResult:
    """端到端 meta-labeling 流水线

    流程：
    1. primary_alpha → primary_side（sign）
    2. MetaLabeler 跑 triple-barrier → meta_target
    3. 构建 features（默认从 Alpha101）
    4. 按 t_in 对齐到 meta 样本
    5. 时序切分 train/test（不打乱）
    6. 训练 SecondaryModel
    7. 预测 confidence → position = side × confidence
    8. OOS 评估：secondary 过滤后的精度 vs primary baseline

    Args:
        df: OHLCV
        primary_alpha: 方向预测序列（如 alpha_42），>0 做多、<0 做空
        feature_alphas: feature 字典；None 则从 Alpha101 全跑
        tp_atr_mult, sl_atr_mult, atr_window, vertical_barrier_bars: MetaLabeler 参数
        test_size: 后 test_size 比例作 OOS
        max_position: 最大仓位上限
        min_train_samples: 训练集最少样本数

    Returns:
        PipelineResult
    """
    from quant_scanner.labels.meta_labeling import MetaLabeler

    # 1. primary_side：从 alpha 取 sign（0/NaN 当作 +1，默认做多）
    primary_side = np.sign(primary_alpha.fillna(1.0)).replace(0, 1).astype(int)
    if isinstance(primary_side, pd.Series):
        primary_side = primary_side.reindex(df.index).fillna(1).astype(int)

    # 2. meta labels
    labeler = MetaLabeler(
        tp_atr_mult=tp_atr_mult,
        sl_atr_mult=sl_atr_mult,
        atr_window=atr_window,
        vertical_barrier_bars=vertical_barrier_bars,
    )
    meta = labeler.prepare_meta_labels(df, primary_side=primary_side)
    if len(meta) < min_train_samples:
        raise ValueError(
            f"meta 样本太少（{len(meta)} < {min_train_samples}），"
            "考虑扩大 period 或减小 vertical_barrier_bars"
        )

    # 3. features
    if feature_alphas is None:
        features = build_features_from_alphas(df)
    elif isinstance(feature_alphas, pd.DataFrame):
        features = feature_alphas.copy()
    else:
        features = pd.DataFrame(feature_alphas)

    # 4. 对齐到 t_in（每个入场点的 feature）
    features_at_entry = features.reindex(meta["t_in"]).fillna(0)
    features_at_entry.index = meta.index  # 用 meta 的整数索引方便后续切分

    # 5. 时序切分
    n = len(meta)
    split = int(n * (1 - test_size))
    X_train = features_at_entry.iloc[:split]
    X_test = features_at_entry.iloc[split:]
    y_train = meta["meta_target"].iloc[:split]
    y_test = meta["meta_target"].iloc[split:]
    split_date = meta["t_in"].iloc[split] if split < n else None

    # 6. 训练
    model = SecondaryModel(clf=clf) if clf is not None else SecondaryModel()
    model.fit(X_train, y_train)

    # 7. 预测（全样本）+ position
    confidence = model.predict_confidence(features_at_entry)
    confidence.name = "confidence"
    position = (meta["side"].values * confidence.values * max_position)
    position = pd.Series(position, index=meta.index, name="position")

    # 8. OOS 评估
    # primary baseline：测试集原始命中率
    primary_oos_precision = float(y_test.mean()) if len(y_test) > 0 else float("nan")
    # secondary：高 confidence 子集（≥ threshold）的命中率
    oos_conf = confidence.iloc[split:]
    high_conf_mask = oos_conf >= model.threshold
    if high_conf_mask.sum() > 0:
        secondary_oos_precision = float(y_test[high_conf_mask].mean())
    else:
        secondary_oos_precision = float("nan")
    filter_rate = float(1 - high_conf_mask.mean())  # 被过滤的比例

    return PipelineResult(
        model=model,
        meta_labels=meta,
        confidence=confidence,
        position=position,
        train_size=split,
        test_size=n - split,
        split_date=split_date,
        primary_precision=primary_oos_precision,
        secondary_oos_precision=secondary_oos_precision,
        secondary_oos_filter_rate=filter_rate,
        oos_predictions=oos_conf,
        oos_actual=y_test,
    )


# ============================================================
# Purged K-Fold CV 评估（串起任务 #94）
# ============================================================

def cross_validate_secondary(
    df: pd.DataFrame,
    primary_alpha: pd.Series,
    feature_alphas: dict[str, pd.Series] | pd.DataFrame | None = None,
    n_splits: int = 5,
    purge_bars: int = 10,
    embargo_bars: int = 5,
    tp_atr_mult: float = 2.0,
    sl_atr_mult: float = 2.0,
    atr_window: int = 20,
    vertical_barrier_bars: int = 10,
    clf: object | None = None,
) -> dict:
    """用 Purged K-Fold 评估 secondary model 的真实 OOS 精度

    每折：训练 secondary → 在 OOS 上预测 confidence → 看「高 confidence 过滤后精度提升」

    Returns:
        {
            "fold_results": [{split, train_size, test_size, primary_oos_precision,
                              secondary_oos_precision, filter_rate, lift}],
            "mean_primary_precision": baseline 平均精度,
            "mean_secondary_precision": secondary 过滤后平均精度,
            "mean_lift": 平均提升（secondary - primary）,
            "mean_filter_rate": 平均过滤比例,
            "n_total": 总样本数,
        }
    """
    from quant_scanner.eval.purged_kfold import purged_kfold_indices
    from quant_scanner.labels.meta_labeling import MetaLabeler

    # 准备 side + meta + features（同 pipeline，但不切 train/test，用 K-fold）
    primary_side = np.sign(primary_alpha.fillna(1.0)).replace(0, 1).astype(int)
    if isinstance(primary_side, pd.Series):
        primary_side = primary_side.reindex(df.index).fillna(1).astype(int)

    labeler = MetaLabeler(
        tp_atr_mult=tp_atr_mult, sl_atr_mult=sl_atr_mult,
        atr_window=atr_window, vertical_barrier_bars=vertical_barrier_bars,
    )
    meta = labeler.prepare_meta_labels(df, primary_side=primary_side)

    if feature_alphas is None:
        features = build_features_from_alphas(df)
    elif isinstance(feature_alphas, pd.DataFrame):
        features = feature_alphas.copy()
    else:
        features = pd.DataFrame(feature_alphas)

    features_at_entry = features.reindex(meta["t_in"]).fillna(0)
    features_at_entry.index = meta.index
    y = meta["meta_target"]

    fold_results: list[dict] = []
    n = len(meta)
    primary_precisions: list[float] = []
    secondary_precisions: list[float] = []
    lifts: list[float] = []
    filter_rates: list[float] = []

    for fold_idx, (train_idx, test_idx) in enumerate(
        purged_kfold_indices(n, n_splits=n_splits,
                             purge_bars=purge_bars, embargo_bars=embargo_bars)
    ):
        if len(train_idx) < 50 or len(test_idx) < 10:
            continue
        X_train = features_at_entry.iloc[train_idx]
        X_test = features_at_entry.iloc[test_idx]
        y_train = y.iloc[train_idx]
        y_test = y.iloc[test_idx]

        if len(np.unique(y_train)) < 2:
            continue  # 单类无法训练

        model = SecondaryModel(clf=_clone_clf(clf)) if clf is not None else SecondaryModel()
        try:
            model.fit(X_train, y_train)
        except Exception:
            continue

        conf = model.predict_confidence(X_test)
        primary_prec = float(y_test.mean())
        high_conf_mask = conf >= model.threshold
        if high_conf_mask.sum() > 0:
            sec_prec = float(y_test[high_conf_mask].mean())
        else:
            sec_prec = float("nan")
        filter_rate = float(1 - high_conf_mask.mean())

        primary_precisions.append(primary_prec)
        if sec_prec == sec_prec:
            secondary_precisions.append(sec_prec)
            lifts.append(sec_prec - primary_prec)
        filter_rates.append(filter_rate)

        fold_results.append({
            "fold": fold_idx,
            "train_size": int(len(train_idx)),
            "test_size": int(len(test_idx)),
            "primary_oos_precision": primary_prec,
            "secondary_oos_precision": sec_prec,
            "filter_rate": filter_rate,
            "lift": sec_prec - primary_prec if sec_prec == sec_prec else float("nan"),
        })

    return {
        "fold_results": fold_results,
        "mean_primary_precision": float(np.mean(primary_precisions)) if primary_precisions else float("nan"),
        "mean_secondary_precision": float(np.mean(secondary_precisions)) if secondary_precisions else float("nan"),
        "mean_lift": float(np.mean(lifts)) if lifts else float("nan"),
        "mean_filter_rate": float(np.mean(filter_rates)) if filter_rates else float("nan"),
        "n_total": n,
    }


# ============================================================
# Pipeline 摘要
# ============================================================

def pipeline_summary(result: PipelineResult) -> dict:
    """Pipeline 结果摘要"""
    return {
        "total_samples": len(result.meta_labels),
        "train_size": result.train_size,
        "test_size": result.test_size,
        "split_date": str(result.split_date) if result.split_date else None,
        "primary_precision": result.primary_precision,
        "secondary_oos_precision": result.secondary_oos_precision,
        "lift": (result.secondary_oos_precision - result.primary_precision)
                if result.secondary_oos_precision == result.secondary_oos_precision else float("nan"),
        "filter_rate": result.secondary_oos_filter_rate,
        "threshold": result.model.threshold,
        "n_features": len(result.model.feature_names),
        "feature_names": result.model.feature_names[:10],  # 只展示 top 10
    }
