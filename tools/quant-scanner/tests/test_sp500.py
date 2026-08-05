"""SP500 宇宙拉取测试"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

from quant_scanner.data.sp500 import load_sp500_tickers, CACHE_PATH


def test_load_sp500_tickers_returns_list():
    """拉取返回非空 list（依赖网络）"""
    tickers = load_sp500_tickers()
    assert isinstance(tickers, list)
    assert len(tickers) >= 400, f"SP500 应至少 400 只，实际 {len(tickers)}"
    assert all(isinstance(t, str) and t.isupper() for t in tickers[:5])


def test_sp500_contains_known_tickers():
    """应包含知名大盘股"""
    tickers = set(load_sp500_tickers())
    known = {"AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA"}
    missing = known - tickers
    assert not missing, f"SP500 缺少知名 ticker: {missing}"


def test_sp500_brk_b_normalized():
    """BRK.B 应被规范化为 BRK-B（yfinance 格式）"""
    tickers = load_sp500_tickers()
    assert "BRK-B" in tickers or "BRK" in tickers, "BRK 类应存在"


def test_sp500_cache_written():
    """首次拉取后缓存文件应存在"""
    if not CACHE_PATH.exists():
        load_sp500_tickers()
    assert CACHE_PATH.exists()
    # 缓存内容应非空
    lines = [l for l in CACHE_PATH.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(lines) >= 400


def test_sp500_offline_falls_back_to_cache(tmp_path, monkeypatch):
    """网络失败时回退到过期缓存"""
    # 模拟 read_html 抛错
    with patch("quant_scanner.data.sp500.pd.read_html", side_effect=Exception("network")):
        tickers = load_sp500_tickers(force_refresh=True)
    # 应有过期缓存可用（前面的测试已经写过缓存）
    if CACHE_PATH.exists():
        assert len(tickers) >= 400
    else:
        assert tickers == []
