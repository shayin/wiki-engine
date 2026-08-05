"""SHAP 特征重要性评估（方法 3 入口）

来源：Lundberg & Lee 2017《A Unified Approach to Interpreting Model Predictions》NeurIPS

**核心价值**：
- Alpha 因子（模式 4-8）只能看「单个 alpha vs 收益」的线性关系
- SHAP 揭示「多个 alpha 联合作用时，哪个 alpha 贡献最大」的非线性结构
- 配合 XGBoost/LightGBM 等树模型，捕捉 alpha 之间的交互效应

**方法 3 入口**：
- 方法 1（算子库）：手工组合 alpha 公式
- 方法 2（Alpha101）：论文现成 101 个
- 方法 3（GP + SHAP）：数据驱动挖新因子 + 解释（本模块是 SHAP 部分）

**接口设计**：
- `ShapFactorEvaluator` 类：把 20 个 alpha 当作特征，XGBoost 预测前瞻收益，SHAP 算贡献度
- 输出：每个 alpha 的 mean |SHAP|、SHAP 方向（正/负）、特征重要性排序

**典型用法**：
```python
from quant_scanner.data.loader import DataLoader
from quant_scanner.factors.alpha101 import Alpha101
from quant_scanner.factors.shap_eval import ShapFactorEvaluator
from quant_scanner.factors.operators import log_returns

loader = DataLoader()
df = loader.load("NVDA", period="2y")
fwd = log_returns(df["close"]).shift(-5)

alpha = Alpha101()
features = alpha.compute_all(df)  # dict[str, Series]

evaluator = ShapFactorEvaluator()
result = evaluator.evaluate(features, fwd)
# result.top_features: [("alpha_3", 0.15, "positive"), ...]
```

**SHAP vs IC 的差异**：

| 维度 | IC（单因子） | SHAP（多因子） |
|------|-------------|---------------|
| 衡量 | 单 alpha vs 收益的秩相关 | 多 alpha 联合模型中各 alpha 的边际贡献 |
| 模型 | 无（纯统计） | XGBoost 树模型 |
| 交互 | 不能捕捉 | 捕捉 alpha 间交互效应 |
| 输出 | IC ∈ [-1, 1] | mean abs SHAP（非负） + 方向 |
| 用途 | 单 alpha 是否有效 | 多 alpha 中谁更重要 |
"""
from __future__ import annotations

import numpy as np
import pandas as pd


class ShapFactorEvaluator:
    """SHAP 特征重要性评估器

    把 alpha 因子作为特征训练 XGBoost 模型预测前瞻收益，
    再用 SHAP 算每个特征对预测的平均贡献度。
    """

    def __init__(
        self,
        n_estimators: int = 200,
        max_depth: int = 4,
        learning_rate: float = 0.05,
        test_size: float = 0.3,
        random_state: int = 42,
        backend: str = "sklearn",
    ):
        """
        Args:
            n_estimators: 树的数量
            max_depth: 树最大深度（浅 = 防过拟合）
            learning_rate: 学习率
            test_size: 测试集比例（用于评估泛化）
            random_state: 随机种子
            backend: "sklearn"（默认，GradientBoostingRegressor，无系统依赖）
                     或 "xgboost"（需 brew install libomp）
        """
        self.n_estimators = n_estimators
        self.max_depth = max_depth
        self.learning_rate = learning_rate
        self.test_size = test_size
        self.random_state = random_state
        self.backend = backend

    def evaluate(
        self,
        features: dict[str, pd.Series],
        forward_returns: pd.Series,
        top_n: int = 10,
    ) -> dict:
        """训练 XGBoost + SHAP 评估

        Args:
            features: alpha 名 → 因子值 Series（来自 Alpha101.compute_all）
            forward_returns: 前瞻收益 Series
            top_n: 返回 top N 重要特征

        Returns:
            dict:
            - "top_features": [(name, mean_abs_shap, direction), ...] 按 SHAP 降序
            - "feature_importance": {name: mean_abs_shap}
            - "shap_values": ndarray（nsamples × nfeatures）
            - "feature_names": list[str]
            - "test_r2": float（XGBoost 测试集 R²，泛化能力）
            - "n_samples": int（有效样本数）
        """
        try:
            import shap
            from sklearn.model_selection import train_test_split
        except ImportError as e:
            return {"error": f"缺少依赖: {e}. 请运行 pip install shap scikit-learn"}

        # 构造特征矩阵
        df_features = pd.DataFrame(features)
        df_features["__target__"] = forward_returns

        # 对齐 + 删 NaN
        df_features = df_features.dropna()
        if len(df_features) < 50:
            return {"error": f"有效样本不足（{len(df_features)} < 50），无法训练"}

        feature_names = [c for c in df_features.columns if c != "__target__"]
        X = df_features[feature_names].values
        y = df_features["__target__"].values
        n_samples = len(X)

        # train/test split
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=self.test_size, random_state=self.random_state,
        )

        # 训练树模型（按 backend 切换）
        if self.backend == "xgboost":
            try:
                import xgboost as xgb
                model = xgb.XGBRegressor(
                    n_estimators=self.n_estimators,
                    max_depth=self.max_depth,
                    learning_rate=self.learning_rate,
                    random_state=self.random_state,
                    n_jobs=1,
                    verbosity=0,
                )
                model.fit(X_train, y_train)
            except Exception as e:
                # libomp 缺失等系统级问题 → 降级到 sklearn
                from sklearn.ensemble import GradientBoostingRegressor
                model = GradientBoostingRegressor(
                    n_estimators=self.n_estimators,
                    max_depth=self.max_depth,
                    learning_rate=self.learning_rate,
                    random_state=self.random_state,
                )
                model.fit(X_train, y_train)
        else:
            from sklearn.ensemble import GradientBoostingRegressor
            model = GradientBoostingRegressor(
                n_estimators=self.n_estimators,
                max_depth=self.max_depth,
                learning_rate=self.learning_rate,
                random_state=self.random_state,
            )
            model.fit(X_train, y_train)

        # 测试集 R²（泛化能力）
        test_r2 = float(model.score(X_test, y_test))

        # SHAP 值（TreeExplainer 对树模型最快最准）
        explainer = shap.TreeExplainer(model)
        shap_values = explainer.shap_values(X)

        # 处理 shap_values 可能是 list（多分类）的情况
        if isinstance(shap_values, list):
            shap_values = shap_values[0]

        # mean |SHAP| per feature
        mean_abs_shap = np.abs(shap_values).mean(axis=0)

        # SHAP 方向：用 correlation(feature_value, shap_value)
        # 正相关 = feature 越大 SHAP 越大 → 推动预测变大 = positive
        # 负相关 = feature 越大 SHAP 越小 → 反向因子 = negative
        directions = []
        for i in range(shap_values.shape[1]):
            feat_vals = X[:, i]
            shap_vals = shap_values[:, i]
            if np.std(feat_vals) > 0 and np.std(shap_vals) > 0:
                corr = float(np.corrcoef(feat_vals, shap_vals)[0, 1])
                directions.append("positive" if corr > 0 else "negative")
            else:
                directions.append("neutral")

        # 排序
        ranking = sorted(
            zip(feature_names, mean_abs_shap, directions),
            key=lambda x: -x[1],
        )

        return {
            "top_features": ranking[:top_n],
            "feature_importance": dict(zip(feature_names, mean_abs_shap)),
            "shap_values": shap_values,
            "feature_names": feature_names,
            "test_r2": test_r2,
            "n_samples": n_samples,
            "model": model,  # 留给调用方做深入分析
        }

    def evaluate_simple(
        self,
        features: dict[str, pd.Series],
        forward_returns: pd.Series,
        top_n: int = 5,
    ) -> list[tuple[str, float, str]]:
        """简化版：只返回 top N 特征列表（不返回 SHAP 矩阵）

        Returns:
            [(name, mean_abs_shap, direction), ...] top N
        """
        result = self.evaluate(features, forward_returns, top_n=top_n)
        if "error" in result:
            return []
        return result["top_features"]


__all__ = ["ShapFactorEvaluator"]
