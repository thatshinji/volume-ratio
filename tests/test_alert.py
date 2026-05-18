"""
alert.py 核心逻辑单元测试。
"""
import sys
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from alert import should_push, detect_signals, SIGNAL_PRIORITY
from tests.conftest import sample_config


# ==========================================================================
# should_push — 状态机逻辑
# ==========================================================================

# 直接 mock 底层数据库操作，避免文件/I/O 复杂性
@patch("alert.get_signal_state")
@patch("alert.update_signal_state")
def test_first_appearance_pushes(mock_update, mock_get):
    """首次出现，推送"""
    mock_get.return_value = None
    assert should_push("CLF.US", "放量") is True


@patch("alert.get_signal_state")
@patch("alert.update_signal_state")
def test_state_continues_silent(mock_update, mock_get):
    """状态持续，静默"""
    mock_get.return_value = "放量"  # 旧状态 = 放量
    assert should_push("CLF.US", "放量") is False  # 新状态相同，不推送


@patch("alert.get_signal_state")
@patch("alert.update_signal_state")
def test_state_upgrade_pushes(mock_update, mock_get):
    """状态升级，推送"""
    mock_get.return_value = "缩量"
    assert should_push("CLF.US", "放量") is True


@patch("alert.get_signal_state")
@patch("alert.update_signal_state")
def test_state_downgrade_pushes(mock_update, mock_get):
    """状态降级，推送"""
    mock_get.return_value = "放量"
    assert should_push("CLF.US", "正常") is True


@patch("alert.get_signal_state")
@patch("alert.update_signal_state")
def test_same_priority_pushes(mock_update, mock_get):
    """同级变化（方向改变），推送"""
    mock_get.return_value = "放量突破"
    assert should_push("CLF.US", "放量下跌") is True


@patch("alert.get_signal_state")
@patch("alert.update_signal_state")
def test_giants_still_pushes(mock_update, mock_get):
    """巨量 → 任何变化都推送"""
    mock_get.return_value = "巨量"
    assert should_push("CLF.US", "正常") is True


# ==========================================================================
# detect_signals
# ==========================================================================

def _mock_result(
    ticker: str = "CLF.US",
    ratio: float = 1.0,
    ratio_intraday: float = 0.0,
    change_pct: float = 0.0,
    price: float = 20.0,
    signal: str = "正常",
    signal_detail: str = "",
    data_quality: str = "ok",
    sample_days: int = 5,
    volume_avg5: float = 100.0,
    signal_intraday: str = "",
    cond_vol: bool = False,
    cond_stop: bool = False,
    cond_stable: bool = False,
) -> dict:
    return {
        "ticker": ticker,
        "name": ticker,
        "ratio": ratio,
        "ratio_intraday": ratio_intraday,
        "change_pct": change_pct,
        "price": price,
        "signal": signal,
        "signal_detail": signal_detail,
        "data_quality": data_quality,
        "historical_sample_days": sample_days,
        "volume_avg5": volume_avg5,
        "signal_intraday": signal_intraday,
        "cond_vol": cond_vol,
        "cond_stop": cond_stop,
        "cond_stable": cond_stable,
    }


@patch("core.market.get_market")
@patch("alert.load_config")
@patch("core.market.is_market_trading")
def test_detect_volume_breakout(mock_trading, mock_cfg, mock_market):
    """放量突破信号触发"""
    mock_cfg.return_value = sample_config()
    mock_market.return_value = "US"
    mock_trading.return_value = True

    alerts, expired = detect_signals([_mock_result(ratio=2.5, change_pct=3.0)])

    assert len(alerts) == 1
    assert "放量突破" in alerts[0]["triggered_signals"]


@patch("core.market.get_market")
@patch("alert.load_config")
@patch("core.market.is_market_trading")
def test_detect_shrink_rebound(mock_trading, mock_cfg, mock_market):
    """缩量止跌信号触发"""
    mock_cfg.return_value = sample_config()
    mock_market.return_value = "US"
    mock_trading.return_value = True

    alerts, expired = detect_signals([_mock_result(ratio=0.5, change_pct=1.0)])

    assert len(alerts) == 1
    assert "缩量止跌" in alerts[0]["triggered_signals"]


@patch("core.market.get_market")
@patch("alert.load_config")
@patch("core.market.is_market_trading")
def test_detect_threshold_only(mock_trading, mock_cfg, mock_market):
    """无规则匹配，但超过阈值时触发"""
    mock_cfg.return_value = sample_config()
    mock_market.return_value = "US"
    mock_trading.return_value = True

    alerts, expired = detect_signals([_mock_result(ratio=2.5, change_pct=0.5)])

    assert len(alerts) == 1
    assert any("放量" in s for s in alerts[0]["triggered_signals"])


@patch("core.market.get_market")
@patch("alert.load_config")
@patch("core.market.is_market_trading")
def test_detect_market_closed_skipped(mock_trading, mock_cfg, mock_market):
    """休市市场不触发"""
    mock_cfg.return_value = sample_config()
    mock_market.return_value = "US"
    mock_trading.return_value = False

    alerts, expired = detect_signals([_mock_result(ratio=5.0, change_pct=3.0)])

    assert len(alerts) == 0


@patch("core.market.get_market")
@patch("alert.load_config")
@patch("core.market.is_market_trading")
def test_detect_muted_ticker_skipped(mock_trading, mock_cfg, mock_market):
    """在 mute 列表中的 ticker 不触发"""
    cfg = sample_config()
    cfg["mute"] = {"CLF.US": (datetime.now() + timedelta(hours=1)).isoformat()}
    mock_cfg.return_value = cfg
    mock_market.return_value = "US"
    mock_trading.return_value = True

    alerts, expired = detect_signals([_mock_result(ratio=5.0, change_pct=3.0)])

    assert len(alerts) == 0


@patch("core.market.get_market")
@patch("alert.load_config")
@patch("core.market.is_market_trading")
def test_detect_intraday_banner_stop_fall(mock_trading, mock_cfg, mock_market):
    """日内放量止跌触发"""
    mock_cfg.return_value = sample_config()
    mock_market.return_value = "US"
    mock_trading.return_value = True

    alerts, expired = detect_signals([_mock_result(
        ratio=1.0,
        ratio_intraday=3.0,
        signal="正常",
        signal_intraday="放量止跌",
        cond_vol=True,
        cond_stop=True,
        cond_stable=True,
    )])

    assert len(alerts) == 1
    assert "放量止跌" in alerts[0]["triggered_signals"]


@patch("core.market.get_market")
@patch("alert.load_config")
@patch("core.market.is_market_trading")
def test_detect_sample_insufficient_skipped(mock_trading, mock_cfg, mock_market):
    """样本不足且无 detail 时不触发"""
    mock_cfg.return_value = sample_config()
    mock_market.return_value = "US"
    mock_trading.return_value = True

    alerts, expired = detect_signals([_mock_result(
        ratio=2.5,
        change_pct=3.0,
        signal="样本不足(1/5)",
        data_quality="数据不足",
        sample_days=1,
    )])

    assert len(alerts) == 0


@patch("core.market.get_market")
@patch("alert.load_config")
@patch("core.market.is_market_trading")
def test_detect_multiple_tickers(mock_trading, mock_cfg, mock_market):
    """多个 ticker 同时触发"""
    mock_cfg.return_value = sample_config()
    mock_market.return_value = "US"
    mock_trading.return_value = True

    results = [
        _mock_result(ticker="CLF.US", ratio=2.5, change_pct=3.0),
        _mock_result(ticker="AAPL.US", ratio=0.4, change_pct=1.0),
    ]
    alerts, expired = detect_signals(results)

    assert len(alerts) == 2
    tickers = {a["ticker"] for a in alerts}
    assert "CLF.US" in tickers
    assert "AAPL.US" in tickers


# ==========================================================================
# SIGNAL_PRIORITY 常量完整性
# ==========================================================================

def test_signal_priority_all_defined():
    """所有信号状态都在优先级表中定义。"""
    expected = {
        "正常", "缩量", "放量", "放量突破", "放量下跌",
        "放量止跌", "缩量止跌", "尾盘放量", "巨量",
    }
    assert set(SIGNAL_PRIORITY.keys()) == expected


def test_signal_priority_order():
    """优先级递增：正常 < 缩量/放量 < 放量xxx/缩量止跌 < 巨量"""
    assert SIGNAL_PRIORITY["正常"] < SIGNAL_PRIORITY["缩量"]
    assert SIGNAL_PRIORITY["缩量"] < SIGNAL_PRIORITY["放量"]
    assert SIGNAL_PRIORITY["放量"] < SIGNAL_PRIORITY["放量突破"]
    assert SIGNAL_PRIORITY["放量突破"] < SIGNAL_PRIORITY["巨量"]
    assert SIGNAL_PRIORITY["放量突破"] == SIGNAL_PRIORITY["放量下跌"]
    assert SIGNAL_PRIORITY["放量突破"] == SIGNAL_PRIORITY["放量止跌"]
