"""扫描引擎

组合多个 signal，对一组 ticker 做评估，输出排名。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Iterable

import pandas as pd
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn

from quant_scanner.data.loader import DataLoader
from quant_scanner.signals.base import BaseSignal, SignalResult

log = logging.getLogger(__name__)
console = Console()


@dataclass
class ScanReport:
    """一次扫描的结果"""

    results: list[dict] = field(default_factory=list)
    # 每个 ticker 一个 dict：{ticker, signals: [SignalResult...], composite_score, passed}

    def passed(self) -> list[dict]:
        return [r for r in self.results if r.get("passed")]

    def top_n(self, n: int = 10) -> list[dict]:
        return sorted(self.results, key=lambda r: -r.get("composite_score", 0))[:n]


class Scanner:
    """扫描器"""

    def __init__(
        self,
        signals: list[BaseSignal],
        loader: DataLoader | None = None,
        weights: dict[str, float] | None = None,
    ):
        """
        Args:
            signals: 信号列表
            loader: 数据加载器（None 则用默认）
            weights: 各信号权重（key=signal.name）。默认等权
        """
        self.signals = signals
        self.loader = loader or DataLoader()
        if weights is None:
            weights = {s.name: 1.0 / len(signals) for s in signals}
        else:
            total = sum(weights.values())
            weights = {k: v / total for k, v in weights.items()}
        self.weights = weights

    def scan(
        self,
        tickers: Iterable[str],
        period: str = "2y",
        show_progress: bool = True,
        max_workers: int = 1,
    ) -> ScanReport:
        """扫描一组 ticker

        Args:
            tickers: 股票代码列表
            period: 拉取历史数据周期
            show_progress: 是否显示进度条
            max_workers: 并发 worker 数（IO bound，建议 4-8）。1 = 串行
        """
        tickers = list(tickers)
        report = ScanReport()

        if max_workers > 1:
            return self._scan_parallel(tickers, period, max_workers, show_progress)

        if show_progress:
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TextColumn("{task.completed}/{task.total}"),
                console=console,
            ) as progress:
                task = progress.add_task("扫描中...", total=len(tickers))
                for ticker in tickers:
                    self._scan_one(ticker, period, report)
                    progress.advance(task)
        else:
            for ticker in tickers:
                self._scan_one(ticker, period, report)

        return report

    def _scan_parallel(
        self,
        tickers: list[str],
        period: str,
        max_workers: int,
        show_progress: bool,
    ) -> ScanReport:
        """并发扫描（IO bound）。

        注意：Progress console 与多线程日志可能交错。日志级别调 WARNING 以下不影响。
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        report = ScanReport()
        # 局部收集再合并，避免线程竞争
        local_results: dict[str, dict] = {}

        def _do(ticker: str):
            try:
                df = self.loader.load(ticker, period=period)
                if df.empty:
                    log.warning(f"跳过 {ticker}：无数据")
                    return None
                sig_results: list[SignalResult] = []
                weighted_sum = 0.0
                for sig in self.signals:
                    sr = sig.evaluate(ticker, df)
                    sig_results.append(sr)
                    weighted_sum += self.weights.get(sig.name, 0) * sr.value
                composite = weighted_sum
                passed = all(sr.passed for sr in sig_results) if sig_results else False
                return {
                    "ticker": ticker,
                    "signals": sig_results,
                    "composite_score": composite,
                    "passed": passed,
                }
            except Exception as e:
                log.error(f"[scan error] {ticker}: {e}")
                return None

        if show_progress:
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TextColumn("{task.completed}/{task.total}"),
                console=console,
            ) as progress:
                task = progress.add_task("并发扫描中...", total=len(tickers))
                with ThreadPoolExecutor(max_workers=max_workers) as pool:
                    futures = {pool.submit(_do, t): t for t in tickers}
                    for fut in as_completed(futures):
                        res = fut.result()
                        if res:
                            local_results[res["ticker"]] = res
                        progress.advance(task)
        else:
            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                futures = {pool.submit(_do, t): t for t in tickers}
                for fut in as_completed(futures):
                    res = fut.result()
                    if res:
                        local_results[res["ticker"]] = res

        # 按 tickers 原始顺序输出
        for t in tickers:
            if t in local_results:
                report.results.append(local_results[t])
        return report

    def _scan_one(self, ticker: str, period: str, report: ScanReport) -> None:
        try:
            df = self.loader.load(ticker, period=period)
            if df.empty:
                log.warning(f"跳过 {ticker}：无数据")
                return

            sig_results: list[SignalResult] = []
            weighted_sum = 0.0
            for sig in self.signals:
                sr = sig.evaluate(ticker, df)
                sig_results.append(sr)
                weighted_sum += self.weights.get(sig.name, 0) * sr.value

            composite = weighted_sum  # 已归一化到 [-1, +1]
            passed = all(sr.passed for sr in sig_results) if sig_results else False

            report.results.append({
                "ticker": ticker,
                "signals": sig_results,
                "composite_score": composite,
                "passed": passed,
            })
        except Exception as e:
            log.error(f"[scan error] {ticker}: {e}")
