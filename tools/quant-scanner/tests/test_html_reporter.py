"""HTML 报告生成测试（P5 升级）"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import numpy as np

from quant_scanner.reporter.html import HTMLReporter
from quant_scanner.scanner.engine import ScanReport
from quant_scanner.signals.base import BaseSignal, SignalResult


class StubSignal(BaseSignal):
    """带各种 details 结构的 stub 信号"""
    name = "stub"

    def __init__(self, details_template: dict):
        self.details_template = details_template

    def evaluate(self, ticker, df):
        return SignalResult(
            ticker=ticker,
            signal_name=self.name,
            value=self.details_template.get("_value", 0.5),
            passed=self.details_template.get("_passed", True),
            details={k: v for k, v in self.details_template.items() if not k.startswith("_")},
            reasons=self.details_template.get("_reasons", ["stub reason"]),
        )


def _synthetic_df(n=100):
    rng = np.random.default_rng(0)
    prices = 100 + np.cumsum(rng.normal(0, 0.5, n))
    return pd.DataFrame({
        "open": prices, "high": prices * 1.005, "low": prices * 0.995,
        "close": prices, "volume": rng.integers(1_000_000, 10_000_000, size=n),
    }, index=pd.date_range(end=pd.Timestamp.now().tz_localize(None), periods=n, freq="B"))


def _build_report(signal_specs: list[dict], tickers: list[str]) -> ScanReport:
    report = ScanReport()
    signals = [StubSignal(spec) for spec in signal_specs]
    df = _synthetic_df()
    for t in tickers:
        sig_results = []
        for sig in signals:
            sr = sig.evaluate(t, df)
            sig_results.append(sr)
        report.results.append({
            "ticker": t,
            "signals": sig_results,
            "composite_score": 0.5,
            "passed": True,
        })
    return report


def test_render_basic(tmp_path: Path):
    """基础渲染：所有关键统计字段存在"""
    report = _build_report(
        [{"_value": 0.7, "_passed": True, "_reasons": ["pass"]}],
        ["A", "B"],
    )
    out = tmp_path / "report.html"
    HTMLReporter().render(report, out)
    text = out.read_text()
    assert "扫描总数" in text
    assert "通过率" in text
    assert ">A<" in text
    assert ">B<" in text


def test_canslim_letter_bar(tmp_path: Path):
    """CAN SLIM 字母条：7 字母都出现"""
    report = _build_report(
        [{"_value": 0.5, "_passed": True, "name": "can_slim", "letters": {"C": 0.9, "A": 0.6, "N": 0.3, "S": 0.8, "L": 0.5, "I": 0.2, "M": 0.7}}],
        ["NVDA"],
    )
    # 强制 signal_name
    for r in report.results:
        for sr in r["signals"]:
            sr.signal_name = "can_slim"
    out = tmp_path / "canslim.html"
    HTMLReporter().render(report, out)
    text = out.read_text()
    # 7 个字母 div
    for letter in "CANSLIM":
        assert f">{letter}<" in text
    # 强/中/弱 class 至少各出现一次
    assert "canslim-strong" in text
    assert "canslim-mid" in text
    assert "canslim-weak" in text


def test_pattern_badge_bullish(tmp_path: Path):
    """看多形态徽章（双底）"""
    report = _build_report(
        [{"_value": 0.6, "_passed": True, "pattern_type": "DOUBLE_BOTTOM",
          "neckline": 78.5, "target": 86.2, "confirmed": True,
          "_reasons": ["双底确认"]}],
        ["AAPL"],
    )
    out = tmp_path / "pattern.html"
    HTMLReporter().render(report, out)
    text = out.read_text()
    assert "DOUBLE_BOTTOM" in text
    assert "pat-bullish" in text
    assert "颈线" in text
    assert "目标" in text


def test_pattern_badge_bearish(tmp_path: Path):
    """看空形态徽章（头肩顶）"""
    report = _build_report(
        [{"_value": -0.7, "_passed": True, "pattern_type": "HEAD_SHOULDERS_TOP",
          "neckline": 122.0, "target": 110.0, "confirmed": True,
          "_reasons": ["H&S 顶"]}],
        ["TSLA"],
    )
    out = tmp_path / "bearish.html"
    HTMLReporter().render(report, out)
    text = out.read_text()
    assert "HEAD_SHOULDERS_TOP" in text
    assert "pat-bearish" in text


def test_composite_bar_visualization(tmp_path: Path):
    """综合分柱状图渲染"""
    report = _build_report(
        [{"_value": 0.5, "_passed": True}],
        ["A"],
    )
    report.results[0]["composite_score"] = 0.85
    out = tmp_path / "bar.html"
    HTMLReporter().render(report, out)
    text = out.read_text()
    assert "bar-fill" in text
    assert "bar-track" in text


def test_no_signals_no_crash(tmp_path: Path):
    """空报告不崩"""
    report = ScanReport()
    out = tmp_path / "empty.html"
    HTMLReporter().render(report, out)
    assert out.exists()
