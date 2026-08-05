"""SEC EDGAR 客户端 — 从官方 XBRL 公司事实 API 拉取精确基本面数据

来源：SEC EDGAR `data.sec.gov` 公开 JSON API（无 key，要求 User-Agent 头，
每秒 10 次请求上限；本模块默认 1 req/sec 保守）。

覆盖 quant-scanner 的 CAN SLIM C/A 字母精确化需求：
- `get_quarterly_eps(ticker, n=8)` — GAAP Diluted EPS 季度序列
- `get_quarterly_eps_yoy(ticker, n=4)` — 季度同比
- `get_annual_eps_cagr(ticker, years=3)` — 年度 EPS CAGR
- `get_roe(ticker)`、`get_operating_margin(ticker)` + 趋势
- `get_shares_outstanding(ticker)`
- `get_insider_form4_count(ticker, days=90)` — 近 90 天 Form 4 数量（cluster buying 代理）

为什么用 GAAP 而不是 non-GAAP：
- SEC XBRL API 只提供 GAAP 概念（US-GAAP taxonomy）
- O'Neil《笑傲股市》原书使用报告 Diluted EPS（GAAP），不依赖公司自行调整的 non-GAAP
- 实务中 IBD 也基于 Compustat GAAP 数据

失败降级：网络失败 → 过期缓存 → 返回 None（让上层降级到 yfinance）。
"""
from __future__ import annotations

import json
import logging
import time
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import requests

from ..utils.config import CACHE_DIR

log = logging.getLogger(__name__)

# SEC 要求所有自动化请求带联系邮箱（无 key 鉴权，仅识别身份）
DEFAULT_USER_AGENT = "quant-scanner/1.0 (+contact@example.com)"
EDGAR_BASE = "https://data.sec.gov"
TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
EDGAR_CACHE_DIR = CACHE_DIR / "edgar"
TICKER_MAP_CACHE = EDGAR_CACHE_DIR / "_ticker_map.json"
CACHE_TTL_DAYS = 7  # 基本面缓存 7 天
RATE_LIMIT_SEC = 1.0  # 保守限速：1 req/sec（SEC 上限是 10 req/sec）

# US-GAAP XBRL 概念名（不同公司可能用不同 taxonomy 版本，按优先级匹配）
EPS_CONCEPTS = ["EarningsPerShareDiluted", "IncomeLossFromContinuingOperationsPerDilutedShare"]
NET_INCOME_CONCEPTS = ["NetIncomeLoss", "ProfitLoss"]
EQUITY_CONCEPTS = ["StockholdersEquity", "Assets", "LiabilitiesAndStockholdersEquity"]
OPERATING_INCOME_CONCEPTS = ["OperatingIncomeLoss", "IncomeLossFromContinuingOperations"]
REVENUE_CONCEPTS = [
    "RevenueFromContractWithCustomerExcludingAssessableTax",
    "Revenues",
    "SalesRevenueNet",
]
SHARES_OUT_CONCEPTS = ["EntityCommonStockSharesOutstanding"]


def _normalize_user_agent(ua: str | None) -> str:
    """SEC 要求 User-Agent 含联系方式。若无实数邮箱则用默认（带占位符）"""
    if not ua or "@" not in ua:
        return DEFAULT_USER_AGENT
    return f"quant-scanner/1.0 (+{ua})"


class EdgarClient:
    """SEC EDGAR companyfacts API 客户端

    使用：直接调 get_* 方法；缓存自动管理。
    """

    def __init__(
        self,
        contact_email: Optional[str] = None,
        cache_dir: Path | None = None,
        cache_ttl_days: int = CACHE_TTL_DAYS,
        rate_limit_sec: float = RATE_LIMIT_SEC,
    ):
        """
        Args:
            contact_email: 联系邮箱（SEC 要求）。None → 用默认占位符
            cache_dir: 缓存目录。None → 用默认 data_cache/edgar/
            cache_ttl_days: 缓存有效期
            rate_limit_sec: 请求间隔（保守 1s）
        """
        self.user_agent = _normalize_user_agent(contact_email)
        self.cache_dir = cache_dir or EDGAR_CACHE_DIR
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_ttl_days = cache_ttl_days
        self.rate_limit_sec = rate_limit_sec
        self._last_request_ts: float = 0.0
        self._ticker_map: dict[str, str] | None = None  # {TICKER: CIK}
        # ticker→CIK 缓存路径放在实例 cache_dir 下（便于测试隔离）
        self._ticker_map_cache_path: Path = self.cache_dir / "_ticker_map.json"

    # -------------------------- 底层 HTTP --------------------------

    def _headers(self) -> dict:
        return {
            "User-Agent": self.user_agent,
            "Accept-Encoding": "gzip, deflate",
            "Host": "data.sec.gov",
        }

    def _throttle(self) -> None:
        """限速：距离上次请求 ≥ rate_limit_sec"""
        elapsed = time.monotonic() - self._last_request_ts
        if elapsed < self.rate_limit_sec:
            time.sleep(self.rate_limit_sec - elapsed)
        self._last_request_ts = time.monotonic()

    def _get_json(self, url: str, host: str | None = None) -> dict | None:
        """带限速的 GET。失败返回 None"""
        self._throttle()
        headers = self._headers()
        if host:
            headers["Host"] = host
        try:
            resp = requests.get(url, headers=headers, timeout=15)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            log.warning(f"[edgar] GET {url} 失败: {e}")
            return None

    # -------------------------- ticker → CIK --------------------------

    def _load_ticker_map(self) -> dict[str, str]:
        """拉取/缓存 SEC 全 ticker→CIK 映射（~6000 个）"""
        if self._ticker_map is not None:
            return self._ticker_map

        cache_path = self._ticker_map_cache_path
        if cache_path.exists():
            mtime = datetime.utcfromtimestamp(cache_path.stat().st_mtime)
            if datetime.utcnow() - mtime < timedelta(days=30):
                self._ticker_map = json.loads(cache_path.read_text())
                return self._ticker_map

        # 拉取（host 是 www.sec.gov 不是 data.sec.gov）
        data = self._get_json(TICKER_MAP_URL, host="www.sec.gov")
        if data is None:
            if cache_path.exists():
                # 回退过期缓存
                log.warning("[edgar] ticker map 拉取失败，回退过期缓存")
                self._ticker_map = json.loads(cache_path.read_text())
                return self._ticker_map
            self._ticker_map = {}
            return self._ticker_map

        # SEC 格式：{"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}, ...}
        ticker_map = {
            v["ticker"].upper(): str(v["cik_str"]).zfill(10)
            for v in data.values()
            if "ticker" in v and "cik_str" in v
        }
        cache_path.write_text(json.dumps(ticker_map))
        self._ticker_map = ticker_map
        return ticker_map

    def _resolve_cik(self, ticker: str) -> str | None:
        """ticker → CIK（10 位 0 填充）"""
        m = self._load_ticker_map()
        return m.get(ticker.upper())

    # -------------------------- companyfacts 缓存 --------------------------

    def _facts_cache_path(self, cik: str) -> Path:
        return self.cache_dir / f"{cik}.json"

    def _load_company_facts(self, ticker: str, use_cache: bool = True) -> dict | None:
        """拉 companyfacts/CIK{cik}.json（含全部历史 XBRL 数据）

        缓存：cache_ttl_days 天有效；网络失败回退过期缓存。
        """
        cik = self._resolve_cik(ticker)
        if not cik:
            log.debug(f"[edgar] 无法解析 CIK: {ticker}")
            return None

        cache_path = self._facts_cache_path(cik)
        today = datetime.utcnow()
        if use_cache and cache_path.exists():
            mtime = datetime.utcfromtimestamp(cache_path.stat().st_mtime)
            if (today - mtime).days < self.cache_ttl_days:
                try:
                    return json.loads(cache_path.read_text())
                except Exception:
                    pass  # 缓存损坏 → 重新拉

        url = f"{EDGAR_BASE}/api/xbrl/companyfacts/CIK{cik}.json"
        data = self._get_json(url)
        if data is None:
            if cache_path.exists():
                log.warning(f"[edgar] {ticker} 拉取失败，回退过期缓存")
                try:
                    return json.loads(cache_path.read_text())
                except Exception:
                    return None
            return None

        try:
            cache_path.write_text(json.dumps(data))
        except Exception as e:
            log.warning(f"[edgar] {ticker} 缓存写入失败: {e}")
        return data

    def _extract_series(
        self,
        facts: dict,
        concepts: list[str],
        unit: str = "USD",
    ) -> list[dict]:
        """从 facts['us-gaap'][concept]['units'][unit] 提取序列

        自动尝试 concepts 列表，返回第一个有效的全部记录列表。
        每条记录字段：{'start', 'end', 'val', 'fy', 'fp', 'form', ...}
        """
        us_gaap = facts.get("facts", {}).get("us-gaap", {})
        for concept in concepts:
            node = us_gaap.get(concept)
            if not node:
                continue
            units = node.get("units", {})
            # 优先 USD/shares；有些概念只有 shares 单位
            for u in [unit, "shares", "USD/shares"]:
                if u in units and units[u]:
                    return units[u]
        return []

    # -------------------------- 公开 API --------------------------

    def get_quarterly_eps(self, ticker: str, n_quarters: int = 8) -> list[dict]:
        """最近 n 个季度的 GAAP Diluted EPS

        Returns:
            [{"period": "2024-Q3", "end": "2024-09-30", "eps": 1.46, "form": "10-Q"}, ...]
            按时间升序
        """
        facts = self._load_company_facts(ticker)
        if not facts:
            return []
        raw = self._extract_series(facts, EPS_CONCEPTS, unit="USD/shares")
        if not raw:
            # 某些公司用 USD 单位
            raw = self._extract_series(facts, EPS_CONCEPTS, unit="USD")
        if not raw:
            return []

        # 过滤：只保留 10-Q/10-K 的 fp=Q1/Q2/Q3/Q4 季度数据，按 end 去重
        seen_periods: dict[str, dict] = {}
        for r in raw:
            form = r.get("form", "")
            fp = r.get("fp", "")
            if form not in ("10-Q", "10-K") or fp not in ("Q1", "Q2", "Q3", "FY"):
                continue
            end = r.get("end")
            if not end or r.get("val") is None:
                continue
            # 同一 end 多次报告（修订）→ 取 frame 最新（值大或 form 优先 10-K）
            key = f"{r.get('fy')}-{fp}"
            prev = seen_periods.get(key)
            if prev is None or end > prev["end"]:
                seen_periods[key] = {
                    "period": key,
                    "end": end,
                    "eps": float(r["val"]),
                    "form": form,
                    "fy": r.get("fy"),
                    "fp": fp,
                }
        # 按 end 升序
        items = sorted(seen_periods.values(), key=lambda x: x["end"])
        return items[-n_quarters:]

    def get_quarterly_eps_yoy(self, ticker: str, n_quarters: int = 4) -> dict[str, float]:
        """最近 n 个季度的 EPS 同比增速

        Returns:
            {"2024-Q3": 0.155, "2024-Q2": 0.082, ...}  按 period 升序
            若同期去年数据不可得则跳过该季度
        """
        quarters = self.get_quarterly_eps(ticker, n_quarters=n_quarters + 4)
        if len(quarters) < 5:
            return {}
        by_period = {q["period"]: q for q in quarters}
        yoy: dict[str, float] = {}
        # 仅对最后 n 个季度算同比
        for q in quarters[-n_quarters:]:
            fy, fp = q["period"].split("-")
            try:
                prev_fy = int(fy) - 1
            except ValueError:
                continue
            prev_key = f"{prev_fy}-{fp}"
            prev = by_period.get(prev_key)
            if not prev or prev["eps"] <= 0 or q["eps"] <= 0:
                continue
            yoy[q["period"]] = (q["eps"] - prev["eps"]) / abs(prev["eps"])
        return yoy

    def get_eps_acceleration(self, ticker: str) -> bool:
        """最近 3 个季度 EPS 同比是否单调递增（加速度）"""
        yoy = self.get_quarterly_eps_yoy(ticker, n_quarters=4)
        vals = list(yoy.values())[-3:]
        if len(vals) < 3:
            return False
        return vals[0] < vals[1] < vals[2]

    def get_annual_eps(self, ticker: str, n_years: int = 5) -> list[dict]:
        """最近 n 个财年的年度 EPS

        Returns:
            [{"fy": 2023, "end": "2023-09-30", "eps": 5.95, "form": "10-K"}, ...] 升序
        """
        facts = self._load_company_facts(ticker)
        if not facts:
            return []
        raw = self._extract_series(facts, EPS_CONCEPTS, unit="USD/shares")
        if not raw:
            raw = self._extract_series(facts, EPS_CONCEPTS, unit="USD")

        annual: dict[int, dict] = {}
        for r in raw:
            if r.get("form") != "10-K" or r.get("fp") != "FY":
                continue
            fy = r.get("fy")
            end = r.get("end")
            val = r.get("val")
            if fy is None or not end or val is None:
                continue
            # 同 fy 多份报告 → 取最新 end
            prev = annual.get(fy)
            if prev is None or end > prev["end"]:
                annual[fy] = {
                    "fy": fy,
                    "end": end,
                    "eps": float(val),
                    "form": "10-K",
                }
        items = sorted(annual.values(), key=lambda x: x["fy"])
        return items[-n_years:]

    def get_annual_eps_cagr(self, ticker: str, years: int = 3) -> float | None:
        """n 年 EPS CAGR（年化复合增长率）。数据不足返回 None"""
        annuals = self.get_annual_eps(ticker, n_years=years + 1)
        if len(annuals) < years + 1:
            return None
        start = annuals[-(years + 1)]["eps"]
        end = annuals[-1]["eps"]
        if start <= 0 or end <= 0:
            return None
        return (end / start) ** (1.0 / years) - 1.0

    def get_roe(self, ticker: str) -> float | None:
        """最近年度 ROE = NetIncome / AvgStockholdersEquity

        Returns:
            0.0-1.0 比例，或 None（数据缺失）
        """
        facts = self._load_company_facts(ticker)
        if not facts:
            return None
        ni_series = self._extract_series(facts, NET_INCOME_CONCEPTS, unit="USD")
        eq_series = self._extract_series(facts, EQUITY_CONCEPTS, unit="USD")
        if not ni_series or not eq_series:
            return None

        # 取最近一年的 10-K net income
        ni_annual = [r for r in ni_series if r.get("form") == "10-K" and r.get("fp") == "FY" and r.get("val") is not None]
        if not ni_annual:
            return None
        ni_annual.sort(key=lambda r: r.get("end", ""), reverse=True)
        ni_recent = ni_annual[0]
        end_date = ni_recent["end"]
        fy = ni_recent.get("fy")

        # 取该财年年末 equity（按 fy 匹配）
        fy_eq = [r for r in eq_series if r.get("fy") == fy and r.get("val") is not None and r.get("form") == "10-K"]
        if not fy_eq:
            return None
        # 同一财年多份（季度 10-Q 也报告），取 end 最接近 end_date 的
        fy_eq.sort(key=lambda r: abs(0 if r.get("end", "") < end_date else 1))
        equity = fy_eq[0]["val"]
        if equity <= 0:
            return None
        return ni_recent["val"] / equity

    def get_operating_margin_trend(self, ticker: str, n_years: int = 3) -> tuple[float, bool] | None:
        """最近年度营业利润率 + n 年趋势是否上升

        Returns:
            (latest_margin, is_rising) 或 None。
            margin 是 0-1 比例。
        """
        facts = self._load_company_facts(ticker)
        if not facts:
            return None
        oi_series = self._extract_series(facts, OPERATING_INCOME_CONCEPTS, unit="USD")
        rev_series = self._extract_series(facts, REVENUE_CONCEPTS, unit="USD")
        if not oi_series or not rev_series:
            return None

        # 按 fy 聚合年度值（同 fy 多份时取 end 最大）
        def annual_map(series):
            buckets: dict[int, list[tuple[str, float]]] = {}
            for r in series:
                if r.get("form") != "10-K" or r.get("fp") != "FY":
                    continue
                fy = r.get("fy")
                val = r.get("val")
                if fy is None or val is None:
                    continue
                buckets.setdefault(fy, []).append((r.get("end", ""), float(val)))
            return {fy: max(items, key=lambda x: x[0])[1] for fy, items in buckets.items()}

        oi_map = annual_map(oi_series)
        rev_map = annual_map(rev_series)
        common_fys = sorted(set(oi_map) & set(rev_map))[-n_years:]
        if len(common_fys) < 2:
            return None
        margins = [oi_map[fy] / rev_map[fy] if rev_map[fy] != 0 else 0 for fy in common_fys]
        latest = margins[-1]
        is_rising = all(margins[i] <= margins[i + 1] for i in range(len(margins) - 1))
        return latest, is_rising

    def get_shares_outstanding(self, ticker: str) -> int | None:
        """最近一期流通股（股数）"""
        facts = self._load_company_facts(ticker)
        if not facts:
            return None
        raw = self._extract_series(facts, SHARES_OUT_CONCEPTS, unit="shares")
        if not raw:
            return None
        valid = [r for r in raw if r.get("val") is not None and r.get("end")]
        if not valid:
            return None
        valid.sort(key=lambda r: r.get("end", ""), reverse=True)
        return int(valid[0]["val"])

    def get_insider_form4_count(self, ticker: str, days: int = 90) -> int:
        """近 N 天 Form 4 提交数量（cluster buying 代理）

        注：SEC submissions API 列出近期 filings，按 type=4 过滤计数。
        本方法不做买入/卖出方向区分（需解析 XML），仅作粗略 cluster 活跃度。
        """
        cik = self._resolve_cik(ticker)
        if not cik:
            return 0
        url = f"{EDGAR_BASE}/submissions/CIK{cik}.json"
        # submissions 用 data.sec.gov（host 已默认）
        data = self._get_json(url)
        if not data:
            return 0
        recent = data.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        dates = recent.get("filingDate", [])
        cutoff = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
        count = 0
        for i, form in enumerate(forms):
            if form == "4":
                d = dates[i] if i < len(dates) else ""
                if d >= cutoff:
                    count += 1
        return count
