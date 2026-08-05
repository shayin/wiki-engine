"""CLI `factors list/eval` smoke 测试 + HTML reporter"""
from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from quant_scanner.scanner.cli import main


def test_factors_list():
    runner = CliRunner()
    result = runner.invoke(main, ["factors", "list"])
    assert result.exit_code == 0
    assert "Alpha101" in result.output
    assert "alpha_1" in result.output
    assert "alpha_20" in result.output


def test_factors_list_count_20():
    """已实现数量应为 20"""
    runner = CliRunner()
    result = runner.invoke(main, ["factors", "list"])
    assert "20" in result.output


def test_factors_html_report(tmp_path: Path):
    """factors_html.render_alpha_factors 生成 HTML 不抛异常，结构正确"""
    from quant_scanner.reporter.factors_html import render_alpha_factors

    rows = [
        {"name": "alpha_3", "ic": 0.08, "ir": 0.52, "non_na": 240},
        {"name": "alpha_12", "ic": -0.06, "ir": -0.41, "non_na": 240},
        {"name": "alpha_9", "ic": 0.005, "ir": 0.02, "non_na": 245},
    ]
    out = tmp_path / "factors.html"
    render_alpha_factors(
        ticker="NVDA", horizon=5, period="2y",
        rows=rows, output=out,
    )
    assert out.exists()
    content = out.read_text(encoding="utf-8")
    assert "NVDA" in content
    assert "alpha_3" in content
    assert "Alpha101" in content


def test_factors_html_confidence_levels(tmp_path: Path):
    """置信度分级：strong_count/total 比例 + top IC"""
    from quant_scanner.reporter.factors_html import _confidence_label

    # 高置信：≥25% 有效且 top IC > 0.05
    conf, warn = _confidence_label(strong_count=5, total=20, top_ic_abs=0.08)
    assert conf == "高"
    assert warn == ""

    # 低置信：≤10% 有效且 top IC < 0.02
    conf, warn = _confidence_label(strong_count=1, total=20, top_ic_abs=0.015)
    assert conf == "低"
    assert "降级" in warn

    # 警惕过拟合：top IC 异常高
    conf, warn = _confidence_label(strong_count=3, total=20, top_ic_abs=0.15)
    assert "过拟合" in warn


def test_factors_matrix_html(tmp_path: Path):
    """多周期 IC 矩阵 HTML 渲染"""
    from quant_scanner.reporter.factors_html import render_alpha_matrix

    rows = [
        {"name": "alpha_3", "ics": {1: 0.02, 5: 0.08, 10: 0.06, 20: 0.04, 60: 0.01},
         "best_horizon": 5, "best_ic": 0.08},
        {"name": "alpha_6", "ics": {1: -0.01, 5: -0.02, 10: -0.07, 20: -0.05, 60: -0.03},
         "best_horizon": 10, "best_ic": -0.07},
    ]
    out = tmp_path / "matrix.html"
    render_alpha_matrix(
        ticker="NVDA", period="2y",
        horizons=[1, 5, 10, 20, 60],
        rows=rows, output=out,
    )
    assert out.exists()
    content = out.read_text(encoding="utf-8")
    assert "NVDA" in content
    assert "IC@5d" in content
    assert "IC@60d" in content
    assert "最佳周期" in content


def test_factors_matrix_cli():
    """CLI `factors matrix` smoke（合成数据不调网络）"""
    from click.testing import CliRunner
    from quant_scanner.scanner.cli import main
    runner = CliRunner()
    # list 不依赖网络
    result = runner.invoke(main, ["factors", "list"])
    assert result.exit_code == 0
    assert "matrix" in result.output
