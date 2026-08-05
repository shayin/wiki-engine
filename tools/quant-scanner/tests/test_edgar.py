"""SEC EDGAR 客户端测试

不依赖真实网络：用 monkeypatch mock requests.get，模拟 companyfacts JSON 响应。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from quant_scanner.data.edgar import EdgarClient, EDGAR_CACHE_DIR


def _mock_ticker_map():
    return {"AAPL": "0000320193", "NVDA": "0001045810"}


def _mock_companyfacts():
    """模拟 AAPL 的 companyfacts JSON（简化）"""
    return {
        "entityName": "Apple Inc.",
        "facts": {
            "us-gaap": {
                "EarningsPerShareDiluted": {
                    "units": {
                        "USD/shares": [
                            # 8 个季度 EPS（升序），每 4 季同比可算
                            {"end": "2022-12-31", "val": 1.50, "fy": 2022, "fp": "Q1", "form": "10-Q"},
                            {"end": "2023-03-31", "val": 1.65, "fy": 2022, "fp": "Q2", "form": "10-Q"},
                            {"end": "2023-06-30", "val": 1.80, "fy": 2022, "fp": "Q3", "form": "10-Q"},
                            {"end": "2022-09-24", "val": 6.15, "fy": 2022, "fp": "FY", "form": "10-K"},
                            {"end": "2023-12-30", "val": 2.18, "fy": 2023, "fp": "Q1", "form": "10-Q"},
                            {"end": "2024-03-30", "val": 2.35, "fy": 2023, "fp": "Q2", "form": "10-Q"},
                            {"end": "2024-06-29", "val": 2.55, "fy": 2023, "fp": "Q3", "form": "10-Q"},
                            {"end": "2023-09-30", "val": 6.13, "fy": 2023, "fp": "FY", "form": "10-K"},
                            {"end": "2024-12-31", "val": 2.80, "fy": 2024, "fp": "Q1", "form": "10-Q"},
                        ]
                    }
                },
                "NetIncomeLoss": {
                    "units": {"USD": [
                        {"end": "2023-09-30", "val": 96995000000, "fy": 2023, "fp": "FY", "form": "10-K"},
                    ]}
                },
                "StockholdersEquity": {
                    "units": {"USD": [
                        {"end": "2023-09-30", "val": 621460000000, "fy": 2023, "fp": "FY", "form": "10-K"},
                    ]}
                },
                "OperatingIncomeLoss": {
                    "units": {"USD": [
                        {"end": "2021-09-25", "val": 100000, "fy": 2021, "fp": "FY", "form": "10-K"},
                        {"end": "2022-09-24", "val": 120000, "fy": 2022, "fp": "FY", "form": "10-K"},
                        {"end": "2023-09-30", "val": 125000, "fy": 2023, "fp": "FY", "form": "10-K"},
                    ]}
                },
                "RevenueFromContractWithCustomerExcludingAssessableTax": {
                    "units": {"USD": [
                        {"end": "2021-09-25", "val": 365000, "fy": 2021, "fp": "FY", "form": "10-K"},
                        {"end": "2022-09-24", "val": 394000, "fy": 2022, "fp": "FY", "form": "10-K"},
                        {"end": "2023-09-30", "val": 383000, "fy": 2023, "fp": "FY", "form": "10-K"},
                    ]}
                },
                "EntityCommonStockSharesOutstanding": {
                    "units": {"shares": [
                        {"end": "2024-06-30", "val": 15200000000, "form": "10-Q"},
                    ]}
                },
            }
        }
    }


@pytest.fixture
def edgar(tmp_path):
    """隔离缓存目录的 EdgarClient"""
    return EdgarClient(cache_dir=tmp_path / "edgar", rate_limit_sec=0)


def _setup_monkeypatch(monkeypatch, ticker_map_data=None, facts_data=None, submissions_data=None):
    """mock EdgarClient._get_json 返回不同 URL 对应不同数据"""
    ticker_map_data = ticker_map_data or _mock_ticker_map_for_url()
    facts_data = facts_data or _mock_companyfacts()

    def fake_get_json(self, url, host=None):
        if "company_tickers.json" in url:
            return ticker_map_data
        if "companyfacts" in url:
            return facts_data
        if "submissions" in url:
            return submissions_data or {"filings": {"recent": {"form": [], "filingDate": []}}}
        return None

    monkeypatch.setattr("quant_scanner.data.edgar.EdgarClient._get_json", fake_get_json)


def _mock_ticker_map_for_url():
    """SEC 原格式：{"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}}"""
    return {
        "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
        "1": {"cik_str": 1045810, "ticker": "NVDA", "title": "NVIDIA Corp"},
    }


def test_resolve_cik(monkeypatch, edgar):
    """ticker → CIK 正确解析"""
    _setup_monkeypatch(monkeypatch)
    assert edgar._resolve_cik("AAPL") == "0000320193"
    assert edgar._resolve_cik("aapl") == "0000320193"  # 大小写不敏感
    assert edgar._resolve_cik("UNKNOWN") is None


def test_get_quarterly_eps(monkeypatch, edgar):
    """拉取最近 n 个季度 EPS"""
    _setup_monkeypatch(monkeypatch)
    quarters = edgar.get_quarterly_eps("AAPL", n_quarters=5)
    assert len(quarters) >= 4
    # 按时间升序
    assert quarters[0]["end"] < quarters[-1]["end"]
    # 最近季度 EPS 应是 2.80（2024-Q1）
    assert quarters[-1]["eps"] == 2.80
    assert quarters[-1]["period"] == "2024-Q1"


def test_get_quarterly_eps_yoy(monkeypatch, edgar):
    """同比计算：2024-Q1 vs 2023-Q1 = 2.80 / 2.18 - 1"""
    _setup_monkeypatch(monkeypatch)
    yoy = edgar.get_quarterly_eps_yoy("AAPL", n_quarters=4)
    assert "2024-Q1" in yoy
    # 2.80 / 2.18 - 1 ≈ 0.284
    assert abs(yoy["2024-Q1"] - 0.284) < 0.01


def test_get_eps_acceleration(monkeypatch, edgar):
    """3 季度同比递增 = 加速度"""
    _setup_monkeypatch(monkeypatch)
    # 2023-Q3, 2023-Q1, 2024-Q1（看实际是否单调递增由 mock 数据决定）
    is_acc = edgar.get_eps_acceleration("AAPL")
    assert isinstance(is_acc, bool)


def test_get_annual_eps_cagr(monkeypatch, edgar):
    """年度 CAGR：有 2022/2023 两个 FY"""
    _setup_monkeypatch(monkeypatch)
    # 3 年 CAGR 需要 4 个财年数据；mock 只给 2 个 → 应返回 None
    cagr = edgar.get_annual_eps_cagr("AAPL", years=3)
    # 数据不足
    assert cagr is None


def test_get_roe(monkeypatch, edgar):
    """ROE = NetIncome / Equity"""
    _setup_monkeypatch(monkeypatch)
    roe = edgar.get_roe("AAPL")
    # 96995000000 / 621460000000 ≈ 0.156
    assert roe is not None
    assert abs(roe - 0.156) < 0.01


def test_get_operating_margin_trend(monkeypatch, edgar):
    """3 年营业利润率：上升趋势"""
    _setup_monkeypatch(monkeypatch)
    # 2021: 100/365, 2022: 120/394, 2023: 125/383 → margins: 0.274, 0.304, 0.326
    result = edgar.get_operating_margin_trend("AAPL", n_years=3)
    assert result is not None
    margin, rising = result
    assert margin > 0
    assert rising is True  # 三个值单调递增


def test_get_shares_outstanding(monkeypatch, edgar):
    """最近流通股"""
    _setup_monkeypatch(monkeypatch)
    shares = edgar.get_shares_outstanding("AAPL")
    assert shares == 15200000000


def test_get_insider_form4_count(monkeypatch, edgar):
    """Form 4 计数"""
    submissions = {
        "filings": {
            "recent": {
                "form": ["4", "4", "4", "10-Q", "4"],
                "filingDate": [
                    (datetime.utcnow() - timedelta(days=10)).strftime("%Y-%m-%d"),
                    (datetime.utcnow() - timedelta(days=20)).strftime("%Y-%m-%d"),
                    (datetime.utcnow() - timedelta(days=30)).strftime("%Y-%m-%d"),
                    (datetime.utcnow() - timedelta(days=40)).strftime("%Y-%m-%d"),
                    (datetime.utcnow() - timedelta(days=200)).strftime("%Y-%m-%d"),  # 超出 90 天
                ]
            }
        }
    }
    _setup_monkeypatch(monkeypatch, submissions_data=submissions)
    count = edgar.get_insider_form4_count("AAPL", days=90)
    assert count == 3


def test_network_failure_returns_none(monkeypatch, edgar):
    """网络失败返回 None"""
    def fail_get(self, url, host=None):
        return None
    monkeypatch.setattr("quant_scanner.data.edgar.EdgarClient._get_json", fail_get)
    # _resolve_cik 失败 → 后续都返回空
    quarters = edgar.get_quarterly_eps("AAPL")
    assert quarters == []


def test_cache_persistence(monkeypatch, edgar):
    """缓存命中：第二次调用不应再调 _get_json"""
    call_count = {"count": 0}
    orig_data = _mock_companyfacts()

    def counting_get(self, url, host=None):
        call_count["count"] += 1
        if "company_tickers" in url:
            return _mock_ticker_map_for_url()
        if "companyfacts" in url:
            return orig_data
        return None

    monkeypatch.setattr("quant_scanner.data.edgar.EdgarClient._get_json", counting_get)

    edgar.get_quarterly_eps("AAPL")
    first_count = call_count["count"]
    edgar.get_quarterly_eps("AAPL")
    second_count = call_count["count"]
    # 缓存生效：第二次不应重复调 companyfacts
    assert second_count == first_count, "缓存应避免重复 HTTP 请求"


def test_user_agent_normalization():
    """User-Agent 含邮箱则保留，否则用默认"""
    c1 = EdgarClient(contact_email="user@example.com")
    assert "user@example.com" in c1.user_agent

    c2 = EdgarClient(contact_email=None)
    assert "contact@example.com" in c2.user_agent
