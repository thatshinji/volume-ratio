#!/usr/bin/env python3
"""
量比计算引擎。

historical_ratio = 今日开盘至当前市场时刻累计成交量 / 过去 N 个交易日同一市场时刻累计成交量均值
intraday_ratio = 最近 W 分钟成交量 / 今天前 B 分钟内每 W 分钟成交量的均值
"""

import json
import os
import sqlite3
import sys
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from statistics import mean, median
from typing import Optional, List
from zoneinfo import ZoneInfo

ROOT = Path(__file__).parent.parent
SNAPSHOT_DIR = ROOT / "data" / "snapshots"
DB_PATH = ROOT / "data" / "ratios.db"
SCHEMA_VERSION = 3

sys.path.insert(0, str(ROOT / "scripts"))

from core.config import load_config
from core.market import get_market, get_all_tickers, get_ticker_name, is_market_trading
from core.market import _is_trading_day as is_trading_day, is_trading_day_on
from core.silence import suppress_stdout

MARKET_TZ = {
    "CN": ZoneInfo("Asia/Shanghai"),
    "HK": ZoneInfo("Asia/Hong_Kong"),
    "US": ZoneInfo("America/New_York"),
}
def _local_tz():
    return datetime.now().astimezone().tzinfo

DEFAULT_HISTORY_DAYS = 5
INTRADAY_SIGNAL_WINDOW_MINUTES = 5
INTRADAY_BASELINE_MINUTES = 30
MIN_HISTORY_SAMPLES = 3
RATIO_WRITE_INTERVAL = 300

MAX_SNAPSHOT_CACHE_ITEMS = 128
MAX_MINUTE_BAR_CACHE_ITEMS = 128
MAX_PRESENCE_CACHE_ITEMS = 512

_snapshot_cache = {}
_minute_bar_cache = {}
_minute_bar_presence_cache = {}
_last_ratio_write = {}
_db_initialized = False


def _cache_put(cache: dict, key, value, max_items: int):
    """Small FIFO cache cap for long-running bot processes."""
    cache[key] = value
    while len(cache) > max_items:
        cache.pop(next(iter(cache)))


@dataclass
class SnapshotRecord:
    ticker: str
    ts: datetime
    market_ts: datetime
    market_date: date
    market_minutes: int
    price: float
    high: float
    low: float
    volume: float
    turnover: float
    change_pct: float


def parse_timestamp(ts: str) -> Optional[datetime]:
    """解析 ISO timestamp。JSONL 中的 naive 时间按本机时区处理。"""
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_local_tz())
    return dt


def _market_tz(market: str) -> ZoneInfo:
    return MARKET_TZ.get(market, MARKET_TZ["US"])


def _to_market_dt(dt: datetime, market: str) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_local_tz())
    return dt.astimezone(_market_tz(market))


def _minutes(t: time) -> int:
    return t.hour * 60 + t.minute


def _market_sessions(market: str) -> list[tuple[int, int]]:
    if market == "CN":
        return [(_minutes(time(9, 30)), _minutes(time(11, 30))), (_minutes(time(13, 0)), _minutes(time(15, 0)))]
    if market == "HK":
        return [(_minutes(time(9, 30)), _minutes(time(12, 0))), (_minutes(time(13, 0)), _minutes(time(16, 0)))]
    return [(_minutes(time(9, 30)), _minutes(time(16, 0)))]


def _is_regular_session(market: str, market_dt: datetime) -> bool:
    minute = market_dt.hour * 60 + market_dt.minute
    return any(start <= minute <= end for start, end in _market_sessions(market))


def _session_start_minutes(market: str, minute: int) -> int:
    sessions = _market_sessions(market)
    for start, end in sessions:
        if start <= minute <= end:
            return start
    return sessions[0][0]


def _is_same_or_before_market_time(record: SnapshotRecord, target_minute: int) -> bool:
    return record.market_minutes <= target_minute


def get_jsonl_path(ticker: str, day: datetime = None) -> Path:
    """获取旧本地日期 JSONL 路径。保留给采集和兼容调用使用。"""
    if day is None:
        day = datetime.now()
    market = get_market(ticker)
    day_str = day.strftime("%Y%m%d")
    filename = f"{ticker.replace('.', '_')}_{day_str}.jsonl"
    return SNAPSHOT_DIR / market / filename


def read_snapshots(ticker: str, day: datetime = None) -> List[dict]:
    """读取指定本地日期 JSONL。新量比计算使用 read_market_snapshots()。"""
    if day is None:
        day = datetime.now()
    jsonl_path = get_jsonl_path(ticker, day)
    if not jsonl_path.exists():
        return []

    records = []
    try:
        with open(jsonl_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return records


def _snapshot_files(ticker: str) -> list[Path]:
    market = get_market(ticker)
    market_dir = SNAPSHOT_DIR / market
    if not market_dir.exists():
        return []
    prefix = ticker.replace(".", "_")
    return sorted(market_dir.glob(f"{prefix}_*.jsonl"))


def _to_record(raw: dict, market: str) -> Optional[SnapshotRecord]:
    ts = parse_timestamp(raw.get("timestamp", ""))
    if not ts:
        return None
    market_ts = _to_market_dt(ts, market)
    if not _is_regular_session(market, market_ts):
        return None
    try:
        price = float(raw.get("price", 0) or 0)
        volume = float(raw.get("volume", 0) or 0)
        high = float(raw.get("high", 0) or 0)
        low = float(raw.get("low", 0) or 0)
        turnover = float(raw.get("turnover", 0) or 0)
        change_pct = float(raw.get("change_pct", 0) or 0)
    except (TypeError, ValueError):
        return None
    if price <= 0 or volume < 0:
        return None
    return SnapshotRecord(
        ticker=raw.get("ticker", ""),
        ts=ts,
        market_ts=market_ts,
        market_date=market_ts.date(),
        market_minutes=market_ts.hour * 60 + market_ts.minute,
        price=price,
        high=high,
        low=low,
        volume=volume,
        turnover=turnover,
        change_pct=change_pct,
    )


def _row_to_record(row: sqlite3.Row) -> Optional[SnapshotRecord]:
    try:
        ts = parse_timestamp(row["last_timestamp"])
        market_ts = parse_timestamp(row["market_timestamp"])
        if not ts or not market_ts:
            return None
        return SnapshotRecord(
            ticker=row["ticker"],
            ts=ts,
            market_ts=market_ts,
            market_date=date.fromisoformat(row["market_date"]),
            market_minutes=int(row["market_minute"]),
            price=float(row["close"] or 0),
            high=float(row["high"] or row["close"] or 0),
            low=float(row["low"] or row["close"] or 0),
            volume=float(row["volume"] or 0),
            turnover=float(row["turnover"] or 0),
            change_pct=float(row["change_pct"] or 0),
        )
    except (TypeError, ValueError, KeyError):
        return None


def _minute_bar_table_exists(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='quote_minute_bars'"
    ).fetchone()
    return row is not None


def _ticker_has_minute_bars(ticker: str) -> bool:
    if ticker in _minute_bar_presence_cache:
        return _minute_bar_presence_cache[ticker]
    init_db()
    try:
        with sqlite3.connect(get_db_path(), timeout=30) as conn:
            if not _minute_bar_table_exists(conn):
                _cache_put(_minute_bar_presence_cache, ticker, False, MAX_PRESENCE_CACHE_ITEMS)
                return False
            row = conn.execute(
                "SELECT 1 FROM quote_minute_bars WHERE ticker = ? LIMIT 1",
                (ticker,),
            ).fetchone()
            has_rows = row is not None
            _cache_put(_minute_bar_presence_cache, ticker, has_rows, MAX_PRESENCE_CACHE_ITEMS)
            return has_rows
    except sqlite3.Error:
        return False


def read_minute_bars(ticker: str, target_date: date = None) -> list[SnapshotRecord]:
    """从 SQLite 分钟聚合表读取计算用快照，避免每次扫描全量 JSONL。"""
    init_db()
    cache_key = (ticker, target_date.isoformat() if target_date else "*")
    if cache_key in _minute_bar_cache:
        return _minute_bar_cache[cache_key]

    params = [ticker]
    where = "ticker = ?"
    if target_date:
        where += " AND market_date = ?"
        params.append(target_date.isoformat())

    try:
        with sqlite3.connect(get_db_path(), timeout=30) as conn:
            conn.row_factory = sqlite3.Row
            if not _minute_bar_table_exists(conn):
                _cache_put(_minute_bar_cache, cache_key, [], MAX_MINUTE_BAR_CACHE_ITEMS)
                return []
            rows = conn.execute(
                f"""
                SELECT ticker, last_timestamp, market_timestamp, market_date, market_minute,
                       close, high, low, volume, turnover, change_pct
                FROM quote_minute_bars
                WHERE {where}
                ORDER BY market_date, market_minute, last_timestamp
                """,
                params,
            ).fetchall()
    except sqlite3.Error:
        return []

    records = [rec for row in rows if (rec := _row_to_record(row))]
    _cache_put(_minute_bar_cache, cache_key, records, MAX_MINUTE_BAR_CACHE_ITEMS)
    if records:
        _cache_put(_minute_bar_presence_cache, ticker, True, MAX_PRESENCE_CACHE_ITEMS)
    return records


def read_market_snapshots(ticker: str, target_date: date = None) -> list[SnapshotRecord]:
    """读取并清洗指定市场交易日的快照，优先使用 SQLite 分钟聚合。"""
    minute_records = read_minute_bars(ticker, target_date)
    if minute_records:
        return minute_records

    market = get_market(ticker)
    cache_key = (ticker, target_date.isoformat() if target_date else "*")
    mtimes = []
    for p in _snapshot_files(ticker):
        try:
            mtimes.append((p, p.stat().st_mtime_ns))
        except OSError:
            continue
    mtimes = tuple(mtimes)
    cached = _snapshot_cache.get(cache_key)
    if cached and cached[0] == mtimes:
        return cached[1]

    records = []
    seen = set()
    for path in _snapshot_files(ticker):
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        raw = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    rec = _to_record(raw, market)
                    if not rec:
                        continue
                    if target_date and rec.market_date != target_date:
                        continue
                    key = (rec.ts.isoformat(), rec.price, rec.volume)
                    if key in seen:
                        continue
                    seen.add(key)
                    records.append(rec)
        except OSError:
            continue

    records.sort(key=lambda r: r.market_ts)
    _cache_put(_snapshot_cache, cache_key, (mtimes, records), MAX_SNAPSHOT_CACHE_ITEMS)
    return records


def get_latest_snapshot_info(ticker: str, day: datetime = None) -> Optional[dict]:
    """获取最近正常交易时段快照。"""
    market = get_market(ticker)
    records = read_market_snapshots(ticker)
    if not records:
        raw = read_snapshots(ticker, day)[-1:] if read_snapshots(ticker, day) else []
        return raw[0] if raw else None
    latest = records[-1]
    return {
        "ticker": ticker,
        "timestamp": latest.ts.isoformat(),
        "price": latest.price,
        "high": latest.high,
        "low": latest.low,
        "volume": latest.volume,
        "turnover": latest.turnover,
        "change_pct": latest.change_pct,
        "market": market,
        "market_time": latest.market_ts.isoformat(),
    }


def _available_market_dates(ticker: str, before: date = None) -> list[date]:
    dates = set()
    try:
        with sqlite3.connect(get_db_path(), timeout=30) as conn:
            minute_rows = conn.execute(
                "SELECT DISTINCT market_date FROM quote_minute_bars WHERE ticker = ?",
                (ticker,),
            ).fetchall()
            snapshot_rows = conn.execute(
                "SELECT DISTINCT market_date FROM quote_snapshots WHERE ticker = ? AND market_date != ''",
                (ticker,),
            ).fetchall()
        dates.update(date.fromisoformat(row[0]) for row in minute_rows + snapshot_rows)
    except (sqlite3.Error, ValueError):
        pass

    if not dates and not _ticker_has_minute_bars(ticker):
        dates = {r.market_date for r in read_market_snapshots(ticker)}
    dates = sorted(dates)
    if before:
        dates = [d for d in dates if d < before]
    return dates


def _records_for_date(ticker: str, market_date: date) -> list[SnapshotRecord]:
    return read_market_snapshots(ticker, market_date)


def _cumulative_volume_at(records: list[SnapshotRecord], target_minute: int) -> float:
    """取截至目标市场分钟的最大累计量，抵抗重复和小幅 volume 回落。"""
    vols = [r.volume for r in records if _is_same_or_before_market_time(r, target_minute)]
    return max(vols) if vols else 0.0


def _price_at(records: list[SnapshotRecord], target_minute: int) -> Optional[SnapshotRecord]:
    best = None
    for rec in records:
        if rec.market_minutes <= target_minute:
            best = rec
        else:
            break
    return best


def _window_volume(records: list[SnapshotRecord], end_minute: int, window_minutes: int) -> float:
    market = get_market(records[0].ticker) if records and records[0].ticker else "US"
    session_start = _session_start_minutes(market, end_minute)
    start_minute = max(session_start, end_minute - window_minutes)
    end_vol = _cumulative_volume_at(records, end_minute)
    start_vol = _cumulative_volume_at(records, start_minute)
    return max(0.0, end_vol - start_vol)


def get_signal(ratio: float, current_time: datetime = None) -> str:
    """根据历史同期量比范围判断信号。"""
    if ratio <= 0:
        return "数据不足"
    if ratio < 0.6:
        return "缩量异常"
    if ratio < 0.8:
        return "缩量"
    if ratio <= 1.2:
        return "正常"
    if ratio <= 2.0:
        return "放量"
    if ratio <= 5.0:
        return "显著放量"
    return "巨量"


def calc_volume_ratio(ticker: str, current_time: datetime = None, api_vol_data: dict = None) -> tuple:
    """
    5日历史同期量比。
    返回: (ratio, today_cumulative_volume, historical_avg_volume, signal)
    """
    detail = calc_historical_ratio_detail(ticker, current_time)
    return detail["ratio"], detail["today_volume"], detail["avg_volume"], detail["signal"]


def calc_historical_ratio_detail(ticker: str, current_time: datetime = None) -> dict:
    if current_time is None:
        current_time = datetime.now()

    config = load_config()
    window = int(config.get("params", {}).get("volume_ratio_window", DEFAULT_HISTORY_DAYS))
    market = get_market(ticker)
    market_dt = _to_market_dt(current_time, market)
    market_date = market_dt.date()
    target_minute = market_dt.hour * 60 + market_dt.minute
    current_date_is_trading = is_trading_day_on(market, market_date)

    if not _is_regular_session(market, market_dt):
        all_records = read_market_snapshots(ticker)
        if all_records:
            latest = all_records[-1]
            market_date = latest.market_date
            market_dt = latest.market_ts
            target_minute = latest.market_minutes
            today_records = _records_for_date(ticker, market_date)
        else:
            today_records = []
    else:
        today_records = _records_for_date(ticker, market_date)
    if not today_records and not current_date_is_trading:
        all_records = read_market_snapshots(ticker)
        if all_records:
            latest = all_records[-1]
            market_date = latest.market_date
            market_dt = latest.market_ts
            target_minute = latest.market_minutes
            today_records = _records_for_date(ticker, market_date)

    if today_records:
        latest_today = today_records[-1]
        if not _is_regular_session(market, market_dt):
            target_minute = latest_today.market_minutes
            market_dt = latest_today.market_ts
        today_volume = _cumulative_volume_at(today_records, target_minute)
    else:
        today_volume = 0.0

    past_vols = []
    past_dates = _available_market_dates(ticker, before=market_date)
    for past_date in reversed(past_dates):
        if not is_trading_day_on(market, past_date):
            continue
        vol = _cumulative_volume_at(_records_for_date(ticker, past_date), target_minute)
        if vol > 0:
            past_vols.append(vol)
        if len(past_vols) >= window:
            break

    avg_volume = mean(past_vols) if past_vols else 0.0
    ratio = today_volume / avg_volume if avg_volume > 0 else 0.0
    signal = get_signal(ratio)
    if len(past_vols) < min(MIN_HISTORY_SAMPLES, window):
        signal = f"样本不足({len(past_vols)}/{window})"

    return {
        "ratio": round(ratio, 4),
        "today_volume": today_volume,
        "avg_volume": avg_volume,
        "signal": signal,
        "sample_days": len(past_vols),
        "market_date": market_date.isoformat(),
        "market_time": market_dt.strftime("%H:%M"),
        "target_minute": target_minute,
        "quality": "ok" if today_volume > 0 and past_vols else "数据不足",
    }


def calc_intraday_ratio(ticker: str, current_time: datetime = None) -> tuple:
    """
    日内滚动量比：最近 W 分钟 vs 今天前 B 分钟每 W 分钟均量。
    返回: (ratio, signal_name, cond_vol, cond_stop, cond_stable)
    """
    detail = calc_intraday_ratio_detail(ticker, current_time)
    return (
        detail["ratio"],
        detail["signal"],
        detail["cond_vol"],
        detail["cond_stop"],
        detail["cond_stable"],
    )


def calc_intraday_ratio_detail(ticker: str, current_time: datetime = None) -> dict:
    if current_time is None:
        current_time = datetime.now()

    config = load_config()
    params = config.get("params", {})
    signal_window = int(params.get("intraday_signal_window_minutes", INTRADAY_SIGNAL_WINDOW_MINUTES))
    baseline_minutes = int(params.get("intraday_baseline_minutes", INTRADAY_BASELINE_MINUTES))
    baseline_method = params.get("intraday_baseline_method", "mean")

    market = get_market(ticker)
    market_dt = _to_market_dt(current_time, market)
    market_date = market_dt.date()
    target_minute = market_dt.hour * 60 + market_dt.minute
    current_date_is_trading = is_trading_day_on(market, market_date)
    if not current_date_is_trading:
        return _empty_intraday_detail("休市")

    if not _is_regular_session(market, market_dt):
        all_records = read_market_snapshots(ticker)
        if all_records:
            latest = all_records[-1]
            market_date = latest.market_date
            market_dt = latest.market_ts
            target_minute = latest.market_minutes
            records = _records_for_date(ticker, market_date)
        else:
            records = []
    else:
        records = _records_for_date(ticker, market_date)
    if not records:
        return _empty_intraday_detail("数据不足")

    if not _is_regular_session(market, market_dt):
        target_minute = records[-1].market_minutes

    session_start = _session_start_minutes(market, target_minute)
    if target_minute - session_start < signal_window + signal_window:
        return _empty_intraday_detail("数据不足")

    signal_volume = _window_volume(records, target_minute, signal_window)
    baseline_end = target_minute - signal_window
    baseline_start = max(session_start, baseline_end - baseline_minutes)
    baseline_vols = []
    cursor = baseline_start + signal_window
    while cursor <= baseline_end:
        vol = _window_volume(records, cursor, signal_window)
        if vol > 0:
            baseline_vols.append(vol)
        cursor += signal_window

    expected_baseline_samples = max(1, (baseline_end - baseline_start) // signal_window)
    min_baseline_samples = max(2, min(3, expected_baseline_samples))
    if signal_volume <= 0 or len(baseline_vols) < min_baseline_samples:
        return _empty_intraday_detail("数据不足", signal_volume=signal_volume)

    baseline_volume = median(baseline_vols) if baseline_method == "median" else mean(baseline_vols)
    ratio = signal_volume / baseline_volume if baseline_volume > 0 else 0.0
    cond_vol = ratio > float(params.get("intraday_alert_threshold", 1.5))

    signal_recs = [r for r in records if target_minute - signal_window <= r.market_minutes <= target_minute]
    baseline_recs = [r for r in records if baseline_start <= r.market_minutes < baseline_end]
    cond_stop = False
    cond_stable = False
    if signal_recs and baseline_recs:
        base_prices = [(r.low or r.price) for r in baseline_recs if (r.low or r.price) > 0]
        sig_prices = [(r.low or r.price) for r in signal_recs if (r.low or r.price) > 0]
        latest_price = signal_recs[-1].price
        if base_prices and sig_prices and latest_price > 0:
            base_low = min(base_prices)
            sig_low = min(sig_prices)
            cond_stop = sig_low >= base_low * 0.995
            cond_stable = latest_price > sig_low * 1.005

    if cond_vol and cond_stop and cond_stable:
        signal = "放量止跌"
    elif cond_vol:
        signal = "放量"
    else:
        signal = ""

    return {
        "ratio": round(ratio, 2),
        "signal": signal,
        "cond_vol": cond_vol,
        "cond_stop": cond_stop,
        "cond_stable": cond_stable,
        "window_volume": signal_volume,
        "baseline_volume": baseline_volume,
        "baseline_samples": len(baseline_vols),
    }


def _empty_intraday_detail(signal: str, signal_volume: float = 0.0) -> dict:
    return {
        "ratio": 0.0,
        "signal": signal,
        "cond_vol": False,
        "cond_stop": False,
        "cond_stable": False,
        "window_volume": signal_volume,
        "baseline_volume": 0.0,
        "baseline_samples": 0,
    }


def _get_market_now(market: str) -> datetime:
    return datetime.now(_market_tz(market))


def get_signal_detail(ratio: float, price_change: float = 0, market: str = "CN") -> str:
    """获取历史同期量比信号详情。"""
    if ratio > 2.0 and price_change > 2:
        return "放量突破"
    if ratio > 2.0 and price_change < -2:
        return "放量下跌"
    if ratio < 0.6 and price_change > 0:
        return "缩量止跌"
    if ratio > 1.5 and market == "CN":
        now = _get_market_now(market)
        if (now.hour == 14 and now.minute >= 30) or now.hour == 15:
            return "尾盘放量"
    return ""


def get_db_path() -> Path:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return DB_PATH


def init_db():
    """初始化 SQLite。v3 在 v2 基础上无损增加分钟聚合表。"""
    global _db_initialized
    if _db_initialized:
        return

    db_path = get_db_path()
    with sqlite3.connect(db_path, timeout=30) as conn:
        current_version = _get_schema_version(conn)
        if current_version not in (2, SCHEMA_VERSION):
            conn.execute("DROP TABLE IF EXISTS volume_ratios")
            conn.execute("DROP TABLE IF EXISTS quote_snapshots")
            conn.execute("DROP TABLE IF EXISTS quote_minute_bars")
            conn.execute("DROP TABLE IF EXISTS signals")
            conn.execute("DROP TABLE IF EXISTS signal_states")
            conn.execute("DROP TABLE IF EXISTS schema_meta")
            conn.execute("CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            conn.execute(
                "INSERT INTO schema_meta (key, value) VALUES ('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
        elif current_version == 2:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS schema_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
            """)
            conn.execute(
                """
                INSERT INTO schema_meta (key, value) VALUES ('schema_version', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (str(SCHEMA_VERSION),),
            )

        conn.execute("""
            CREATE TABLE IF NOT EXISTS quote_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL,
                market TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                market_timestamp TEXT,
                market_date TEXT,
                price REAL,
                open REAL,
                high REAL,
                low REAL,
                volume REAL,
                turnover REAL,
                change REAL,
                change_pct REAL,
                source TEXT DEFAULT 'websocket',
                UNIQUE(ticker, timestamp, volume, price)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS quote_minute_bars (
                ticker TEXT NOT NULL,
                market TEXT NOT NULL,
                market_date TEXT NOT NULL,
                market_minute INTEGER NOT NULL,
                market_timestamp TEXT NOT NULL,
                first_timestamp TEXT NOT NULL,
                last_timestamp TEXT NOT NULL,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume REAL,
                turnover REAL,
                change_pct REAL,
                source TEXT DEFAULT 'websocket',
                updated_at TEXT NOT NULL,
                PRIMARY KEY(ticker, market_date, market_minute)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS volume_ratios (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL,
                name TEXT,
                timestamp TEXT NOT NULL,
                market TEXT,
                market_timestamp TEXT,
                market_date TEXT,
                price REAL,
                change_pct REAL,
                historical_ratio REAL,
                historical_today_volume REAL,
                historical_avg_volume REAL,
                historical_sample_days INTEGER,
                historical_signal TEXT,
                intraday_ratio REAL,
                intraday_window_volume REAL,
                intraday_baseline_volume REAL,
                intraday_baseline_samples INTEGER,
                intraday_signal TEXT,
                cond_vol INTEGER DEFAULT 0,
                cond_stop INTEGER DEFAULT 0,
                cond_stable INTEGER DEFAULT 0,
                data_quality TEXT,
                UNIQUE(ticker, timestamp)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL,
                name TEXT,
                timestamp TEXT NOT NULL,
                signal_type TEXT NOT NULL,
                ratio REAL,
                price REAL,
                change_pct REAL,
                source TEXT,
                llm_analysis TEXT,
                notified INTEGER DEFAULT 1
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS signal_states (
                ticker TEXT PRIMARY KEY,
                state TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS llm_calls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                model TEXT,
                success INTEGER DEFAULT 1
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_quote_snapshots_ticker_time ON quote_snapshots(ticker, timestamp)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_quote_snapshots_market_date ON quote_snapshots(market, market_date)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_quote_minute_bars_ticker_date ON quote_minute_bars(ticker, market_date, market_minute)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_quote_minute_bars_market_date ON quote_minute_bars(market, market_date)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_volume_ratios_ticker ON volume_ratios(ticker)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_volume_ratios_timestamp ON volume_ratios(timestamp)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_signals_timestamp ON signals(timestamp)")
    _db_initialized = True


def _get_schema_version(conn: sqlite3.Connection) -> int:
    try:
        row = conn.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()
        return int(row[0]) if row else 0
    except sqlite3.Error:
        return 0


def _as_float(value, default: float = 0.0) -> float:
    try:
        return float(value or default)
    except (TypeError, ValueError):
        return default


def _clear_bar_caches(ticker: str):
    for key in list(_minute_bar_cache.keys()):
        if key[0] == ticker:
            del _minute_bar_cache[key]
    for key in list(_snapshot_cache.keys()):
        if key[0] == ticker:
            del _snapshot_cache[key]
    _cache_put(_minute_bar_presence_cache, ticker, True, MAX_PRESENCE_CACHE_ITEMS)


def save_quote_minute_bar(
    ticker: str,
    data: dict,
    source: str = "websocket",
    conn: sqlite3.Connection = None,
):
    """保存一分钟一条的累计量快照，供量比计算快速读取。"""
    market = get_market(ticker)
    ts = parse_timestamp(data.get("timestamp", ""))
    if not ts:
        return
    market_ts = _to_market_dt(ts, market)
    if not _is_regular_session(market, market_ts):
        return

    price = _as_float(data.get("price"))
    volume = _as_float(data.get("volume"))
    if price <= 0 or volume < 0:
        return

    minute_ts = market_ts.replace(second=0, microsecond=0)
    market_minute = market_ts.hour * 60 + market_ts.minute
    high = _as_float(data.get("high"), price)
    low = _as_float(data.get("low"), price)
    if high <= 0:
        high = price
    if low <= 0:
        low = price
    now = datetime.now().isoformat()
    params = (
        ticker,
        market,
        market_ts.date().isoformat(),
        market_minute,
        minute_ts.isoformat(),
        ts.isoformat(),
        ts.isoformat(),
        price,
        high,
        low,
        price,
        volume,
        _as_float(data.get("turnover")),
        _as_float(data.get("change_pct")),
        source,
        now,
    )

    def execute(target: sqlite3.Connection):
        target.execute("""
            INSERT INTO quote_minute_bars
            (ticker, market, market_date, market_minute, market_timestamp,
             first_timestamp, last_timestamp, open, high, low, close, volume,
             turnover, change_pct, source, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(ticker, market_date, market_minute) DO UPDATE SET
                last_timestamp = CASE
                    WHEN excluded.last_timestamp >= quote_minute_bars.last_timestamp
                    THEN excluded.last_timestamp ELSE quote_minute_bars.last_timestamp END,
                close = CASE
                    WHEN excluded.last_timestamp >= quote_minute_bars.last_timestamp
                    THEN excluded.close ELSE quote_minute_bars.close END,
                high = MAX(quote_minute_bars.high, excluded.high),
                low = MIN(quote_minute_bars.low, excluded.low),
                volume = MAX(quote_minute_bars.volume, excluded.volume),
                turnover = CASE
                    WHEN excluded.turnover >= quote_minute_bars.turnover
                    THEN excluded.turnover ELSE quote_minute_bars.turnover END,
                change_pct = CASE
                    WHEN excluded.last_timestamp >= quote_minute_bars.last_timestamp
                    THEN excluded.change_pct ELSE quote_minute_bars.change_pct END,
                source = excluded.source,
                updated_at = excluded.updated_at
        """, params)

    try:
        if conn is None:
            init_db()
            with sqlite3.connect(get_db_path(), timeout=30) as own_conn:
                execute(own_conn)
        else:
            execute(conn)
        _clear_bar_caches(ticker)
    except sqlite3.Error:
        if conn is not None:
            try:
                conn.rollback()
            except sqlite3.Error:
                pass


def save_quote_snapshot(ticker: str, data: dict, source: str = "websocket"):
    """按 WebSocket/REST 返回结构保存原始快照，并同步更新分钟聚合表。"""
    init_db()
    market = get_market(ticker)
    ts = parse_timestamp(data.get("timestamp", ""))
    market_ts = _to_market_dt(ts, market) if ts else None
    try:
        with sqlite3.connect(get_db_path(), timeout=30) as conn:
            conn.execute("""
                INSERT OR IGNORE INTO quote_snapshots
                (ticker, market, timestamp, market_timestamp, market_date, price, open, high, low,
                 volume, turnover, change, change_pct, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                ticker,
                market,
                data.get("timestamp", ""),
                market_ts.isoformat() if market_ts else "",
                market_ts.date().isoformat() if market_ts else "",
                float(data.get("price", 0) or 0),
                float(data.get("open", 0) or 0),
                float(data.get("high", 0) or 0),
                float(data.get("low", 0) or 0),
                float(data.get("volume", 0) or 0),
                float(data.get("turnover", 0) or 0),
                float(data.get("change", 0) or 0),
                float(data.get("change_pct", 0) or 0),
                source,
            ))
            save_quote_minute_bar(ticker, data, source=source, conn=conn)
    except sqlite3.Error:
        pass


def save_ratio(result: dict):
    """保存两套量比结果（每 ticker 每 5 分钟写一次）。"""
    now = datetime.now()
    ticker = result["ticker"]
    last_write = _last_ratio_write.get(ticker)
    if last_write and (now - last_write).total_seconds() < RATIO_WRITE_INTERVAL:
        return

    init_db()
    try:
        with sqlite3.connect(get_db_path(), timeout=30) as conn:
            conn.execute("""
                INSERT INTO volume_ratios
                (ticker, name, timestamp, market, market_timestamp, market_date, price, change_pct,
                 historical_ratio, historical_today_volume, historical_avg_volume, historical_sample_days,
                 historical_signal, intraday_ratio, intraday_window_volume, intraday_baseline_volume,
                 intraday_baseline_samples, intraday_signal, cond_vol, cond_stop, cond_stable, data_quality)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                ticker,
                result.get("name", ticker),
                now.isoformat(),
                result.get("market", ""),
                result.get("market_time", ""),
                result.get("market_date", ""),
                result.get("price", 0),
                result.get("change_pct", 0),
                result.get("ratio", 0),
                result.get("volume_today", 0),
                result.get("volume_avg5", 0),
                result.get("historical_sample_days", 0),
                result.get("signal", ""),
                result.get("ratio_intraday", 0),
                result.get("intraday_window_volume", 0),
                result.get("intraday_baseline_volume", 0),
                result.get("intraday_baseline_samples", 0),
                result.get("signal_intraday", ""),
                int(bool(result.get("cond_vol", False))),
                int(bool(result.get("cond_stop", False))),
                int(bool(result.get("cond_stable", False))),
                result.get("data_quality", ""),
            ))
        _last_ratio_write[ticker] = now
    except sqlite3.Error:
        pass


def save_signal(ticker: str, name: str, signal_type: str, ratio: float,
                price: float, change_pct: float, source: str = "",
                llm_analysis: str = "", notified: int = 1):
    init_db()
    now = datetime.now().isoformat()
    try:
        with sqlite3.connect(get_db_path(), timeout=30) as conn:
            conn.execute("""
                INSERT INTO signals
                (ticker, name, timestamp, signal_type, ratio, price, change_pct, source, llm_analysis, notified)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (ticker, name, now, signal_type, ratio, price, change_pct, source, llm_analysis, notified))
    except sqlite3.Error:
        pass


def get_latest_snapshot(ticker: str) -> Optional[dict]:
    return get_latest_snapshot_info(ticker, datetime.now())


def compute_all() -> List[dict]:
    config = load_config()
    tickers = get_all_tickers(config)
    api_data = _fetch_price_from_api(tickers)
    results = []
    for ticker in tickers:
        result = compute_ticker(ticker, api_data=api_data)
        if result:
            results.append(result)
    return results


def _fetch_price_from_api(tickers: list) -> dict:
    """从长桥 API 批量获取最新价格；量比本身不使用日 K 全日量。"""
    if not tickers:
        return {}
    try:
        from longbridge.openapi import OAuthBuilder, Config, QuoteContext
        token_dir = Path.home() / ".longbridge" / "openapi" / "tokens"
        files = list(token_dir.iterdir())
        if not files:
            return {}
        cid = files[0].name
        with suppress_stdout():
            oauth = OAuthBuilder(cid).build(lambda url: None)
            config = Config.from_oauth(oauth)
            ctx = QuoteContext(config)
            quotes = ctx.quote(tickers)
        result = {}
        for q in quotes:
            last = float(q.last_done or 0)
            prev = float(q.prev_close or 0)
            change_pct = round((last - prev) / prev * 100, 2) if prev > 0 else None
            result[q.symbol] = {"price": last, "change_pct": change_pct, "volume": int(q.volume or 0)}
        return result
    except BaseException as e:
        if isinstance(e, (KeyboardInterrupt, SystemExit)):
            raise
        print(f"[compute] API 价格获取失败: {e}", flush=True)
        return {}


def compute_ticker(ticker: str, api_data: dict = None, api_vol_data: dict = None) -> Optional[dict]:
    market = get_market(ticker)
    config = load_config()
    name = get_ticker_name(config, ticker)

    historical = calc_historical_ratio_detail(ticker)
    intraday = calc_intraday_ratio_detail(ticker)
    snapshot = get_latest_snapshot(ticker)

    price = snapshot.get("price", 0) if snapshot else 0
    change_pct = snapshot.get("change_pct", 0) if snapshot else 0

    if api_data is None:
        api_data = _fetch_price_from_api([ticker])
    if ticker in api_data:
        price = api_data[ticker]["price"] or price
        if api_data[ticker].get("change_pct") is not None:
            change_pct = api_data[ticker]["change_pct"]

    signal = historical["signal"]
    if not is_trading_day(market):
        signal = "休市"

    signal_detail = "" if signal == "休市" else get_signal_detail(historical["ratio"], change_pct, market)

    result = {
        "ticker": ticker,
        "name": name,
        "market": market,
        "market_date": historical["market_date"],
        "market_time": historical["market_time"],
        "ratio": round(historical["ratio"], 2),
        "ratio_intraday": intraday["ratio"],
        "volume_today": historical["today_volume"],
        "volume_avg5": round(historical["avg_volume"], 2),
        "historical_sample_days": historical["sample_days"],
        "price": price,
        "change_pct": round(change_pct, 2),
        "signal": signal,
        "signal_detail": signal_detail,
        "signal_intraday": intraday["signal"],
        "cond_vol": intraday["cond_vol"],
        "cond_stop": intraday["cond_stop"],
        "cond_stable": intraday["cond_stable"],
        "intraday_window_volume": intraday["window_volume"],
        "intraday_baseline_volume": round(intraday["baseline_volume"], 2),
        "intraday_baseline_samples": intraday["baseline_samples"],
        "data_quality": historical["quality"],
    }

    save_ratio(result)
    return result


if __name__ == "__main__":
    init_db()
    if len(sys.argv) > 1:
        ticker = sys.argv[1]
        result = compute_ticker(ticker)
        if result:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(f"无法计算 {ticker} 的量比", file=sys.stderr)
            sys.exit(1)
    else:
        print(json.dumps(compute_all(), ensure_ascii=False, indent=2))
