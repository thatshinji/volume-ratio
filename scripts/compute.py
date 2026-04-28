#!/usr/bin/env python3
"""
量比计算引擎 - 计算实时量比并判断信号
量比 = 最近N分钟真实成交量 / 过去5日同一时段均量
盘中量比 = (当前累计量 - N分钟前累计量) / 历史同期差分量均值
"""

import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List

ROOT = Path(__file__).parent.parent
CONFIG_PATH = ROOT / "config.yaml"
SNAPSHOT_DIR = ROOT / "data" / "snapshots"
DB_PATH = ROOT / "data" / "ratios.db"


def load_config() -> dict:
    import yaml
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_market(ticker: str) -> str:
    if ticker.endswith(".US"):
        return "US"
    elif ticker.endswith(".HK"):
        return "HK"
    elif ticker.endswith(".SH") or ticker.endswith(".SZ"):
        return "CN"
    return "US"


def parse_timestamp(ts: str) -> Optional[datetime]:
    """解析 ISO timestamp 为 datetime 对象"""
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace('Z', '+00:00'))
    except (ValueError, AttributeError):
        return None


def get_market_dir(ticker: str) -> Path:
    market = get_market(ticker)
    return SNAPSHOT_DIR / market


def get_ticker_prefix(ticker: str) -> str:
    return ticker.replace('.', '_')


def list_snapshots(ticker: str, day: datetime) -> List[Path]:
    """列出指定日期的所有快照文件，按时间排序"""
    market_dir = get_market_dir(ticker)
    if not market_dir.exists():
        return []

    day_str = day.strftime("%Y%m%d")
    prefix = get_ticker_prefix(ticker)
    snapshots = []

    for f in market_dir.iterdir():
        if not f.name.startswith(prefix):
            continue
        if day_str not in f.name:
            continue
        snapshots.append(f)

    snapshots.sort()
    return snapshots


def get_latest_snapshot_info(ticker: str, day: datetime = None) -> Optional[dict]:
    """获取指定日期最新的快照数据（解析后的 dict）"""
    if day is None:
        day = datetime.now()

    snapshots = list_snapshots(ticker, day)
    if not snapshots:
        return None

    latest_file = snapshots[-1]
    try:
        with open(latest_file, "r", encoding="utf-8") as fp:
            return json.load(fp)
    except (json.JSONDecodeError, OSError):
        return None


def get_snapshot_n_minutes_ago(ticker: str, day: datetime, n: int = 5) -> Optional[dict]:
    """
    获取指定日期 n 分钟前的快照
    用于计算 interval volume = latest - n_minutes_ago
    """
    snapshots = list_snapshots(ticker, day)
    if not snapshots:
        return None

    target_time = day - timedelta(minutes=n)
    # 找到最接近但不超过 target_time 的快照
    best = None
    for f in snapshots:
        try:
            with open(f, "r", encoding="utf-8") as fp:
                data = json.load(fp)
                ts = parse_timestamp(data.get("timestamp", ""))
                if ts and ts <= target_time:
                    best = (ts, data)
                elif ts and ts > target_time:
                    break
        except (json.JSONDecodeError, OSError):
            continue

    return best[1] if best else None


def get_interval_volume(ticker: str, day: datetime, window_minutes: int = 5) -> float:
    """
    计算指定日期指定时间窗口内的真实成交量（差分量）
    interval_vol = 最新快照累计量 - window_minutes 前的快照累计量
    """
    latest = get_latest_snapshot_info(ticker, day)
    if not latest:
        return 0.0

    ago = get_snapshot_n_minutes_ago(ticker, day, window_minutes)
    if not ago:
        return 0.0

    latest_vol = float(latest.get("volume", 0))
    ago_vol = float(ago.get("volume", 0))

    return max(0.0, latest_vol - ago_vol)


def get_today_volume(ticker: str, current_time: datetime = None) -> float:
    """获取今日同时段真实成交量（差分量）"""
    if current_time is None:
        current_time = datetime.now()

    config = load_config()
    window = config.get("params", {}).get("volume_ratio_window", 5)

    return get_interval_volume(ticker, current_time, window)


def get_day_volume(ticker: str, day: datetime) -> float:
    """获取指定日期同时段的真实成交量"""
    config = load_config()
    window = config.get("params", {}).get("volume_ratio_window", 5)

    return get_interval_volume(ticker, day, window)


def get_db_path() -> Path:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return DB_PATH


def init_db():
    """初始化 SQLite 数据库"""
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS volume_ratios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            ratio REAL,
            volume_today REAL,
            volume_avg5 REAL,
            price REAL,
            change_pct REAL,
            signal TEXT,
            UNIQUE(ticker, timestamp)
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS daily_summary (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            date TEXT NOT NULL,
            avg_ratio REAL,
            max_ratio REAL,
            min_ratio REAL,
            final_price REAL,
            final_change_pct REAL,
            signal TEXT,
            UNIQUE(ticker, date)
        )
    """)
    conn.commit()
    conn.close()


def calc_volume_ratio(ticker: str, current_time: datetime = None) -> tuple:
    """
    计算量比
    返回: (ratio, today_vol, avg_5d_vol, signal)
    """
    if current_time is None:
        current_time = datetime.now()

    config = load_config()
    window = config.get("params", {}).get("volume_ratio_window", 5)

    today_vol = get_today_volume(ticker, current_time)
    past_vols = []

    for i in range(1, window + 1):
        past_day = current_time - timedelta(days=i)
        vol = get_day_volume(ticker, past_day)
        if vol > 0:
            past_vols.append(vol)

    if not past_vols:
        return 0.0, today_vol, 0.0, "数据不足"

    avg_5d_vol = sum(past_vols) / len(past_vols)
    ratio = today_vol / avg_5d_vol if avg_5d_vol > 0 else 0.0

    signal = get_signal(ratio, current_time)

    return ratio, today_vol, avg_5d_vol, signal


def get_signal(ratio: float, current_time: datetime = None) -> str:
    """根据量比范围判断信号"""
    if ratio < 0.6:
        return "缩量异常"
    elif ratio < 0.8:
        return "缩量"
    elif ratio <= 1.2:
        return "正常"
    elif ratio <= 2.0:
        return "放量"
    elif ratio <= 5.0:
        return "显著放量"
    else:
        return "巨量"


BASELINE_WINDOW = 10  # 基线窗口：最近 10 个间隔（约5分钟）
SIGNAL_WINDOW = 5     # 信号窗口：最近 5 个间隔（约2.5分钟）
MIN_RECORDS = BASELINE_WINDOW + 3  # 最少需要 13 条记录才能检测


def calc_intraday_ratio(ticker: str, current_time: datetime = None) -> tuple:
    """
    日内滚动量比：三条件放量止跌检测
    基于 monitor_stop_signal.py 逻辑

    返回: (ratio, signal_name, cond_vol, cond_stop, cond_stable)
    """
    if current_time is None:
        current_time = datetime.now()

    snapshots = list_snapshots(ticker, current_time)
    if len(snapshots) < MIN_RECORDS:
        return 0.0, "数据不足", False, False, False

    # 读取所有快照，构建 real_vol 序列
    records = []
    prev_vol = None
    for f in snapshots:
        try:
            with open(f, "r", encoding="utf-8") as fp:
                data = json.load(fp)
            vol = float(data.get("volume", 0) or 0)
            price = float(data.get("price", 0) or 0)
            low = float(data.get("low", 0) or 0)
            ts = parse_timestamp(data.get("timestamp", ""))

            if prev_vol is not None:
                real_vol = max(0, vol - prev_vol)
                records.append({
                    "ts": ts,
                    "price": price,
                    "real_vol": real_vol,
                    "low": low,
                })
            prev_vol = vol
        except (json.JSONDecodeError, OSError):
            continue

    if len(records) < MIN_RECORDS:
        return 0.0, "数据不足", False, False, False

    # 分割基线和信号窗口
    baseline_recs = records[-(SIGNAL_WINDOW + BASELINE_WINDOW) : -SIGNAL_WINDOW]
    signal_recs = records[-SIGNAL_WINDOW:]

    base_vols = [r["real_vol"] for r in baseline_recs if r["real_vol"] > 0]
    sig_vols = [r["real_vol"] for r in signal_recs if r["real_vol"] > 0]

    if not base_vols or not sig_vols:
        return 0.0, "数据不足", False, False, False

    avg_base_vol = sum(base_vols) / len(base_vols)
    avg_sig_vol = sum(sig_vols) / len(sig_vols)
    vol_ratio = avg_sig_vol / avg_base_vol if avg_base_vol > 0 else 0.0

    # 条件 1: 放量
    cond_vol = vol_ratio > 1.5

    # 条件 2: 止跌（信号期最低价 >= 基线最低价 × 0.995）
    base_min_price = min(r["low"] for r in baseline_recs)
    sig_min_low = min(r["low"] for r in signal_recs)
    cond_stop = sig_min_low >= base_min_price * 0.995

    # 条件 3: 企稳（最新价 > 信号期最低价 × 1.005）
    sig_prices = [r["price"] for r in signal_recs]
    sig_min_price = min(sig_prices)
    latest_price = sig_prices[-1]
    cond_stable = latest_price > sig_min_price * 1.005

    # 信号名称
    if cond_vol and cond_stop and cond_stable:
        signal_name = "放量止跌"
    elif cond_vol:
        signal_name = "放量"
    else:
        signal_name = ""

    return round(vol_ratio, 2), signal_name, cond_vol, cond_stop, cond_stable


def get_signal_detail(ratio: float, price_change: float = 0) -> str:
    """获取信号详情"""
    if ratio > 2.0 and price_change > 2:
        return "放量突破"
    elif ratio > 2.0 and price_change < -2:
        return "放量下跌"
    elif ratio < 0.6 and price_change > 0:
        return "缩量止跌"
    elif ratio > 1.5:
        hour = datetime.now().hour
        minute = datetime.now().minute
        if hour == 14 and minute >= 30 or hour == 15:
            return "尾盘放量"
    return ""


def save_ratio(ticker: str, ratio: float, volume_today: float, volume_avg5: float,
               price: float, change_pct: float, signal: str):
    """保存量比到数据库"""
    init_db()
    conn = sqlite3.connect(get_db_path())
    c = conn.cursor()
    now = datetime.now().isoformat()

    try:
        c.execute("""
            INSERT INTO volume_ratios
            (ticker, timestamp, ratio, volume_today, volume_avg5, price, change_pct, signal)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (ticker, now, ratio, volume_today, volume_avg5, price, change_pct, signal))
        conn.commit()
    except sqlite3.IntegrityError:
        pass
    conn.close()


def get_latest_snapshot(ticker: str) -> Optional[dict]:
    """获取最新行情快照"""
    return get_latest_snapshot_info(ticker, datetime.now())


def compute_all() -> List[dict]:
    """计算所有监控标的的量比"""
    config = load_config()
    watchlist = config.get("watchlist", {})

    results = []
    for market in ["us", "hk", "cn"]:
        for ticker in watchlist.get(market, []):
            result = compute_ticker(ticker)
            if result:
                results.append(result)
    return results


def compute_ticker(ticker: str) -> Optional[dict]:
    """计算单个标的的量比"""
    snapshot = get_latest_snapshot(ticker)

    ratio, today_vol, avg_vol, signal = calc_volume_ratio(ticker)
    intraday_ratio, signal_intraday, cond_vol, cond_stop, cond_stable = calc_intraday_ratio(ticker)

    price = snapshot.get("price", 0) if snapshot else 0
    change_pct = snapshot.get("change_pct", 0) if snapshot else 0

    result = {
        "ticker": ticker,
        "ratio": round(ratio, 2),
        "ratio_intraday": intraday_ratio,
        "volume_today": today_vol,
        "volume_avg5": round(avg_vol, 2),
        "price": price,
        "change_pct": round(change_pct, 2),
        "signal": signal,
        "signal_detail": get_signal_detail(ratio, change_pct),
        "signal_intraday": signal_intraday,
        "cond_vol": cond_vol,
        "cond_stop": cond_stop,
        "cond_stable": cond_stable,
    }

    save_ratio(ticker, ratio, today_vol, avg_vol, price, change_pct, signal)

    return result


if __name__ == "__main__":
    if len(sys.argv) > 1:
        ticker = sys.argv[1]
        result = compute_ticker(ticker)
        if result:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(f"无法计算 {ticker} 的量比", file=sys.stderr)
            sys.exit(1)
    else:
        results = compute_all()
        print(json.dumps(results, ensure_ascii=False, indent=2))