"""S&P 500 宇宙拉取（维基百科）

用于 RS Rating 的全市场基准。O'Neil 原版用 S&P 1500 / Russell 3000，
S&P 500 是合理近似（覆盖美股大盘 80%+ 市值）。

数据源：https://en.wikipedia.org/wiki/List_of_S%26P_500_companies
缓存：首次拉取后写入 `data/sp500_cache.txt`，30 天 TTL。
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from ..utils.config import PROJECT_ROOT

log = logging.getLogger(__name__)

WIKI_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
CACHE_PATH = PROJECT_ROOT / "data" / "sp500_cache.txt"
CACHE_TTL_DAYS = 30


def load_sp500_tickers(force_refresh: bool = False) -> list[str]:
    """获取 S&P 500 ticker 列表（维基百科抓取 + 30 天缓存）

    Args:
        force_refresh: True = 忽略缓存重新抓取

    Returns:
        大写 ticker 列表（约 500 个）。抓取失败且无缓存 → 返回空列表。
    """
    if not force_refresh and CACHE_PATH.exists():
        mtime = datetime.fromtimestamp(CACHE_PATH.stat().st_mtime)
        if datetime.now() - mtime < timedelta(days=CACHE_TTL_DAYS):
            tickers = [
                line.strip().upper()
                for line in CACHE_PATH.read_text(encoding="utf-8").splitlines()
                if line.strip() and not line.startswith("#")
            ]
            if tickers:
                log.debug(f"[sp500] cache hit: {len(tickers)} tickers")
                return tickers

    try:
        tables = pd.read_html(WIKI_URL, storage_options={"User-Agent": "Mozilla/5.0"})
        # 第一个表是 S&P 500 列表
        df = tables[0]
        # 维基百科列名："Symbol"（有时带后缀如 "Symbol.1"）
        symbol_col = None
        for col in df.columns:
            if str(col).startswith("Symbol"):
                symbol_col = col
                break
        if symbol_col is None:
            log.error(f"[sp500] 未找到 Symbol 列，columns={list(df.columns)}")
            return _fallback_from_cache()
        tickers = df[symbol_col].astype(str).str.strip().str.upper().tolist()
        # 清理：剔除含特殊字符的（如 BRK.B → BRK-B，yfinance 格式）
        tickers = [t.replace(".", "-") for t in tickers if t and t != "NAN"]
        # 去重保序
        seen = set()
        unique = [t for t in tickers if not (t in seen or seen.add(t))]
        # 写缓存
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text("\n".join(unique), encoding="utf-8")
        log.info(f"[sp500] 拉取成功：{len(unique)} tickers → {CACHE_PATH.name}")
        return unique
    except Exception as e:
        log.error(f"[sp500] 维基百科抓取失败: {e}")
        return _fallback_from_cache()


def _fallback_from_cache() -> list[str]:
    """网络失败时尝试用过期缓存"""
    if CACHE_PATH.exists():
        tickers = [
            line.strip().upper()
            for line in CACHE_PATH.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")
        ]
        log.warning(f"[sp500] 使用过期缓存：{len(tickers)} tickers")
        return tickers
    return []
