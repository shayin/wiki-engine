"""VCP 算法验证脚本

用历史经典 VCP 突破案例验证算法：
- NVDA 2023-01-01 ~ 2023-05-30（AI 浪潮爆发前 VCP，从 ~145 横盘后突破 ~270 → ~800）
- META 2022-11-01 ~ 2023-02-15（底部反转后 VCP，从 ~90 → ~150）
- TSLA 2020-05-01 ~ 2020-08-31（拆股前 VCP，从 ~150 → ~2200）
- SMCI 2023-08-01 ~ 2024-01-31（AI 服务器狂飙前的 VCP，~250 → ~1000+）

跑 VCP 信号，验证算法能否识别这些教科书形态。
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

# 让脚本能直接跑
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from quant_scanner.data.loader import DataLoader
from quant_scanner.signals.vcp import VCPSignal

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# 经典 VCP 案例：(ticker, start, end, 描述)
CLASSIC_CASES = [
    ("NVDA",  "2023-01-01", "2023-05-30", "AI 浪潮爆发前的 VCP"),
    ("META",  "2022-11-01", "2023-02-15", "底部反转后的 VCP"),
    ("TSLA",  "2020-05-01", "2020-08-31", "拆股前的 VCP"),
    ("SMCI",  "2023-08-01", "2024-01-31", "AI 服务器狂飙前的 VCP"),
    ("AAPL",  "2023-06-01", "2023-12-31", "iPhone 15 周期"),
    ("NFLX",  "2022-07-01", "2022-12-31", "下跌反弹期（非 VCP 对照）"),
]


def main():
    loader = DataLoader()
    sig = VCPSignal()
    print(f"\n{'Ticker':<8} {'区间':<35} {'VCP评分':<8} {'通过':<6} 描述")
    print("-" * 100)
    for ticker, start, end, desc in CLASSIC_CASES:
        cache_key = f"{ticker}_{start.replace('-', '')}_{end.replace('-', '')}"
        df = loader.load(ticker, start=start, end=end, cache_key=cache_key)
        if df.empty:
            print(f"{ticker:<8} {start}~{end}  无数据")
            continue
        result = sig.evaluate(ticker, df)
        status = "✓" if result.passed else "✗"
        print(f"{ticker:<8} {start}~{end}  {result.value:<8.2f} {status:<6} {desc}")
        for r in result.reasons:
            print(f"           {r}")
        print()


if __name__ == "__main__":
    main()
