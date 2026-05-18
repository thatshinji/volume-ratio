"""
compute.py 核心算法单元测试。
"""
import sys
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from compute import get_signal, get_signal_detail, calc_historical_ratio_detail
from compute import calc_intraday_ratio_detail, SnapshotRecord
from tests.conftest import sample_config


# ==========================================================================
# get_signal — 纯函数
# ==========================================================================

@pytest.mark.parametrize("ratio,expected", [
    (6.0,   "巨量"),
    (5.0,   "显著放量"),
    (3.5,   "显著放量"),
    (2.0,   "放量"),
    (1.5,   "放量"),
    (1.2,   "正常"),
    (1.0,   "正常"),
    (0.8,   "正常"),
    (0.7,   "缩量"),
    (0.6,   "缩量"),
    (0.4,   "缩量异常"),
    (0.0,   "数据不足"),
    (-1.0,  "数据不足"),
])
def test_get_signal(ratio, expected):
    assert get_signal(ratio) == expected


# ==========================================================================
# get_signal_detail — 纯函数
# ==========================================================================

@pytest.mark.parametrize("ratio,change,expected", [
    (2.5,  3.0,  "放量突破"),
    (2.1,  2.1,  "放量突破"),
    (2.5, -3.0,  "放量下跌"),
    (2.1, -2.1,  "放量下跌"),
    (0.5,  1.0,  "缩量止跌"),
    (0.5,  0.5,  "缩量止跌"),
    (1.5,  0.0,  ""),
    (1.0,  1.0,  ""),
])
def test_get_signal_detail(ratio, change, expected):
    assert get_signal_detail(ratio, change) == expected


# ==========================================================================
# calc_historical_ratio_detail
#
# 调用路径：
#   calc_historical_ratio_detail
#   → _resolve_session_time → _cumulative_volume_at(today_records, target_minute)
#   → _available_market_dates(ticker, before=market_date)
#   → is_trading_day_on(market, past_date)     ← 每个历史日期
#   → _cumulative_volume_at(_records_for_date(...), target_minute)  ← 每个历史日期
#
# mock _cumulative_volume_at 控制今日量和历史均量；
# mock _available_market_dates 控制历史日期数量；
# mock is_trading_day_on 让循环正常遍历。
# ==========================================================================

from core.market import is_trading_day_on


@patch("compute.load_config")
@patch("compute._cumulative_volume_at")
@patch("compute._available_market_dates")
@patch("compute.is_trading_day_on", new=lambda m, d: True)
def test_historical_ratio_normal(mock_avail, mock_cum_vol, mock_cfg):
    """今日量=200，历史均量=100，ratio=2.0"""
    mock_cfg.return_value = sample_config()
    mock_avail.return_value = [date(2026, 5, 15)]
    # 第一次 = 今日；第二次 = 唯一历史日期
    mock_cum_vol.side_effect = [200.0, 100.0]

    result = calc_historical_ratio_detail("CLF.US")

    assert result["ratio"] == pytest.approx(2.0, rel=0.01)
    assert result["quality"] == "ok"


@patch("compute.load_config")
@patch("compute._cumulative_volume_at")
@patch("compute._available_market_dates")
@patch("compute.is_trading_day_on", new=lambda m, d: True)
def test_historical_ratio_insufficient_samples(mock_avail, mock_cum_vol, mock_cfg):
    """历史均量为0，ratio=0，quality='数据不足'"""
    mock_cfg.return_value = sample_config()
    mock_avail.return_value = [date(2026, 5, 15)]
    mock_cum_vol.side_effect = [200.0, 0.0]

    result = calc_historical_ratio_detail("CLF.US")

    assert result["ratio"] == 0.0
    assert result["quality"] == "数据不足"


@patch("compute.load_config")
@patch("compute._cumulative_volume_at")
@patch("compute._available_market_dates")
@patch("compute.is_trading_day_on", new=lambda m, d: True)
def test_historical_ratio_zero_history(mock_avail, mock_cum_vol, mock_cfg):
    """今日量为0，ratio=0"""
    mock_cfg.return_value = sample_config()
    mock_avail.return_value = [date(2026, 5, 15)]
    mock_cum_vol.side_effect = [0.0, 100.0]

    result = calc_historical_ratio_detail("CLF.US")

    assert result["ratio"] == 0.0


@patch("compute.load_config")
@patch("compute._cumulative_volume_at")
@patch("compute._available_market_dates")
@patch("compute.is_trading_day_on", new=lambda m, d: True)
def test_historical_ratio_no_data(mock_avail, mock_cum_vol, mock_cfg):
    """无任何数据，ratio=0"""
    mock_cfg.return_value = sample_config()
    mock_avail.return_value = []
    mock_cum_vol.side_effect = [0.0, 0.0]

    result = calc_historical_ratio_detail("CLF.US")

    assert result["ratio"] == 0.0
    assert result["sample_days"] == 0


# ==========================================================================
# calc_intraday_ratio_detail
#
# 调用路径：
#   → _resolve_session_time → _records_for_date → read_market_snapshots
#   → _window_volume(records, signal_end, 5)   ← 信号窗口
#   → _window_volume(records, cursor_i, 5) × N  ← 基准窗口（循环 N 次）
#
# mock _window_volume 可控制信号窗口量与基准均量，从而控制 ratio。
# ==========================================================================

def _mock_records(vol_per_min: float, start: int, end: int, ticker: str = "CLF.US"):
    """生成 mock SnapshotRecord 列表，volume = vol_per_min（用于验证增量结构）。"""
    records = []
    d = date(2026, 5, 18)
    minute = start
    while minute <= end:
        rec = MagicMock(spec=SnapshotRecord)
        rec.ticker = ticker
        rec.market_date = d
        rec.market_minutes = minute
        rec.volume = vol_per_min   # 增量（_window_volume 取 max）
        rec.price = 20.0
        rec.high = 20.5
        rec.low = 19.5
        rec.change_pct = 0.0
        rec.ts = datetime(2026, 5, 18, minute // 60, minute % 60)
        rec.market_ts = rec.ts
        records.append(rec)
        minute += 1
    return records


@patch("compute.load_config")
@patch("compute._window_volume")
@patch("compute._resolve_session_time")
def test_intraday_ratio_normal(mock_resolve, mock_win_vol, mock_cfg):
    """信号窗口量=75，基准均量=25，ratio=3.0"""
    mock_cfg.return_value = sample_config()
    mock_resolve.return_value = (
        datetime(2026, 5, 18, 10, 30),
        date(2026, 5, 18),
        630,
        _mock_records(5.0, 570, 630) + _mock_records(15.0, 626, 630),
    )
    # 第1次 = 信号窗口量=75；后续 = 基准均量=25（11次）
    mock_win_vol.side_effect = [75.0] + [25.0] * 11

    result = calc_intraday_ratio_detail("CLF.US")

    assert result["ratio"] == pytest.approx(3.0, rel=0.05)
    assert result["cond_vol"] is True


@patch("compute.load_config")
@patch("compute.is_trading_day_on")
def test_intraday_ratio_no_data(is_trading, mock_cfg):
    """无记录 → 数据不足"""
    mock_cfg.return_value = sample_config()
    is_trading.return_value = True

    result = calc_intraday_ratio_detail("CLF.US")

    assert result["signal"] == "数据不足"
    assert result["ratio"] == 0.0


@patch("compute.load_config")
@patch("compute.is_trading_day_on")
def test_intraday_ratio_market_closed(is_trading, mock_cfg):
    """非交易日"""
    mock_cfg.return_value = sample_config()
    is_trading.return_value = False

    result = calc_intraday_ratio_detail("CLF.US")

    assert result["signal"] == "休市"


@patch("compute.load_config")
@patch("compute._window_volume")
@patch("compute._resolve_session_time")
def test_intraday_ratio_banner_stop_fall(mock_resolve, mock_win_vol, mock_cfg):
    """放量止跌：三条件全部满足"""
    mock_cfg.return_value = sample_config()

    base = []
    for m in range(570, 626):
        rec = MagicMock(spec=SnapshotRecord)
        rec.ticker = "CLF.US"
        rec.market_date = date(2026, 5, 18)
        rec.market_minutes = m
        rec.volume = 5.0
        rec.price = 20.0
        rec.high = 20.5
        rec.low = 19.5
        rec.change_pct = 0.0
        rec.ts = datetime(2026, 5, 18, m // 60, m % 60)
        rec.market_ts = rec.ts
        base.append(rec)

    sig = []
    for m in range(626, 631):
        rec = MagicMock(spec=SnapshotRecord)
        rec.ticker = "CLF.US"
        rec.market_date = date(2026, 5, 18)
        rec.market_minutes = m
        rec.volume = 15.0
        rec.price = 20.3
        rec.high = 20.5
        rec.low = 20.1
        rec.change_pct = 0.0
        rec.ts = datetime(2026, 5, 18, m // 60, m % 60)
        rec.market_ts = rec.ts
        sig.append(rec)

    mock_resolve.return_value = (
        datetime(2026, 5, 18, 10, 30),
        date(2026, 5, 18),
        630,
        base + sig,
    )
    mock_win_vol.side_effect = [75.0] + [25.0] * 11

    result = calc_intraday_ratio_detail("CLF.US")

    assert result["signal"] == "放量止跌"
    assert result["cond_vol"] is True
    assert result["cond_stop"] is True
    assert result["cond_stable"] is True
