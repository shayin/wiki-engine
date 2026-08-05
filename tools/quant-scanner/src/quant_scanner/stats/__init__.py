"""胜率统计模块

P6 迭代：基于 cross-ai-debate 共识（R1-R10）实现的 SignalEvent 采集 + 前瞻标签系统。

核心架构（不可动摇，详见 docs/review-debate-win-rate-stats.md）：
1. SignalEvent 独立于 TradeRecord，回答信号有效性而非策略归因
2. stats 按日频独立采集，不复用 engine 周频调仓日
3. engine 通过 PIT adapter 显式传 pit_date，旧 signal 走回退
4. 前瞻标签从 t+1 open 起算（保守时点）
5. event_type 作为统一二级键
6. extractor 返回 list 允许同日多事件
7. 第一期 long-only，bearish/unknown 进诊断桶
8. holdout 默认 12 个月，事件后切分
"""
from quant_scanner.stats.pit_adapter import (
    PITAwareProtocol,
    evaluate_with_pit,
)
