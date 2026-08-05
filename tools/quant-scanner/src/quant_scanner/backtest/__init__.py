"""回测模块

事件驱动回测框架，支持现有 VCP / Trend Template / CAN SLIM 信号。

示例：
    from quant_scanner.backtest import Backtester
    from quant_scanner.signals.trend_template import TrendTemplateSignal
    from quant_scanner.signals.vcp import VCPSignal
    from quant_scanner.signals.can_slim import CANSLIMSignal

    bt = Backtester(
        signals=[TrendTemplateSignal(), VCPSignal(), CANSLIMSignal()],
        universe=["TSLA", "NVDA", "AAPL"],
    )
    report = bt.run(start="2020-01-01", end="2025-12-31")
    print(report.summary_dict())
"""
from quant_scanner.backtest.engine import BacktestReport, Backtester, TradeRecord

__all__ = ["Backtester", "BacktestReport", "TradeRecord"]
