"""Deep Factor Model — Gu-Kelly-Xiu (2020) JFE 多模型对比

来源：
- Gu, Kelly, Xiu (2020) "Empirical Asset Pricing via Machine Learning"
  *Journal of Financial Economics* 134(2): 385-424

**核心论文发现**：
对 100+ 个 alpha 因子，用 10+ 种 ML 方法做前瞻收益预测的对比：

| 方法 | OOS R² | 排名 |
|------|--------|------|
| Gradient Boosted Trees | ~0.40% | 1 |
| Random Forest | ~0.30% | 2 |
| Neural Network (MLP/5-layer) | ~0.30% | 3 |
| Elastic Net | ~0.10% | 10 |
| OLS-3 (Fama-French) | < 0% | 13 |

**关键启示**：
1. **树模型最优**（非线性 + 抗过拟合）
2. **神经网络第二**（适合极大数据，小样本不如树）
3. **线性模型最差**（捕获不到非线性交互）

**本模块设计**：
不重复造轮子，而是封装 sklearn 三模型对比，用 Purged K-Fold 评估。
让用户知道：对当前标的，哪个 ML 方法最适合。

**典型用法**：

```python
from quant_scanner.ml.deep_factor import compare_ml_models
result = compare_ml_models(
    features, target,
    n_splits=5, purge_bars=10, embargo_bars=5,
)
print(result.summary())  # 每个模型的 OOS 性能
```
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.neural_network import MLPClassifier


# ============================================================
# 模型工厂
# ============================================================

def make_models(seed: int = 42) -> dict[str, object]:
    """构造 3 个 ML 模型（Gu-Kelly-Xiu 2020 推荐配置）

    - **LR**: LogisticRegression（liblinear，L2 正则）
    - **GBM**: GradientBoostingClassifier（100 estimators, depth=3，抗过拟合）
    - **MLP**: MLPClassifier（2 hidden layers [64, 32]，ReLU，adam）
    """
    return {
        "LR (Linear)": LogisticRegression(
            max_iter=1000, solver="liblinear", random_state=seed,
        ),
        "GBM (Trees)": GradientBoostingClassifier(
            n_estimators=100, max_depth=3, random_state=seed,
        ),
        "MLP (Neural Net)": MLPClassifier(
            hidden_layer_sizes=(64, 32),
            activation="relu",
            solver="adam",
            alpha=1e-4,          # L2 正则
            batch_size="auto",
            learning_rate_init=1e-3,
            max_iter=200,
            early_stopping=True,  # 防过拟合
            validation_fraction=0.15,
            random_state=seed,
        ),
    }


# ============================================================
# 对比评估
# ============================================================

@dataclass
class ModelFoldResult:
    """单个 fold 的结果"""
    fold: int
    train_size: int
    test_size: int
    precision: float       # 高置信子集命中率
    filter_rate: float     # 被过滤比例


@dataclass
class ModelComparison:
    """单模型 K-Fold 评估结果"""
    name: str
    fold_results: list[ModelFoldResult] = field(default_factory=list)
    mean_precision: float = float("nan")
    mean_filter_rate: float = float("nan")
    n_folds_run: int = 0
    fit_failures: int = 0

    def aggregate(self) -> None:
        if not self.fold_results:
            return
        precs = [r.precision for r in self.fold_results if r.precision == r.precision]
        frs = [r.filter_rate for r in self.fold_results if r.filter_rate == r.filter_rate]
        self.mean_precision = float(np.mean(precs)) if precs else float("nan")
        self.mean_filter_rate = float(np.mean(frs)) if frs else float("nan")
        self.n_folds_run = len(self.fold_results)


@dataclass
class ComparisonResult:
    """多模型对比结果"""
    baseline_precision: float         # primary 原始命中率
    models: dict[str, ModelComparison] = field(default_factory=dict)
    n_total: int = 0

    def best_model(self) -> tuple[str, ModelComparison] | None:
        """返回 OOS precision 最高的模型"""
        if not self.models:
            return None
        best = max(
            self.models.items(),
            key=lambda kv: kv[1].mean_precision if kv[1].mean_precision == kv[1].mean_precision else -1,
        )
        return best

    def summary(self) -> dict:
        best = self.best_model()
        return {
            "baseline_precision": self.baseline_precision,
            "n_total": self.n_total,
            "models": {
                name: {
                    "mean_precision": m.mean_precision,
                    "mean_filter_rate": m.mean_filter_rate,
                    "n_folds_run": m.n_folds_run,
                    "fit_failures": m.fit_failures,
                    "lift_vs_baseline": (
                        m.mean_precision - self.baseline_precision
                        if m.mean_precision == m.mean_precision else float("nan")
                    ),
                }
                for name, m in self.models.items()
            },
            "best_model": best[0] if best else None,
            "best_precision": best[1].mean_precision if best else float("nan"),
        }


def compare_ml_models(
    features: pd.DataFrame,
    target: pd.Series,
    n_splits: int = 5,
    purge_bars: int = 10,
    embargo_bars: int = 5,
    threshold: float = 0.5,
    models: dict[str, object] | None = None,
    seed: int = 42,
) -> ComparisonResult:
    """用 Purged K-Fold 对比多个 ML 模型的 OOS 表现（Gu-Kelly-Xiu 2020）

    流程：
    1. 对每个模型跑 Purged K-Fold CV
    2. 每折记录 OOS 高置信子集命中率
    3. 对比 mean_precision，找出最佳模型

    Args:
        features: 特征 DataFrame
        target: 0/1 二分类 target
        n_splits: K-fold 数
        purge_bars: 清除 bar 数
        embargo_bars: 禁运 bar 数
        threshold: confidence ≥ threshold 视为高置信子集
        models: 自定义模型字典（None 用默认 3 模型）
        seed: 随机种子

    Returns:
        ComparisonResult
    """
    from quant_scanner.eval.purged_kfold import purged_kfold_indices
    from sklearn.base import clone
    import warnings as _warnings

    if models is None:
        models = make_models(seed=seed)

    # 特征标准化（防 MLP/sklearn 溢出，保证数值稳定）
    from sklearn.preprocessing import StandardScaler
    scaler = StandardScaler()
    try:
        features_scaled = pd.DataFrame(
            scaler.fit_transform(features.fillna(0)),
            index=features.index,
            columns=features.columns,
        )
        features_scaled = features_scaled.replace([np.inf, -np.inf], 0).clip(-10, 10)
    except Exception:
        features_scaled = features.fillna(0).replace([np.inf, -np.inf], 0).clip(-10, 10)

    # 初始化结果对象
    baseline_prec = float(target.mean())
    comparisons = {name: ModelComparison(name=name) for name in models}
    n_total = len(target)

    # 对每个模型跑 K-Fold
    for name, base_clf in models.items():
        for fold_idx, (train_idx, test_idx) in enumerate(
            purged_kfold_indices(
                n_total, n_splits=n_splits,
                purge_bars=purge_bars, embargo_bars=embargo_bars,
            )
        ):
            if len(train_idx) < 50 or len(test_idx) < 10:
                continue

            X_train = features_scaled.iloc[train_idx].values
            X_test = features_scaled.iloc[test_idx].values
            y_train = target.iloc[train_idx].values.astype(int)
            y_test = target.iloc[test_idx].values.astype(int)

            if len(np.unique(y_train)) < 2:
                continue  # 单类无法训练

            try:
                clf = clone(base_clf)
                with _warnings.catch_warnings(), np.errstate(over="ignore", invalid="ignore", divide="ignore"):
                    _warnings.simplefilter("ignore", category=RuntimeWarning)
                    clf.fit(X_train, y_train)
                    proba = clf.predict_proba(X_test)
            except Exception:
                comparisons[name].fit_failures += 1
                continue
            classes = list(clf.classes_)
            if 1 in classes:
                conf = proba[:, classes.index(1)]
            else:
                conf = proba[:, -1]

            high_conf = conf >= threshold
            if high_conf.sum() > 0:
                prec = float(y_test[high_conf].mean())
            else:
                prec = float("nan")
            filter_rate = float(1 - high_conf.mean())

            comparisons[name].fold_results.append(
                ModelFoldResult(
                    fold=fold_idx,
                    train_size=len(train_idx),
                    test_size=len(test_idx),
                    precision=prec,
                    filter_rate=filter_rate,
                )
            )
        comparisons[name].aggregate()

    return ComparisonResult(
        baseline_precision=baseline_prec,
        models=comparisons,
        n_total=n_total,
    )


# ============================================================
# 端到端流水线（与 secondary_model 的 pipeline 对接）
# ============================================================

def run_model_comparison_pipeline(
    df: pd.DataFrame,
    primary_alpha: pd.Series,
    feature_alphas: dict[str, pd.Series] | pd.DataFrame | None = None,
    tp_atr_mult: float = 2.0,
    sl_atr_mult: float = 2.0,
    atr_window: int = 20,
    vertical_barrier_bars: int = 10,
    n_splits: int = 5,
    top_features: int = 10,
) -> ComparisonResult:
    """端到端：构建特征 → Meta-Labeling → 对比 3 模型

    Args:
        df: OHLCV
        primary_alpha: 方向预测（>0 多，<0 空）
        feature_alphas: feature 字典；None 则自动构建
        tp_atr_mult, sl_atr_mult, atr_window, vertical_barrier_bars: MetaLabeler 参数
        n_splits: Purged K-Fold 数
        top_features: 按 |IC| 取 top N feature 防过拟合

    Returns:
        ComparisonResult
    """
    from quant_scanner.labels.meta_labeling import MetaLabeler
    from quant_scanner.ml.secondary_model import build_features_from_alphas
    from quant_scanner.factors.operators import factor_ic, log_returns

    # primary side
    primary_side = np.sign(primary_alpha.fillna(1.0)).replace(0, 1).astype(int)
    if isinstance(primary_side, pd.Series):
        primary_side = primary_side.reindex(df.index).fillna(1).astype(int)

    # meta target
    labeler = MetaLabeler(
        tp_atr_mult=tp_atr_mult, sl_atr_mult=sl_atr_mult,
        atr_window=atr_window, vertical_barrier_bars=vertical_barrier_bars,
    )
    meta = labeler.prepare_meta_labels(df, primary_side=primary_side)

    # features
    if feature_alphas is None:
        features = build_features_from_alphas(df)
    elif isinstance(feature_alphas, pd.DataFrame):
        features = feature_alphas.copy()
    else:
        features = pd.DataFrame(feature_alphas)

    features_at_entry = features.reindex(meta["t_in"]).fillna(0)
    features_at_entry.index = meta.index
    y = meta["meta_target"]

    # 选 top features
    if top_features and isinstance(features, pd.DataFrame) and len(features.columns) > top_features:
        fwd = log_returns(df["close"]).shift(-5)
        ic_scores = {c: factor_ic(features[c], fwd) for c in features.columns}
        sorted_cols = sorted(
            [c for c in ic_scores if ic_scores[c] == ic_scores[c]],
            key=lambda c: -abs(ic_scores[c]),
        )[:top_features]
        features_at_entry = features_at_entry[sorted_cols]

    purge = max(vertical_barrier_bars, 5)

    return compare_ml_models(
        features=features_at_entry,
        target=y,
        n_splits=n_splits,
        purge_bars=purge,
        embargo_bars=max(purge // 2, 2),
    )


if __name__ == "__main__":
    # Self-test with synthetic data
    rng = np.random.default_rng(42)
    n = 500

    # Synthetic features
    X = pd.DataFrame({
        "f1": rng.normal(0, 1, n),
        "f2": rng.normal(0, 1, n),
        "f3": rng.normal(0, 1, n),
    })
    # Synthesized target: depends on f1 + 0.5*f2 (nonlinear via sign)
    logits = X["f1"] + 0.5 * X["f2"] + 0.3 * X["f1"] * X["f2"]
    proba = 1 / (1 + np.exp(-logits))
    y = pd.Series((rng.uniform(0, 1, n) < proba).astype(int))

    result = compare_ml_models(X, y, n_splits=3, purge_bars=5, embargo_bars=2)
    summary = result.summary()

    print(f"\nBaseline precision: {summary['baseline_precision']:.3f}")
    for name, info in summary["models"].items():
        lift = info["lift_vs_baseline"]
        print(f"  {name:25s} prec={info['mean_precision']:.3f} "
              f"filter={info['mean_filter_rate']:.1%} lift={lift:+.3f}")
    print(f"\nBest: {summary['best_model']} (prec={summary['best_precision']:.3f})")
