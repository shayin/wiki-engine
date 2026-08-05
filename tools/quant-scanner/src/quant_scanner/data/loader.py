"""数据加载层

多源 K 线加载（yahoo v8 chart HTTP 主力 → yfinance 兜底 → 东财兜底），带本地 parquet 缓存。
- 同一天内同一 ticker 只拉一次
- 历史数据按 ticker 存为 parquet，便于快速重读
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable, Optional

import pandas as pd

from quant_scanner.utils.config import get_cache_dir

log = logging.getLogger(__name__)


def _ticker_cache_path(ticker: str) -> Path:
    return get_cache_dir() / f"{ticker.upper()}.parquet"


def _meta_cache_path(ticker: str) -> Path:
    return get_cache_dir() / f"{ticker.upper()}.meta.parquet"


class DataLoader:
    """多源 K 线加载器（yahoo chart 主力 + yfinance/东财兜底），带本地缓存"""

    def __init__(self, cache_days: int = 1):
        """
        Args:
            cache_days: 缓存有效期（天）。默认 1 = 当天内复用
        """
        self.cache_days = cache_days
        self._yf = None  # lazy import
        self._edgar = None  # lazy import

    @property
    def yf(self):
        if self._yf is None:
            import yfinance as yf
            self._yf = yf
        return self._yf

    @property
    def edgar(self):
        """SEC EDGAR 客户端（懒加载）"""
        if self._edgar is None:
            from .edgar import EdgarClient
            self._edgar = EdgarClient()
        return self._edgar

    # ---------- 行情数据 ----------

    def load(
        self,
        ticker: str,
        period: str = "2y",
        interval: str = "1d",
        use_cache: bool = True,
        start: str | None = None,
        end: str | None = None,
        cache_key: str | None = None,
    ) -> pd.DataFrame:
        """加载日线数据

        Args:
            ticker: 股票代码
            period: yfinance period（1y/2y/5y/max）
            interval: yfinance interval（1d/1wk/1h）
            use_cache: 是否使用本地缓存
            start: 起始日期 YYYY-MM-DD（与 period 二选一）
            end: 结束日期 YYYY-MM-DD
            cache_key: 自定义缓存 key（用于历史回测避免覆盖默认缓存）
        """
        cache_path = _ticker_cache_path(ticker) if cache_key is None else (get_cache_dir() / f"{cache_key}.parquet")
        today = datetime.utcnow().date()

        if use_cache and cache_path.exists():
            mtime = datetime.utcfromtimestamp(cache_path.stat().st_mtime).date()
            cache_ttl = self.cache_days if cache_key is None else 365
            if (today - mtime).days < cache_ttl:
                df = pd.read_parquet(cache_path)
                log.debug(f"[cache hit] {ticker} {len(df)} rows")
                return df

        # 多源容错：yahoo_chart（HTTP 直连，主力）→ yfinance（crumb 处理兜底）→ 东财（日线兜底）
        df = self._fetch_from_yahoo_chart(
            ticker, period=period, interval=interval, start=start, end=end
        )
        if df is None or df.empty:
            df = self._fetch_from_yfinance(
                ticker, period=period, interval=interval, start=start, end=end
            )
        if (df is None or df.empty) and interval == "1d":
            # 前两源都失败 → 东财 fallback（仅日线）
            df = self._fetch_from_eastmoney(ticker, period=period)
            if df is not None and not df.empty:
                log.info(f"[eastmoney fallback] {ticker} {len(df)} rows")
        if df is None or df.empty:
            # 所有在线源失败 → 过期缓存兜底（stale 优于无数据，避免 yahoo 限速时空手而归）
            if cache_path.exists():
                stale = pd.read_parquet(cache_path)
                if not stale.empty:
                    log.warning(
                        f"[stale cache] {ticker} 在线源全失败，用过期缓存 "
                        f"({len(stale)} rows, mtime="
                        f"{datetime.utcfromtimestamp(cache_path.stat().st_mtime).date()})"
                    )
                    return stale
            log.warning(f"[no data] {ticker}")
            return pd.DataFrame()

        df.to_parquet(cache_path)
        log.info(f"[fetched] {ticker} {len(df)} rows → {cache_path.name}")
        return df

    def _fetch_from_yahoo_chart(
        self,
        ticker: str,
        period: str = "2y",
        interval: str = "1d",
        start: str | None = None,
        end: str | None = None,
        max_retries: int = 3,
    ) -> Optional[pd.DataFrame]:
        """Yahoo v8 chart API（纯 HTTP，无需 cookie/crumb/无头浏览器）。主力源。

        带指数退避重试（2/4/8s）应对 yahoo 临时限速（实测：高频请求触发 429/空 result，
        冷却 45s 后恢复）。失败返回 None，让上层走 yfinance/过期缓存兜底。
        interval/period 与 yfinance 格式一致（1d/1wk/1h，2y/6mo/max），yahoo v8 原生兼容；
        start/end（YYYY-MM-DD）转 period1/period2 unix 时间戳。
        """
        import requests
        import time

        if start and end:
            period1 = int(datetime.strptime(start, "%Y-%m-%d").timestamp())
            period2 = int(datetime.strptime(end, "%Y-%m-%d").timestamp())
            url = (
                f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
                f"?period1={period1}&period2={period2}&interval={interval}"
            )
        else:
            url = (
                f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
                f"?interval={interval}&range={period}"
            )
        last_err = "unknown"
        for attempt in range(max_retries):
            try:
                r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
                if r.status_code == 200:
                    result = (r.json().get("chart") or {}).get("result")
                    if result and result[0].get("timestamp"):
                        res = result[0]
                        ts = res["timestamp"]
                        q = (res.get("indicators") or {}).get("quote", [{}])[0]
                        # yahoo timestamp 是 unix 秒（UTC），转纽约时区对齐美股交易日再去 tz
                        idx = (
                            pd.to_datetime(ts, unit="s", utc=True)
                            .tz_convert("America/New_York")
                            .tz_localize(None)
                        )
                        df = pd.DataFrame(
                            {
                                "open": q.get("open"),
                                "high": q.get("high"),
                                "low": q.get("low"),
                                "close": q.get("close"),
                                "volume": q.get("volume"),
                            },
                            index=idx,
                        )
                        df.index.name = "Date"
                        df = df.dropna(how="all")
                        if not df.empty:
                            return df
                        last_err = "empty after dropna"
                    else:
                        err = (r.json().get("chart") or {}).get("error")
                        last_err = f"no result ({err})"
                else:
                    last_err = f"http {r.status_code}"
                # 未拿到有效数据 → 退避重试（限速 / 瞬时故障）
                if attempt < max_retries - 1:
                    wait = 2 ** (attempt + 1)
                    log.debug(
                        f"[yahoo_chart retry] {ticker} {last_err}, 等 {wait}s "
                        f"(attempt {attempt + 1}/{max_retries})"
                    )
                    time.sleep(wait)
            except Exception as e:
                last_err = f"{type(e).__name__}: {e}"
                if attempt < max_retries - 1:
                    time.sleep(2 ** (attempt + 1))
        log.warning(f"[yahoo_chart failed] {ticker} after {max_retries} retries: {last_err}")
        return None

    def _fetch_from_yfinance(
        self,
        ticker: str,
        period: str,
        interval: str,
        start: str | None = None,
        end: str | None = None,
    ) -> Optional[pd.DataFrame]:
        try:
            kwargs = dict(
                interval=interval,
                auto_adjust=False,
                progress=False,
                threads=False,
            )
            if start and end:
                kwargs["start"] = start
                kwargs["end"] = end
            else:
                kwargs["period"] = period
            df = self.yf.download(ticker, **kwargs)
            if df is None or df.empty:
                return None
            # yfinance 返回 MultiIndex 列名（field, ticker），扁平化
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            # 标准化列名
            df = df.rename(columns=str.lower)
            df.index = pd.to_datetime(df.index).tz_localize(None)
            return df
        except Exception as e:
            log.error(f"[yfinance error] {ticker}: {e}")
            return None

    def _fetch_from_eastmoney(
        self,
        ticker: str,
        period: str = "2y",
    ) -> Optional[pd.DataFrame]:
        """东方财富美股 K 线（yfinance 失败时的 fallback）。

        纯 HTTP 请求，反爬弱、免费、含成交量。secid=105.{ticker}（105=美股）。
        返回与 yfinance 同格式 OHLCV DataFrame（小写列名，Date 索引）。
        """
        try:
            import requests
            end = datetime.utcnow().strftime("%Y%m%d")
            years = int(period.replace("y", "")) if "y" in period else 2
            beg = (datetime.utcnow() - timedelta(days=365 * years)).strftime("%Y%m%d")
            url = "http://push2his.eastmoney.com/api/qt/stock/kline/get"
            base_params = {
                "klt": 101,  # 日线
                "fqt": 0,  # 不复权
                "beg": beg, "end": end,
                "fields1": "f1,f2,f3,f4,f5,f6",
                "fields2": "f51,f52,f53,f54,f55,f56,f57",
            }
            headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"}
            import time
            # 美股 secid 前缀不固定（105/106），两个都试；东财偶发 502，重试 1 次
            klines = []
            for attempt in range(2):
                for prefix in (105, 106):
                    params = {**base_params, "secid": f"{prefix}.{ticker}"}
                    try:
                        r = requests.get(url, params=params, timeout=15, headers=headers)
                        if r.status_code != 200:
                            continue
                        data = r.json()
                        klines = (data.get("data") or {}).get("klines", [])
                        if klines:
                            break
                    except Exception:
                        continue
                if klines:
                    break
                if attempt == 0:
                    time.sleep(2)  # 502 退避重试
            if not klines:
                return None
            # f51=日期 f52=开 f53=收 f54=高 f55=低 f56=量 f57=额
            rows = []
            dates = []
            for line in klines:
                parts = line.split(",")
                dates.append(parts[0])
                rows.append({
                    "open": float(parts[1]),
                    "close": float(parts[2]),
                    "high": float(parts[3]),
                    "low": float(parts[4]),
                    "volume": int(float(parts[5])),
                })
            df = pd.DataFrame(rows, index=pd.to_datetime(dates))
            df.index.name = "Date"
            df = df[["open", "high", "low", "close", "volume"]]
            return df
        except Exception as e:
            log.error(f"[eastmoney error] {ticker}: {e}")
            return None

    # ---------- 基本面数据 ----------

    def load_fundamentals(self, ticker: str, use_cache: bool = True) -> dict:
        """加载基本面（EPS、机构持仓等）

        Returns:
            dict 包含：
              - current_eps: 最近季度 EPS
              - annual_eps_growth: 年度 EPS 同比增长
              - institutional_pct: 机构持仓占比
              - market_cap
              - sector
              - shares_outstanding
        """
        cache_path = _meta_cache_path(ticker)
        today = datetime.utcnow().date()

        if use_cache and cache_path.exists():
            mtime = datetime.utcfromtimestamp(cache_path.stat().st_mtime).date()
            if (today - mtime).days < 7:  # 基本面缓存 7 天
                return pd.read_parquet(cache_path).to_dict("records")[0]

        data = self._fetch_fundamentals(ticker)
        if data:
            pd.DataFrame([data]).to_parquet(cache_path)
        return data

    def _fetch_fundamentals(self, ticker: str) -> dict:
        try:
            info = self.yf.Ticker(ticker).info
            return {
                "current_eps": info.get("trailingEps"),
                "forward_eps": info.get("forwardEps"),
                "quarterly_eps_growth": info.get("earningsQuarterlyGrowth"),
                "market_cap": info.get("marketCap"),
                "sector": info.get("sector"),
                "industry": info.get("industry"),
                "shares_outstanding": info.get("sharesOutstanding"),
                "institutional_pct": info.get("heldPercentInstitutions"),
                "beta": info.get("beta"),
            }
        except Exception as e:
            log.error(f"[fundamentals error] {ticker}: {e}")
            return {}

    def load_eps_history(self, ticker: str, use_cache: bool = True) -> dict:
        """加载 EPS 历史用于 CAN SLIM C/A 字母精确计算

        Returns:
            dict 包含：
              - quarterly_eps: 最近 8 个季度 EPS（Diluted EPS）pandas Series
              - annual_eps: 最近 4-5 年年度 EPS pandas Series
              - quarterly_yoy: 最近 4 季度的同比增速
              - quarterly_accelerating: 是否连续加速（bool）
              - annual_3y_cagr: 3 年年化增长率
        """
        cache_path = get_cache_dir() / f"{ticker.upper()}.eps.parquet"
        today = datetime.utcnow().date()

        if use_cache and cache_path.exists():
            mtime = datetime.utcfromtimestamp(cache_path.stat().st_mtime).date()
            if (today - mtime).days < 7:
                return pd.read_parquet(cache_path).to_dict("records")[0]

        data = self._fetch_eps_history(ticker)
        if data:
            pd.DataFrame([data]).to_parquet(cache_path)
        return data

    def _fetch_eps_history(self, ticker: str) -> dict:
        """从 yfinance 拉季度+年度利润表，提取 EPS 序列"""
        try:
            tk = self.yf.Ticker(ticker)
            result: dict = {}

            # 季度 EPS（取最近 8 个季度）
            try:
                qis = tk.quarterly_income_stmt
                if qis is not None and not qis.empty:
                    # 找 Diluted EPS 行（不同公司命名略有差异）
                    eps_row = None
                    for candidate in ["Diluted EPS", "Basic EPS"]:
                        if candidate in qis.index:
                            eps_row = qis.loc[candidate]
                            break
                    if eps_row is not None:
                        # 季度按时间升序
                        q_eps = eps_row.dropna().sort_index()
                        if len(q_eps) >= 5:
                            # dict key 用 str（避免 parquet 不支持 Timestamp key）
                            result["quarterly_eps"] = {str(k.date()): float(v) for k, v in q_eps.items()}
                            # 计算最近 4 季度的同比增速
                            yoy = {}
                            eps_list = list(q_eps.items())  # [(date, value), ...]
                            for i in range(4, min(8, len(eps_list))):
                                cur_date, cur_val = eps_list[i]
                                prev_val = eps_list[i - 4][1]
                                if prev_val and prev_val > 0:
                                    yoy[str(cur_date.date())] = float((cur_val - prev_val) / abs(prev_val))
                            result["quarterly_yoy"] = yoy
                            # 加速度检查：最近 3 个 YoY 是否单调递增
                            yoy_vals = list(yoy.values())[-3:]
                            if len(yoy_vals) == 3:
                                result["quarterly_accelerating"] = bool(
                                    yoy_vals[0] < yoy_vals[1] < yoy_vals[2]
                                )
                            else:
                                result["quarterly_accelerating"] = False
            except Exception as e:
                log.debug(f"[quarterly EPS error] {ticker}: {e}")

            # 年度 EPS（取最近 5 年）
            try:
                ais = tk.income_stmt
                if ais is not None and not ais.empty:
                    eps_row = None
                    for candidate in ["Diluted EPS", "Basic EPS"]:
                        if candidate in ais.index:
                            eps_row = ais.loc[candidate]
                            break
                    if eps_row is not None:
                        a_eps = eps_row.dropna().sort_index()
                        if len(a_eps) >= 4:
                            result["annual_eps"] = {str(k.date()): float(v) for k, v in a_eps.items()}
                            # 3 年年化增长率
                            if len(a_eps) >= 4 and a_eps.iloc[-4] > 0 and a_eps.iloc[-1] > 0:
                                result["annual_3y_cagr"] = float(
                                    (a_eps.iloc[-1] / a_eps.iloc[-4]) ** (1 / 3) - 1
                                )
            except Exception as e:
                log.debug(f"[annual EPS error] {ticker}: {e}")

            return result
        except Exception as e:
            log.error(f"[EPS history error] {ticker}: {e}")
            return {}

    # ---------- 批量加载 ----------

    def load_batch(
        self,
        tickers: Iterable[str],
        period: str = "2y",
        max_workers: int = 8,
    ) -> dict[str, pd.DataFrame]:
        """并发批量加载（IO bound，用线程池）。

        yfinance 对单个 session 的并发请求容忍度约 8。超过会被限速。
        缓存命中的 ticker 仍走串行（无网络开销）。
        """
        tickers = list(tickers)
        result: dict[str, pd.DataFrame] = {}
        # 分离缓存命中 / 未命中
        missing: list[str] = []
        today = datetime.utcnow().date()
        for t in tickers:
            cache_path = _ticker_cache_path(t)
            if cache_path.exists():
                mtime = datetime.utcfromtimestamp(cache_path.stat().st_mtime).date()
                if (today - mtime).days < self.cache_days:
                    df = pd.read_parquet(cache_path)
                    if not df.empty:
                        result[t] = df
                    continue
            missing.append(t)

        if not missing:
            return result

        # 并发拉取未命中项
        from concurrent.futures import ThreadPoolExecutor, as_completed
        max_workers = min(max_workers, len(missing))
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(self.load, t, period): t for t in missing}
            for fut in as_completed(futures):
                t = futures[fut]
                try:
                    df = fut.result()
                    if not df.empty:
                        result[t] = df
                except Exception as e:
                    log.error(f"[load_batch error] {t}: {e}")
        return result

    # ---------- SEC EDGAR 接口（精确基本面，CAN SLIM C/A 字母升级）----------

    def load_edgar_quarterly_eps(self, ticker: str, n_quarters: int = 8) -> list[dict]:
        """EDGAR 最近 n 个季度 Diluted EPS（GAAP）

        Returns:
            [{"period": "2024-Q3", "end": "2024-09-30", "eps": 1.46, "form": "10-Q"}, ...]
            失败返回 []
        """
        try:
            return self.edgar.get_quarterly_eps(ticker, n_quarters=n_quarters)
        except Exception as e:
            log.warning(f"[edgar quarterly EPS] {ticker}: {e}")
            return []

    def load_edgar_quarterly_eps_yoy(self, ticker: str, n_quarters: int = 4) -> dict[str, float]:
        """EDGAR 最近 n 个季度 EPS 同比（C 字母主数据源）"""
        try:
            return self.edgar.get_quarterly_eps_yoy(ticker, n_quarters=n_quarters)
        except Exception as e:
            log.warning(f"[edgar quarterly YoY] {ticker}: {e}")
            return {}

    def load_edgar_eps_acceleration(self, ticker: str) -> bool:
        """EDGAR EPS 加速度（连续 3 季度同比递增）"""
        try:
            return self.edgar.get_eps_acceleration(ticker)
        except Exception as e:
            log.warning(f"[edgar EPS accel] {ticker}: {e}")
            return False

    def load_edgar_annual_eps_cagr(self, ticker: str, years: int = 3) -> float | None:
        """EDGAR n 年 EPS CAGR（A 字母主数据源）"""
        try:
            return self.edgar.get_annual_eps_cagr(ticker, years=years)
        except Exception as e:
            log.warning(f"[edgar annual CAGR] {ticker}: {e}")
            return None

    def load_edgar_roe(self, ticker: str) -> float | None:
        """EDGAR 最近年度 ROE（A 字母辅助校验）"""
        try:
            return self.edgar.get_roe(ticker)
        except Exception as e:
            log.warning(f"[edgar ROE] {ticker}: {e}")
            return None

    def load_edgar_operating_margin_trend(self, ticker: str, n_years: int = 3) -> tuple[float, bool] | None:
        """EDGAR (latest_margin, is_rising) — A 字母辅助校验"""
        try:
            return self.edgar.get_operating_margin_trend(ticker, n_years=n_years)
        except Exception as e:
            log.warning(f"[edgar op margin] {ticker}: {e}")
            return None

    def load_insider_form4_count(self, ticker: str, days: int = 90) -> int:
        """EDGAR 近 N 天 Form 4 数量（S 字母 cluster buying 代理）"""
        try:
            return self.edgar.get_insider_form4_count(ticker, days=days)
        except Exception as e:
            log.warning(f"[edgar Form 4 count] {ticker}: {e}")
            return 0

    # ---------- 机构持仓（I 字母升级）----------

    def load_institutional_holders(self, ticker: str, use_cache: bool = True) -> dict | None:
        """拉 yfinance Ticker.institutional_holders，解析成结构化 I 字母输入

        Returns:
            {
                "holder_count": int,             # 报告的机构总数（截断至 yfinance 返回行数）
                "net_increasing": int,           # Change > 0 的机构数（净增持）
                "net_decreasing": int,           # Change < 0 的机构数（净减持）
                "net_new": int,                  # Change == NaN 或新进入（视为新建仓）
                "total_change_shares": float,    # sum(Change) 总股数变化方向
                "latest_report_date": str|None,  # 最近报告日 'YYYY-MM-DD'
                "top_holders": list[dict],       # 前 10 大持仓人
            }
            数据缺失返回 None（让上层降级到 institutional_pct）。

        Note:
            yfinance institutional_holders 列：Holder/Shares/Date/Change/% Out/Value
            返回行数通常 ≤ 10-40，不覆盖全部机构，仅作方向代理。
        """
        cache_path = get_cache_dir() / f"{ticker.upper()}.inst.parquet"
        today = datetime.utcnow().date()
        if use_cache and cache_path.exists():
            mtime = datetime.utcfromtimestamp(cache_path.stat().st_mtime).date()
            if (today - mtime).days < 7:
                try:
                    return pd.read_parquet(cache_path).to_dict("records")[0]
                except Exception:
                    pass  # 缓存损坏 → 重拉

        data = self._fetch_institutional_holders(ticker)
        if data:
            try:
                pd.DataFrame([data]).to_parquet(cache_path)
            except Exception as e:
                log.warning(f"[inst holders cache write] {ticker}: {e}")
        return data

    def _fetch_institutional_holders(self, ticker: str) -> dict | None:
        try:
            tk = self.yf.Ticker(ticker)
            df = tk.institutional_holders
            if df is None or df.empty:
                return None

            # 标准化列名（yfinance 返回 PascalCase）
            col_map = {c: c.lower().replace(" ", "_").replace("%", "pct") for c in df.columns}
            df = df.rename(columns=col_map)

            n_total = len(df)
            # Change 列可能缺失或全 NaN
            change_col = df.get("change")
            if change_col is not None:
                change_clean = change_col.fillna(0)
                net_inc = int((change_clean > 0).sum())
                net_dec = int((change_clean < 0).sum())
                net_new = int((change_clean == 0).sum())
                total_change = float(change_clean.sum())
            else:
                net_inc = net_dec = net_new = 0
                total_change = 0.0

            # 最近报告日（Date 列）
            latest_date = None
            date_col = df.get("date")
            if date_col is not None and len(date_col) > 0:
                try:
                    latest_date = str(pd.to_datetime(date_col).max().date())
                except Exception:
                    latest_date = None

            # 前 10 大持仓人
            top: list[dict] = []
            for _, row in df.head(10).iterrows():
                top.append({
                    "holder": str(row.get("holder", "")),
                    "shares": int(row["shares"]) if pd.notna(row.get("shares")) else None,
                    "change": float(row["change"]) if pd.notna(row.get("change")) else None,
                    "pct_out": float(row.get("pct_out", 0)) if pd.notna(row.get("pct_out")) else None,
                })

            return {
                "holder_count": int(n_total),
                "net_increasing": net_inc,
                "net_decreasing": net_dec,
                "net_new": net_new,
                "total_change_shares": total_change,
                "latest_report_date": latest_date,
                "top_holders": top,
            }
        except Exception as e:
            log.warning(f"[inst holders] {ticker}: {e}")
            return None


# 模块级便捷函数
def load_ticker(ticker: str, period: str = "2y") -> pd.DataFrame:
    return DataLoader().load(ticker, period=period)


def load_batch(tickers: Iterable[str], period: str = "2y") -> dict[str, pd.DataFrame]:
    return DataLoader().load_batch(tickers, period=period)
