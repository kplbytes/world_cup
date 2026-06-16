"""时间工具函数"""

from __future__ import annotations

import pandas as pd


def ensure_utc(dt: pd.Timestamp | str) -> pd.Timestamp:
    """确保时间戳为 UTC 时区。"""
    ts = pd.Timestamp(dt)
    if ts.tz is None:
        return ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def format_date(dt: pd.Timestamp | str) -> str:
    """格式化日期为 YYYY-MM-DD。"""
    return pd.Timestamp(dt).strftime("%Y-%m-%d")
