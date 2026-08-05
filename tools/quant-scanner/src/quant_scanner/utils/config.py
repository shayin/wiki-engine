"""路径和配置管理"""
from __future__ import annotations

from pathlib import Path

# 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parents[3]

CACHE_DIR = PROJECT_ROOT / "data_cache"
REPORTS_DIR = PROJECT_ROOT / "reports"


def get_cache_dir() -> Path:
    """获取数据缓存目录"""
    ensure_dirs()
    return CACHE_DIR


def get_reports_dir() -> Path:
    """获取报告输出目录"""
    ensure_dirs()
    return REPORTS_DIR


def ensure_dirs() -> None:
    """确保必要目录存在"""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
