from .base import BaseSignal, SignalResult
from .trend_template import TrendTemplateSignal
from .vcp import VCPSignal
from .can_slim import CANSLIMSignal
from .rs_rating import RSRatingSignal
from .major_reversal import MajorReversalSignal
from .continuation import ContinuationSignal
from .oscillator_timing import OscillatorTimingSignal
from .market_direction import MarketDirectionSignal
from .new_high_supply import NewHighSupplySignal
from .dow_phases import DowPhasesSignal
from .support_resistance import SupportResistanceSignal

__all__ = [
    "BaseSignal",
    "SignalResult",
    "TrendTemplateSignal",
    "VCPSignal",
    "CANSLIMSignal",
    "RSRatingSignal",
    "MajorReversalSignal",
    "ContinuationSignal",
    "OscillatorTimingSignal",
    "MarketDirectionSignal",
    "NewHighSupplySignal",
    "DowPhasesSignal",
    "SupportResistanceSignal",
]
