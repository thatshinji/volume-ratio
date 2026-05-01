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
from datetime import datetime, timedelta, date
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


def get_last_day_volume(ticker: str, day: datetime) -> float:
    """获取指定日期 JSONL 最后一条快照的 volume（累计量）"""
    records = read_snapshots(ticker, day)
    if not records:
        return 0.0
    return float(records[-1].get("volume", 0))


# 历史日成交量缓存: {(ticker, date_str): volume}
_historical_vol_cache = {}
# K 线数据日缓存: {ticker: (date_fetched, api_vol_data)}
_kline_daily_cache = {}
# 交易日日缓存: {market: (date_fetched, trading_days_set)}
_trading_days_daily_cache = {}


def _check_trading_days(market: str) -> set:
    """查询今日是否为交易日，返回最近 5 个交易日集合（每天只查一次）"""
    today = date.today()
    if market in _trading_days_daily_cache:
        cached_date, cached_data = _trading_days_daily_cache[market]
        if cached_date == today:
            return cached_data
    try:
        import io
        from longbridge.openapi import OAuthBuilder, Config, QuoteContext, Market
        token_dir = Path.home() / ".longbridge" / "openapi" / "tokens"
        files = list(token_dir.iterdir())
        if not files:
            return set()
        cid = files[0].name

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
            market_enum = getattr(Market, market, None)
            if market_enum is None:
                return set()
            end = date.today() + timedelta(days=1)
            start = end - timedelta(days=10)
            result = ctx.trading_days(market_enum, start, end)
        except Exception:
            sys.stdout = old_stdout
            os.dup2(old_stdout_fd, 1)
            os.close(old_stdout_fd)
            return set()
        finally:
            sys.stdout = old_stdout
            os.dup2(old_stdout_fd, 1)
            os.close(old_stdout_fd)

        days = set(result.trading_days)
        _trading_days_daily_cache[market] = (today, days)
        return days
    except Exception:
        return set()


# 交易日缓存 {market: set_of_trading_day_strings}
_trading_days_cache = {}


def is_trading_day(ticker: str) -> bool:
    """判断今天是否为该 ticker 所在市场的交易日"""
    market = get_market(ticker)
    if market not in _trading_days_cache:
        _trading_days_cache[market] = _check_trading_days(market)
    trading_days = _trading_days_cache[market]
    if not trading_days:
        return True  # 查询失败时默认交易日
    return date.today() in trading_days


def _fetch_historical_volumes(tickers: list, days: int = 7) -> dict:
    """批量获取历史日成交量（通过 Longbridge K 线 API，每天只查一次）"""
    if not tickers:
        return {}

    today = date.today()
    # 检查缓存：所有 ticker 都有今日缓存则直接返回
    result = {}
    uncached_tickers = []
    for ticker in tickers:
        if ticker in _kline_daily_cache and _kline_daily_cache[ticker][0] == today:
            result.update(_kline_daily_cache[ticker][1])
        else:
            uncached_tickers.append(ticker)
    if not uncached_tickers:
        return result
    tickers = uncached_tickers
    try:
        import io
        from longbridge.openapi import OAuthBuilder, Config, QuoteContext, Period, AdjustType
        token_dir = Path.home() / ".longbridge" / "openapi" / "tokens"
        files = list(token_dir.iterdir())
        if not files:
            return {}
        cid = files[0].name

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
        except Exception:
            sys.stdout = old_stdout
            os.dup2(old_stdout_fd, 1)
            os.close(old_stdout_fd)
            raise
        finally:
            sys.stdout = old_stdout
            os.dup2(old_stdout_fd, 1)
            os.close(old_stdout_fd)

        end = date.today()
        start = end - timedelta(days=days + 5)  # 多取几天，跳过非交易日

        result = {}
        for ticker in tickers:
            try:
                old_stdout_fd = os.dup(1)
                devnull = os.open(os.devnull, os.O_WRONLY)
                os.dup2(devnull, 1)
                os.close(devnull)
                old_stdout = sys.stdout
                sys.stdout = io.StringIO()
                try:
                    cs = ctx.history_candlesticks_by_date(
                        ticker, Period.Day, AdjustType.NoAdjust, start, end
                    )
                except Exception:
                    sys.stdout = old_stdout
                    os.dup2(old_stdout_fd, 1)
                    os.close(old_stdout_fd)
                    continue
                finally:
                    sys.stdout = old_stdout
                    os.dup2(old_stdout_fd, 1)
                    os.close(old_stdout_fd)

                ticker_data = {}
                for c in cs:
                    # c.timestamp 格式: "2026-04-30 12:00:00" 或 "2026-04-30 00:00:00"
                    day_str = str(c.timestamp)[:10].replace("-", "")
                    vol = int(c.volume)
                    ticker_data[(ticker, day_str)] = vol
                result.update(ticker_data)
                _kline_daily_cache[ticker] = (today, ticker_data)
            except Exception:
                continue

        return result
    except Exception as e:
        print(f"[compute] 历史成交量获取失败: {e}", flush=True)
        return {}


def get_historical_day_volume(ticker: str, day: datetime, api_vol_data: dict = None) -> float:
    """获取指定日期的真实日成交量（优先用 API K 线数据）"""
    day_str = day.strftime("%Y%m%d")

    # 1. 优先用传入的 API 数据
    if api_vol_data and (ticker, day_str) in api_vol_data:
        return float(api_vol_data[(ticker, day_str)])

    # 2. 检查缓存
    if (ticker, day_str) in _historical_vol_cache:
        return _historical_vol_cache[(ticker, day_str)]

    # 3. 回退到 JSONL 计算
    records = read_snapshots(ticker, day)
    if not records:
        return 0.0
    if len(records) < 2:
        return float(records[-1].get("volume", 0))
    first_vol = float(records[0].get("volume", 0))
    last_vol = float(records[-1].get("volume", 0))
    return max(0.0, last_vol - first_vol)


def get_today_volume(ticker: str, current_time: datetime = None) -> float:
    """获取今日同时段真实成交量（差分量）"""
    if current_time is None:
        current_time = datetime.now()

    config = load_config()
    window = config.get("params", {}).get("volume_ratio_window", 5)

    return get_interval_volume(ticker, current_time, window)


def get_day_volume(ticker: str, day: datetime, api_vol_data: dict = None) -> float:
    """获取指定日期的真实成交量（优先用 API K 线数据）"""
    return get_historical_day_volume(ticker, day, api_vol_data)


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
        conn.execute("CREATE INDEX IF NOT EXISTS idx_signals_timestamp ON signals(timestamp)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_volume_ratios_ticker ON volume_ratios(ticker)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_volume_ratios_timestamp ON volume_ratios(timestamp)")
    _db_initialized = True


def calc_volume_ratio(ticker: str, current_time: datetime = None, api_vol_data: dict = None) -> tuple:
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
        vol = get_day_volume(ticker, past_day, api_vol_data)
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
        if (hour == 14 and minute >= 30) or hour == 15:
            return "尾盘放量"
    return ""


# 降频写入缓存: {ticker: last_write_time}
_last_ratio_write = {}
RATIO_WRITE_INTERVAL = 300  # 5 分钟写一次


def save_ratio(ticker: str, ratio: float, volume_today: float, volume_avg5: float,
               price: float, change_pct: float, signal: str):
    """保存量比到数据库（每 5 分钟写一次）"""
    now = datetime.now()
    last_write = _last_ratio_write.get(ticker)
    if last_write and (now - last_write).total_seconds() < RATIO_WRITE_INTERVAL:
        return

    init_db()
    db_path = get_db_path()

    try:
        with sqlite3.connect(db_path, timeout=30) as conn:
            conn.execute("""
                INSERT INTO volume_ratios
                (ticker, timestamp, ratio, volume_today, volume_avg5, price, change_pct, signal)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (ticker, now.isoformat(), ratio, volume_today, volume_avg5, price, change_pct, signal))
        _last_ratio_write[ticker] = now
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

    # 批量获取 REST API 数据（价格 + 成交量），一次 API 调用
    api_data = _fetch_price_from_api(tickers)

    # 批量获取历史日成交量（K 线 API）
    api_vol_data = _fetch_historical_volumes(tickers, days=7)

    results = []
    for ticker in tickers:
        result = compute_ticker(ticker, api_data=api_data, api_vol_data=api_vol_data)
        if result:
            results.append(result)

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
        except Exception:
            # 确保 stdout 恢复
            try:
                sys.stdout = old_stdout
                os.dup2(old_stdout_fd, 1)
                os.close(old_stdout_fd)
            except Exception:
                pass
            raise
        finally:
            sys.stdout = old_stdout
            os.dup2(old_stdout_fd, 1)
            os.close(old_stdout_fd)
        result = {}
        for q in quotes:
            last = float(q.last_done or 0)
            prev = float(q.prev_close or 0)
            change_pct = ((last - prev) / prev * 100) if prev > 0 else 0
            result[q.symbol] = {"price": last, "change_pct": round(change_pct, 2), "volume": int(q.volume)}
        return result
    except Exception as e:
        print(f"[compute] API 价格获取失败: {e}", flush=True)
        return {}


def compute_ticker(ticker: str, api_data: dict = None, api_vol_data: dict = None) -> Optional[dict]:
    """计算单个标的的量比（api_data/api_vol_data 可选，避免重复 API 调用）"""
    snapshot = get_latest_snapshot(ticker)

    # 单独调用时也获取 K 线历史数据
    if api_vol_data is None:
        api_vol_data = _fetch_historical_volumes([ticker], days=7)

    ratio, today_vol, avg_vol, signal = calc_volume_ratio(ticker, api_vol_data=api_vol_data)
    intraday_ratio, signal_intraday, cond_vol, cond_stop, cond_stable = calc_intraday_ratio(ticker)

    price = snapshot.get("price", 0) if snapshot else 0
    change_pct = snapshot.get("change_pct", 0) if snapshot else 0

    # REST API 修正：API volume = 当日成交量（K 线数据证实）
    if api_data is None:
        api_data = _fetch_price_from_api([ticker])
    if ticker in api_data:
        price = api_data[ticker]["price"]
        change_pct = api_data[ticker]["change_pct"]
        api_vol = api_data[ticker]["volume"]

        if not is_trading_day(ticker):
            ratio, signal = 0.0, "休市"
        elif api_vol > 0:
            today_vol = api_vol
            if avg_vol > 0:
                ratio = round(today_vol / avg_vol, 2)
                signal = get_signal(ratio)

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
