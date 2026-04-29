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
SNAPSHOT_DIR = ROOT / "data" / "snapshots"
DB_PATH = ROOT / "data" / "ratios.db"

# 将 scripts/ 加入 sys.path
sys.path.insert(0, str(ROOT / "scripts"))

from core.config import load_config
from core.market import get_market, get_all_tickers, get_ticker_name


def parse_timestamp(ts: str) -> Optional[datetime]:
    """解析 ISO timestamp 为 datetime 对象"""
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace('Z', '+00:00'))
    except (ValueError, AttributeError):
        return None


def get_jsonl_path(ticker: str, day: datetime = None) -> Path:
    """获取 JSONL 文件路径"""
    if day is None:
        day = datetime.now()
    market = get_market(ticker)
    market_dir = SNAPSHOT_DIR / market
    day_str = day.strftime("%Y%m%d")
    filename = f"{ticker.replace('.', '_')}_{day_str}.jsonl"
    return market_dir / filename


def read_snapshots(ticker: str, day: datetime = None) -> List[dict]:
    """从 JSONL 文件读取指定日期的所有快照数据"""
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


def get_latest_snapshot_info(ticker: str, day: datetime = None) -> Optional[dict]:
    """获取指定日期最新的快照数据"""
    records = read_snapshots(ticker, day)
    return records[-1] if records else None


def get_snapshot_n_minutes_ago(ticker: str, day: datetime, n: int = 5) -> Optional[dict]:
    """
    获取指定日期 n 分钟前的快照
    用于计算 interval volume = latest - n_minutes_ago
    """
    records = read_snapshots(ticker, day)
    if not records:
        return None

    target_time = day - timedelta(minutes=n)
    best = None
    for data in records:
        ts = parse_timestamp(data.get("timestamp", ""))
        if ts and ts <= target_time:
            best = data
        elif ts and ts > target_time:
            break

    return best


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


# 数据库初始化标志
_db_initialized = False


def init_db():
    """初始化 SQLite 数据库（只执行一次）"""
    global _db_initialized
    if _db_initialized:
        return

    db_path = get_db_path()
    with sqlite3.connect(db_path, timeout=30) as conn:
        conn.execute("""
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
            CREATE TABLE IF NOT EXISTS llm_calls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                model TEXT,
                success INTEGER DEFAULT 1
            )
        """)
    _db_initialized = True


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

    raw_snapshots = read_snapshots(ticker, current_time)
    if len(raw_snapshots) < MIN_RECORDS:
        return 0.0, "数据不足", False, False, False

    # 构建 real_vol 序列（差分量）
    records = []
    prev_vol = None
    for data in raw_snapshots:
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
    db_path = get_db_path()
    now = datetime.now().isoformat()

    try:
        with sqlite3.connect(db_path, timeout=30) as conn:
            conn.execute("""
                INSERT INTO volume_ratios
                (ticker, timestamp, ratio, volume_today, volume_avg5, price, change_pct, signal)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (ticker, now, ratio, volume_today, volume_avg5, price, change_pct, signal))
    except sqlite3.IntegrityError:
        pass


def save_signal(ticker: str, name: str, signal_type: str, ratio: float,
                price: float, change_pct: float, source: str = "",
                llm_analysis: str = "", notified: int = 1):
    """保存信号记录到 signals 表"""
    init_db()
    db_path = get_db_path()
    now = datetime.now().isoformat()

    try:
        with sqlite3.connect(db_path, timeout=30) as conn:
            conn.execute("""
                INSERT INTO signals
                (ticker, name, timestamp, signal_type, ratio, price, change_pct, source, llm_analysis, notified)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (ticker, name, now, signal_type, ratio, price, change_pct, source, llm_analysis, notified))
    except sqlite3.Error:
        pass


def get_latest_snapshot(ticker: str) -> Optional[dict]:
    """获取最新行情快照"""
    return get_latest_snapshot_info(ticker, datetime.now())


def compute_all() -> List[dict]:
    """计算所有监控标的的量比"""
    config = load_config()
    tickers = get_all_tickers(config)

    results = []
    for ticker in tickers:
        result = compute_ticker(ticker)
        if result:
            results.append(result)

    # 对价格为 0 的标的，批量从 API 获取最新价格
    zero_tickers = [r["ticker"] for r in results if r.get("price", 0) == 0]
    if zero_tickers:
        api_prices = _fetch_price_from_api(zero_tickers)
        for r in results:
            if r["ticker"] in api_prices:
                r["price"] = api_prices[r["ticker"]]["price"]
                r["change_pct"] = api_prices[r["ticker"]]["change_pct"]

    return results


def _fetch_price_from_api(tickers: list) -> dict:
    """从长桥 API 批量获取最新价格（用于无快照数据的标的）"""
    if not tickers:
        return {}
    try:
        import io
        from longbridge.openapi import OAuthBuilder, Config, QuoteContext
        token_dir = Path.home() / ".longbridge" / "openapi" / "tokens"
        files = list(token_dir.iterdir())
        if not files:
            return {}
        cid = files[0].name
        # 抑制 SDK 的 stdout 输出（SDK 可能直接写 fd 1）
        old_stdout_fd = os.dup(1)
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, 1)
        os.close(devnull)
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            oauth = OAuthBuilder(cid).build(lambda url: None)
            config = Config.from_oauth(oauth)
            ctx = QuoteContext(config)
            quotes = ctx.quote(tickers)
        finally:
            sys.stdout = old_stdout
            os.dup2(old_stdout_fd, 1)
            os.close(old_stdout_fd)
        result = {}
        for q in quotes:
            last = float(q.last_done or 0)
            prev = float(q.prev_close or 0)
            change_pct = ((last - prev) / prev * 100) if prev > 0 else 0
            result[q.symbol] = {"price": last, "change_pct": round(change_pct, 2)}
        return result
    except Exception as e:
        try:
            sys.stdout = old_stdout
            os.dup2(old_stdout_fd, 1)
            os.close(old_stdout_fd)
        except Exception:
            pass
        print(f"[compute] API 价格获取失败: {e}", flush=True)
        return {}


def compute_ticker(ticker: str) -> Optional[dict]:
    """计算单个标的的量比"""
    snapshot = get_latest_snapshot(ticker)

    ratio, today_vol, avg_vol, signal = calc_volume_ratio(ticker)
    intraday_ratio, signal_intraday, cond_vol, cond_stop, cond_stable = calc_intraday_ratio(ticker)

    price = snapshot.get("price", 0) if snapshot else 0
    change_pct = snapshot.get("change_pct", 0) if snapshot else 0

    config = load_config()
    name = get_ticker_name(config, ticker)

    result = {
        "ticker": ticker,
        "name": name,
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
