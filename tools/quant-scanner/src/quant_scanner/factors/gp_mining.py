"""GP 因子挖掘（方法 3 主菜）

来源：Koza 1992《Genetic Programming: On the Programming of Computers
by Means of Natural Selection》 + WorldQuant Alpha 因子挖掘实践

**核心价值**：
- Alpha101（方法 2）只能搬运论文现成公式，挖掘范围受限于原作者
- SHAP（方法 3 入口）只能评估已有因子组合的边际贡献
- **GP（方法 3 主菜）数据驱动挖新因子**：在算子集中搜索因子表达式，
  以 IC（信息系数）为适应度，进化 N 代后输出新 alpha 公式
- 与 SHAP 配合：GP 挖出候选 → SHAP 验证多因子模型中的真实边际贡献

**接口设计**：
- `GPFactorMiner` 类：无外部依赖（不引 gplearn），自实现基因树 + 锦标赛选择 + 交叉/变异
- 算子集：直接复用 `operators.py` 已有 50+ 算子（ts_mean/div/ts_rank/...）
- 适应度 = |Spearman IC|（调用 operators.factor_ic）
- 输出：最佳因子公式（可序列化的 AST）+ 其 IC/IR + 进化历史

**典型用法**：
```python
from quant_scanner.data.loader import DataLoader
from quant_scanner.factors.operators import log_returns
from quant_scanner.factors.gp_mining import GPFactorMiner

loader = DataLoader()
df = loader.load("NVDA", period="2y")
fwd = log_returns(df["close"]).shift(-5)

miner = GPFactorMiner(population_size=50, generations=20, random_state=42)
result = miner.mine(df, fwd, feature_cols=["close", "volume"])
# result.best_formula: "ts_rank(div(ts_delta(close, 5), volume), 20)"
# result.best_ic: 0.082
```

**设计权衡**（为何自实现而非引 gplearn）：
- gplearn 是行业标准但 ~2000 行复杂实现，且引入 sklearn 兼容性约束
- 本模块 ~300 行紧凑实现，覆盖核心 GP 机制（选择/交叉/变异/精英保留）
- 算子直接复用 operators.py，零重复
- 与项目最小依赖原则一致

**与 SHAP 的衔接**：
GP 挖出的新因子必须用 SHAP（shap_eval.py）在多因子模型中验证，
排除与已有 alpha 信息冗余的"假新因子"。
"""
from __future__ import annotations

import math
import random
import string
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import operators as ops
from .operators import factor_ic, factor_ir


# ----------------------------------------------------------------------
# 算子注册表
# ----------------------------------------------------------------------

# 一元算子（接 1 个 Series → Series）
UNARY_OPS: dict[str, dict] = {
    "ts_mean":      {"fn": ops.ts_mean,      "default_window": 20, "max_window": 60},
    "ts_std":       {"fn": ops.ts_std,        "default_window": 20, "max_window": 60},
    "ts_rank":      {"fn": ops.ts_rank,       "default_window": 20, "max_window": 60},
    "ts_delta":     {"fn": ops.ts_delta,      "default_window": 5,  "max_window": 20},
    "ts_delay":     {"fn": ops.ts_delay,      "default_window": 5,  "max_window": 20},
    "ts_skew":      {"fn": ops.ts_skew,       "default_window": 20, "max_window": 60},
    "ts_kurt":      {"fn": ops.ts_kurt,       "default_window": 20, "max_window": 60},
    "ts_decay_linear": {"fn": ops.ts_decay_linear, "default_window": 10, "max_window": 30},
    "ts_sum":       {"fn": ops.ts_sum,        "default_window": 10, "max_window": 30},
    "ts_max":       {"fn": ops.ts_max,        "default_window": 20, "max_window": 60},
    "ts_min":       {"fn": ops.ts_min,        "default_window": 20, "max_window": 60},
    "abs_":         {"fn": ops.abs_,          "default_window": None, "max_window": None},
    "sign":         {"fn": ops.sign,          "default_window": None, "max_window": None},
    "log":          {"fn": ops.log,           "default_window": None, "max_window": None},
    "sigmoid":      {"fn": ops.sigmoid,       "default_window": None, "max_window": None},
    "relu":         {"fn": ops.relu,          "default_window": None, "max_window": None},
    "rsi":          {"fn": ops.rsi,           "default_window": 14, "max_window": 28},
    "rank":         {"fn": ops.rank,          "default_window": None, "max_window": None},
    "scale":        {"fn": ops.scale,         "default_window": None, "max_window": None},
    "zscore":       {"fn": ops.zscore,        "default_window": 20, "max_window": 60},
    "returns":      {"fn": ops.returns,       "default_window": 5,  "max_window": 20},
    "log_returns":  {"fn": ops.log_returns,   "default_window": 5,  "max_window": 20},
}

# 二元算子（接 2 个 Series → Series）
BINARY_OPS: dict[str, dict] = {
    "add":  {"fn": ops.add},
    "sub":  {"fn": ops.sub},
    "mul":  {"fn": ops.mul},
    "div":  {"fn": ops.div},
    "max_": {"fn": ops.max_},
    "min_": {"fn": ops.min_},
    "ts_corr": {"fn": ops.ts_corr, "default_window": 20, "max_window": 60},
    "ts_cov":  {"fn": ops.ts_cov,  "default_window": 20, "max_window": 60},
}

DEFAULT_FEATURES = ["close", "open", "high", "low", "volume"]


# ----------------------------------------------------------------------
# 基因树
# ----------------------------------------------------------------------

@dataclass
class Node:
    """基因树节点

    - 内部节点：op + children（list[Node]）
    - 叶子节点：feature 名 或 常量
    """
    op: str | None = None  # None = leaf
    children: list["Node"] = field(default_factory=list)
    feature: str | None = None  # leaf: feature 名
    const: float | None = None  # leaf: 常量值
    window: int | None = None  # 时序算子的 window 参数

    def is_leaf(self) -> bool:
        return self.op is None

    def __repr__(self) -> str:
        return self.to_formula()

    def to_formula(self) -> str:
        """序列化为可读公式字符串"""
        if self.is_leaf():
            if self.feature is not None:
                return self.feature
            return f"{self.const:.4g}"
        # 内部节点
        args = [c.to_formula() for c in self.children]
        if self.window is not None:
            args.append(f"w={self.window}")
        return f"{self.op}({', '.join(args)})"

    def evaluate(self, df: pd.DataFrame) -> pd.Series:
        """递归求值：返回 Series

        异常时返回 NaN Series（让适应度函数过滤）
        """
        if self.is_leaf():
            if self.feature is not None:
                if self.feature not in df.columns:
                    return pd.Series(np.nan, index=df.index)
                return df[self.feature]
            return pd.Series(self.const, index=df.index, dtype=float)

        spec = UNARY_OPS.get(self.op) or BINARY_OPS.get(self.op)
        if spec is None:
            return pd.Series(np.nan, index=df.index)

        try:
            child_vals = [c.evaluate(df) for c in self.children]
            fn = spec["fn"]
            if self.op in UNARY_OPS:
                # 一元算子：带 window 参数
                w = self.window or spec.get("default_window")
                if w is not None:
                    return fn(child_vals[0], int(w))
                return fn(child_vals[0])
            else:
                # 二元算子
                if self.op in ("ts_corr", "ts_cov"):
                    w = self.window or spec.get("default_window", 20)
                    return fn(child_vals[0], child_vals[1], int(w))
                return fn(child_vals[0], child_vals[1])
        except Exception:
            return pd.Series(np.nan, index=df.index)

    def size(self) -> int:
        """节点总数（含叶子）"""
        if self.is_leaf():
            return 1
        return 1 + sum(c.size() for c in self.children)

    def depth(self) -> int:
        if self.is_leaf():
            return 0
        return 1 + max(c.depth() for c in self.children)

    def clone(self) -> "Node":
        return Node(
            op=self.op,
            children=[c.clone() for c in self.children],
            feature=self.feature,
            const=self.const,
            window=self.window,
        )


# ----------------------------------------------------------------------
# GP 主类
# ----------------------------------------------------------------------

class GPFactorMiner:
    """遗传规划因子挖掘器

    用法：
        miner = GPFactorMiner(population_size=50, generations=20)
        result = miner.mine(df, fwd, feature_cols=["close", "volume"])
    """

    def __init__(
        self,
        population_size: int = 50,
        generations: int = 20,
        tournament_size: int = 5,
        p_crossover: float = 0.7,
        p_mutate: float = 0.15,
        max_depth: int = 4,
        const_range: tuple[float, float] = (-5.0, 5.0),
        p_const_leaf: float = 0.2,
        elitism: int = 2,
        random_state: int = 42,
    ):
        """
        Args:
            population_size: 种群大小
            generations: 进化代数
            tournament_size: 锦标赛大小（越大 → 选择压力越大）
            p_crossover: 交叉概率
            p_mutate: 变异概率
            max_depth: 基因树最大深度（防膨胀）
            const_range: 常量叶子取值范围
            p_const_leaf: 叶子是常量（而非 feature）的概率
            elitism: 每代直接保留的最优个体数
            random_state: 随机种子
        """
        self.population_size = population_size
        self.generations = generations
        self.tournament_size = tournament_size
        self.p_crossover = p_crossover
        self.p_mutate = p_mutate
        self.max_depth = max_depth
        self.const_range = const_range
        self.p_const_leaf = p_const_leaf
        self.elitism = elitism
        self.random_state = random_state

    # --- 树生成 ---

    def _random_window(self, op: str) -> int | None:
        spec = UNARY_OPS.get(op) or BINARY_OPS.get(op, {})
        max_w = spec.get("max_window")
        if max_w is None:
            return None
        # 偏向常用 window（5/10/20/60）
        choices = [w for w in [5, 10, 20, 60] if w <= max_w]
        return random.choice(choices) if choices else max_w

    def _random_leaf(self, features: list[str]) -> Node:
        if random.random() < self.p_const_leaf:
            const = random.uniform(*self.const_range)
            # 避免零常量（容易让 mul/div 退化）
            if abs(const) < 0.1:
                const = 0.1 if const >= 0 else -0.1
            return Node(const=const)
        return Node(feature=random.choice(features))

    def _random_tree(
        self,
        features: list[str],
        depth: int | None = None,
    ) -> Node:
        """生成随机基因树（半半策略：grow + full 混合）"""
        if depth is None:
            depth = random.randint(2, self.max_depth)

        if depth <= 0:
            return self._random_leaf(features)

        # 60% 概率选二元，30% 选一元，10% 直接变叶子
        roll = random.random()
        if roll < 0.10:
            return self._random_leaf(features)
        elif roll < 0.40:
            op = random.choice(list(UNARY_OPS.keys()))
            child = self._random_tree(features, depth=depth - 1)
            return Node(op=op, children=[child], window=self._random_window(op))
        else:
            op = random.choice(list(BINARY_OPS.keys()))
            left = self._random_tree(features, depth=depth - 1)
            right = self._random_tree(features, depth=depth - 1)
            return Node(op=op, children=[left, right], window=self._random_window(op))

    # --- 选择 / 交叉 / 变异 ---

    def _tournament(
        self,
        population: list[Node],
        fitness: list[float],
    ) -> Node:
        """锦标赛选择：随机抽 k 个，返回适应度最高的"""
        idxs = random.sample(range(len(population)), min(self.tournament_size, len(population)))
        best_idx = max(idxs, key=lambda i: fitness[i])
        return population[best_idx].clone()

    def _get_all_nodes(self, root: Node) -> list[Node]:
        """收集所有节点（含 root）的引用路径，便于交叉/变异"""
        result = [(None, root, -1)]  # (parent, node, child_idx)
        if not root.is_leaf():
            for i, c in enumerate(root.children):
                for p, n, ci in self._get_all_nodes(c):
                    result.append((c if p is None else p, n, ci))
                result.append((root, c, i))
        return result

    def _crossover(self, a: Node, b: Node) -> tuple[Node, Node]:
        """子树交换交叉：随机选 a 的子树，用 b 的随机子树替换"""
        a_clone = a.clone()
        b_clone = b.clone()

        a_nodes = self._collect_with_parents(a_clone)
        b_nodes = self._collect_with_parents(b_clone)

        if len(a_nodes) <= 1 or len(b_nodes) <= 1:
            return a_clone, b_clone

        # 选 a 的非 root 节点替换（a_nodes[0] 是 root，parent=None，跳过）
        _, a_parent, a_idx = random.choice(a_nodes[1:])
        b_node, _, _ = random.choice(b_nodes)

        new_a = a_parent.children[a_idx].clone()
        a_parent.children[a_idx] = b_node.clone()

        # 对称操作（保留种群多样性）
        _, b_parent, b_idx = random.choice(b_nodes[1:])
        b_parent.children[b_idx] = new_a

        return a_clone, b_clone

    def _collect_with_parents(
        self,
        root: Node,
        parent: Node | None = None,
        idx: int = -1,
    ) -> list[tuple[Node, Node, int]]:
        """返回 [(node, parent, child_idx)]，root 的 parent=None, idx=-1

        递归传递 parent，避免把非根节点误记为无 parent。
        """
        result = [(root, parent, idx)]
        if not root.is_leaf():
            for i, c in enumerate(root.children):
                result.extend(self._collect_with_parents(c, parent=root, idx=i))
        return result

    def _mutate(self, node: Node, features: list[str]) -> Node:
        """变异：随机选一个子树，替换为新随机子树"""
        clone = node.clone()
        all_nodes = self._collect_with_parents(clone)
        if len(all_nodes) <= 1:
            return clone

        # 70% 概率替换内部节点为随机子树（功能变异），30% 替换叶子
        target_parent, target_idx = None, -1
        if random.random() < 0.7:
            candidates = [(n, p, i) for n, p, i in all_nodes[1:] if not n.is_leaf()]
            if not candidates:
                candidates = all_nodes[1:]
        else:
            candidates = all_nodes[1:]

        if not candidates:
            return clone

        target_node, target_parent, target_idx = random.choice(candidates)
        new_subtree = self._random_tree(features, depth=random.randint(1, 2))

        if target_parent is None:
            return new_subtree
        target_parent.children[target_idx] = new_subtree
        return clone

    # --- 适应度 ---

    def _fitness(
        self,
        node: Node,
        df: pd.DataFrame,
        fwd: pd.Series,
    ) -> tuple[float, float, int]:
        """返回 (|IC|, IC_signed, n_samples_used)

        |IC| 作为适应度（越大越好）。失败返回 0.0（合法无信号）而非负值，
        让无效个体在排序中自然沉底但不污染选择池。
        """
        try:
            factor = node.evaluate(df)
        except Exception:
            return 0.0, float("nan"), 0

        if factor.isna().all() or factor.std() == 0:
            return 0.0, float("nan"), 0

        ic = factor_ic(factor, fwd)
        if math.isnan(ic):
            return 0.0, float("nan"), 0
        return abs(ic), ic, len(factor.dropna())

    # --- 主入口 ---

    def mine(
        self,
        df: pd.DataFrame,
        forward_returns: pd.Series,
        feature_cols: list[str] | None = None,
        verbose: bool = False,
    ) -> dict:
        """运行 GP 挖掘

        Args:
            df: OHLCV DataFrame（需含 feature_cols 中列）
            forward_returns: 前瞻收益 Series
            feature_cols: 允许 GP 用的输入列（默认 close/open/high/low/volume）
            verbose: 是否打印每代进度

        Returns:
            dict:
            - "best_node": 最佳基因树（Node 对象）
            - "best_formula": str（公式字符串）
            - "best_ic": signed IC（带方向）
            - "best_abs_ic": |IC|（适应度）
            - "best_ir": IR（信息比率）
            - "best_factor": pd.Series（最佳因子值）
            - "history": list[dict] 每代最优个体（gen/best_ic/mean_ic）
            - "n_samples": 有效样本数
        """
        random.seed(self.random_state)
        np.random.seed(self.random_state)

        features = feature_cols or [c for c in DEFAULT_FEATURES if c in df.columns]
        if not features:
            return {"error": "df 中无可用特征列"}
        # 验证指定的 features 真实存在
        missing = [f for f in features if f not in df.columns]
        if missing:
            return {"error": f"features 不在 df 中: {missing}"}

        # 1. 初始化种群（拒绝 fitness=0 的无效个体，最多重试 5 次）
        population: list[Node] = []
        for _ in range(self.population_size):
            tree = self._random_tree(features)
            for _retry in range(5):
                abs_ic, _, _ = self._fitness(tree, df, forward_returns)
                if abs_ic > 0:
                    break
                tree = self._random_tree(features)
            population.append(tree)

        history: list[dict] = []
        best_ever: Node | None = None
        best_ever_ic = -1.0

        for gen in range(self.generations):
            # 2. 评估适应度
            scored = [(ind, self._fitness(ind, df, forward_returns)) for ind in population]
            scored.sort(key=lambda x: x[1][0], reverse=True)

            gen_best_node, (gen_best_abs_ic, gen_best_ic, _) = scored[0]
            mean_ic = sum(s[1][0] for s in scored) / len(scored)

            if gen_best_abs_ic > best_ever_ic:
                best_ever_ic = gen_best_abs_ic
                best_ever = gen_best_node.clone()

            history.append({
                "gen": gen + 1,
                "best_abs_ic": gen_best_abs_ic,
                "best_ic": gen_best_ic,
                "mean_abs_ic": mean_ic,
                "best_formula": gen_best_node.to_formula(),
            })
            if verbose:
                print(f"[GP] gen {gen+1}/{self.generations} "
                      f"best |IC|={gen_best_abs_ic:.4f} mean |IC|={mean_ic:.4f}")

            # 3. 精英保留
            new_pop: list[Node] = [scored[i][0].clone() for i in range(min(self.elitism, len(scored)))]

            # 4. 选择 + 交叉 + 变异
            fitness_only = [s[1][0] for s in scored]
            nodes_only = [s[0] for s in scored]

            while len(new_pop) < self.population_size:
                if random.random() < self.p_crossover:
                    a = self._tournament(nodes_only, fitness_only)
                    b = self._tournament(nodes_only, fitness_only)
                    c1, c2 = self._crossover(a, b)
                    if random.random() < self.p_mutate:
                        c1 = self._mutate(c1, features)
                    new_pop.append(c1)
                    if len(new_pop) < self.population_size:
                        new_pop.append(c2)
                else:
                    a = self._tournament(nodes_only, fitness_only)
                    if random.random() < self.p_mutate:
                        a = self._mutate(a, features)
                    new_pop.append(a)

            # 5. 控制膨胀（深度超限的重置）
            new_pop = [
                ind if ind.depth() <= self.max_depth + 1 else self._random_tree(features, depth=self.max_depth)
                for ind in new_pop
            ]
            population = new_pop

        if best_ever is None:
            return {"error": "GP 未产出有效因子"}

        # 最终评估最佳因子
        best_factor = best_ever.evaluate(df)
        best_ic = factor_ic(best_factor, forward_returns)
        best_ir = factor_ir(best_factor, forward_returns)

        return {
            "best_node": best_ever,
            "best_formula": best_ever.to_formula(),
            "best_ic": best_ic,
            "best_abs_ic": abs(best_ic) if not math.isnan(best_ic) else 0.0,
            "best_ir": best_ir,
            "best_factor": best_factor,
            "history": history,
            "n_samples": int(best_factor.dropna().notna().sum()),
            "feature_cols": features,
        }


__all__ = ["GPFactorMiner", "Node", "UNARY_OPS", "BINARY_OPS"]
