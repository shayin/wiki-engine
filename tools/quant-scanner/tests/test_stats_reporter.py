"""StatsReporter 测试"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from quant_scanner.stats.analyzer import SliceStats, HoldoutSplit
from quant_scanner.stats.reporter import StatsReporter, DiagnosticRow


def _make_split(holdout_start=None, train_sat=True, holdout_sat=True,
                train_n=5, holdout_n=2) -> HoldoutSplit:
    return HoldoutSplit(
        train_events=[object() for _ in range(train_n)],
        holdout_events=[object() for _ in range(holdout_n)],
        holdout_start=holdout_start,
        train_satisfied=train_sat,
        holdout_satisfied=holdout_sat,
    )


def _make_stats(n=20, ev=0.05) -> SliceStats:
    return SliceStats(
        signal_name="major_reversal", event_type="DOUBLE_BOTTOM",
        direction="long", n=n, n_censored=2,
        win_rate=0.55, win_rate_low=0.35, win_rate_high=0.73,
        ev=ev, median_return=0.03, avg_win=0.08, avg_loss=-0.04,
        profit_factor=2.0, avg_mae=-0.02, avg_mfe=0.05,
        confidence_tier="default" if n >= 200 else "low" if n >= 50 else "exploratory" if n >= 20 else "grey",
    )


def test_render_basic(tmp_path: Path):
    reporter = StatsReporter()
    out = tmp_path / "stats.html"
    reporter.render(
        top_level=[_make_stats(20)],
        second_level=[_make_stats(20)],
        diagnostic=[DiagnosticRow("major_reversal", "DOUBLE_TOP", "short", 5)],
        split=_make_split(pd.Timestamp("2024-12-01")),
        total_events=27,
        total_censored=2,
        horizon=20,
        universe_size=10,
        output=out,
    )
    assert out.exists()
    text = out.read_text()
    assert "形态胜率统计" in text
    assert "T+1 开盘价" in text
    assert "仅统计做多" in text
    assert "Wilson" in text
    assert "DOUBLE_BOTTOM" in text
    assert "双底" in text  # 中文翻译
    assert "DOUBLE_TOP" in text  # 诊断桶
    assert "双顶" in text  # 诊断桶中文
    assert "major_reversal" in text
    assert "重大反转形态" in text  # signal 中文化
    assert "术语表" in text  # 新增术语卡片
    assert "核心结论" in text  # 新增自动结论


def test_render_holdout_off(tmp_path: Path):
    """holdout_months=0 场景：holdout_start=None"""
    reporter = StatsReporter()
    out = tmp_path / "no_holdout.html"
    reporter.render(
        top_level=[],
        second_level=[],
        diagnostic=[],
        split=_make_split(holdout_start=None, holdout_n=0),
        total_events=0,
        total_censored=0,
        horizon=20,
        universe_size=5,
        output=out,
    )
    text = out.read_text()
    assert "全样本" in text or "holdout 关闭" in text


def test_render_low_confidence_greyed(tmp_path: Path):
    """低样本行应有 tier-grey class"""
    reporter = StatsReporter()
    out = tmp_path / "grey.html"
    stats = _make_stats(n=10)  # grey
    assert stats.confidence_tier == "grey"
    reporter.render(
        top_level=[stats],
        second_level=[],
        diagnostic=[],
        split=_make_split(),
        total_events=10,
        total_censored=0,
        horizon=20,
        universe_size=2,
        output=out,
    )
    text = out.read_text()
    assert "tier-grey" in text


def test_render_infinite_profit_factor(tmp_path: Path):
    """profit_factor=inf 时显示 ∞"""
    reporter = StatsReporter()
    stats = _make_stats()
    stats.profit_factor = float("inf")
    out = tmp_path / "pf.html"
    reporter.render(
        top_level=[stats], second_level=[], diagnostic=[],
        split=_make_split(),
        total_events=20, total_censored=0,
        horizon=20, universe_size=2,
        output=out,
    )
    text = out.read_text()
    assert "∞" in text


def test_render_empty_tables_no_crash(tmp_path: Path):
    """空表不崩"""
    reporter = StatsReporter()
    out = tmp_path / "empty.html"
    reporter.render(
        top_level=[], second_level=[], diagnostic=[],
        split=_make_split(holdout_start=None, holdout_n=0),
        total_events=0, total_censored=0,
        horizon=20, universe_size=0,
        output=out,
    )
    assert out.exists()


def test_render_with_validated_holdout_table(tmp_path: Path):
    """holdout_table.is_validated=True → 报告包含 holdout 段表格"""
    from quant_scanner.stats.reporter import HoldoutTable
    reporter = StatsReporter()
    out = tmp_path / "with_holdout.html"
    h_stats = _make_stats(20)
    reporter.render(
        top_level=[_make_stats(50)],
        second_level=[_make_stats(20)],
        diagnostic=[],
        split=_make_split(pd.Timestamp("2024-12-01")),
        total_events=100, total_censored=0,
        horizon=20, universe_size=5,
        output=out,
        holdout_table=HoldoutTable(
            top_level=[h_stats],
            second_level=[h_stats],
            is_validated=True,
        ),
    )
    text = out.read_text()
    assert "holdout 段独立验证" in text
    assert "holdout 顶层信号总表" in text


def test_render_with_unvalidated_holdout_no_table(tmp_path: Path):
    """holdout_table.is_validated=False → 显示样本不足提示而非表格"""
    from quant_scanner.stats.reporter import HoldoutTable
    reporter = StatsReporter()
    out = tmp_path / "no_holdout.html"
    reporter.render(
        top_level=[_make_stats(50)],
        second_level=[],
        diagnostic=[],
        split=_make_split(pd.Timestamp("2024-12-01")),
        total_events=50, total_censored=0,
        horizon=20, universe_size=5,
        output=out,
        holdout_table=HoldoutTable(
            top_level=[], second_level=[], is_validated=False,
        ),
    )
    text = out.read_text()
    assert "样本不足" in text
    assert "holdout 段验证" not in text
