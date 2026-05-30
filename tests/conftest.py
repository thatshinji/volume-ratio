"""
共享 fixtures 和数据生成工具。
"""
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "scripts"))


# ---------------------------------------------------------------------------
# 工厂函数：生成 SnapshotRecord
# ---------------------------------------------------------------------------

def make_record(
    ticker: str = "CLF.US",
    market_date: date = None,
    market_minutes: int = 600,  # 10:00
    volume: float = 10000.0,
    price: float = 20.0,
    high: float = 20.5,
    low: float = 19.5,
    change_pct: float = 0.0,
    ts: datetime = None,
) -> MagicMock:
    """生成一个 mock SnapshotRecord，符合 compute.SnapshotRecord 的接口。"""
    if market_date is None:
        market_date = date.today()
    if ts is None:
        ts = datetime(2026, 5, 18, 10, 0)
    rec = MagicMock()
    rec.ticker = ticker
    rec.market_date = market_date
    rec.market_minutes = market_minutes
    rec.volume = volume
    rec.price = price
    rec.high = high
    rec.low = low
    rec.change_pct = change_pct
    rec.ts = ts
    rec.market_ts = ts
    return rec


def sample_config(overrides: dict = None) -> dict:
    """返回测试用 config fixture。"""
    cfg = {
        "watchlist": {
            "us": ["CLF.US-克利夫兰"],
            "hk": [],
            "cn": [],
        },
        "params": {
            "volume_ratio_window": 5,
            "intraday_signal_window_minutes": 5,
            "intraday_baseline_minutes": 30,
            "intraday_baseline_method": "mean",
            "intraday_alert_threshold": 1.5,
            "alert_threshold": 2.0,
            "shrink_threshold": 0.6,
        },
        "mute": {},
    }
    if overrides:
        # 浅合并 params
        if "params" in overrides:
            cfg["params"] = {**cfg["params"], **overrides["params"]}
        cfg.update({k: v for k, v in overrides.items() if k != "params"})
    return cfg


# ---------------------------------------------------------------------------
# 缓存清理
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clear_compute_caches():
    """每个测试前清理 compute.py 的模块级缓存。"""
    from compute import (
        _snapshot_cache,
        _minute_bar_cache,
        _minute_bar_presence_cache,
        _last_ratio_write,
        _db_initialized,
    )
    _snapshot_cache.clear()
    _minute_bar_cache.clear()
    _minute_bar_presence_cache.clear()
    _last_ratio_write.clear()
    # 不重置 _db_initialized，避免 init_db 副作用
    yield
    _snapshot_cache.clear()
    _minute_bar_cache.clear()
    _minute_bar_presence_cache.clear()
    _last_ratio_write.clear()


@pytest.fixture(autouse=True)
def clear_market_caches():
    """每个测试前清理 market.py 的缓存。"""
    from core.market import (
        _trading_days_cache,
        _trading_days_range_cache,
        _trading_days_lookup_cache,
    )
    _trading_days_cache.clear()
    _trading_days_range_cache.clear()
    _trading_days_lookup_cache.clear()
    yield
    _trading_days_cache.clear()
    _trading_days_range_cache.clear()
    _trading_days_lookup_cache.clear()


@pytest.fixture(autouse=True)
def clear_config_cache():
    """每个测试前清理 config.py 的缓存。"""
    import core.config
    core.config._config_cache = None
    core.config._config_mtime = 0
    yield
    core.config._config_cache = None
    core.config._config_mtime = 0
