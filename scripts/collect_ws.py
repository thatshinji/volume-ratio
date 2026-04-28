#!/usr/bin/env python3
"""
行情采集脚本 - Longbridge WebSocket 实时推送模式
回调线程只管入队，主线程负责写出到磁盘（解决后台模式下文件丢失问题）

Usage:
    python3 collect_ws.py                    # 前台运行
    python3 collect_ws.py --daemon            # 后台守护进程运行
"""

import argparse
import json
import os
import queue
import signal
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent.parent
CONFIG_PATH = ROOT / "config.yaml"
SNAPSHOT_DIR = ROOT / "data" / "snapshots"

running = True
quote_queue = queue.Queue()
_prev_close_cache = {}  # ticker -> prev_close


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


def fetch_prev_close(tickers: list):
    """启动时从 ctx.quote() 获取昨收价，缓存到 _prev_close_cache"""
    from longbridge.openapi import OAuthBuilder, Config, QuoteContext

    try:
        client_id = Path.home() / ".longbridge" / "openapi" / "tokens"
        cid = list(client_id.iterdir())[0].name
        oauth = OAuthBuilder(cid).build(lambda url: None)
        config = Config.from_oauth(oauth)
        ctx = QuoteContext(config)

        quotes = ctx.quote(tickers)
        for q in quotes:
            _prev_close_cache[q.symbol] = float(q.prev_close or 0)
        print(f"[ws] prev_close 缓存完成: {len(_prev_close_cache)} 个标的", flush=True)
    except Exception as e:
        print(f"[ws] prev_close 缓存失败: {e}", flush=True)


def get_prev_close(ticker: str) -> float:
    """从最新快照文件获取昨收价"""
    market = get_market(ticker)
    market_dir = SNAPSHOT_DIR / market
    if not market_dir.exists():
        return 0.0

    prefix = ticker.replace('.', '_')
    today = datetime.now().strftime("%Y%m%d")

    latest_file = None
    latest_time = ""
    for f in market_dir.iterdir():
        if not f.name.startswith(prefix):
            continue
        # 只看今天的快照
        if today not in f.name:
            continue
        ts = f.name.split('_')[-1].replace('.json', '')
        if ts > latest_time:
            latest_time = ts
            latest_file = f

    if latest_file:
        try:
            with open(latest_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                return float(data.get("price", 0))
        except (json.JSONDecodeError, OSError):
            pass
    return 0.0


def extract_fields(quote, ticker: str) -> dict:
    """从 PushQuote 提取字段（prev_close 从缓存获取）"""
    last = float(quote.last_done or 0)
    open_price = float(quote.open or 0)
    high = float(quote.high or 0)
    low = float(quote.low or 0)
    volume = int(quote.volume or 0)
    turnover = float(quote.turnover or 0)

    prev_close = _prev_close_cache.get(ticker, 0.0)
    if prev_close == 0:
        prev_close = open_price if open_price > 0 else last

    change = last - prev_close
    change_pct = (change / prev_close * 100) if prev_close > 0 else 0

    return {
        "ticker": ticker,
        "timestamp": datetime.now().isoformat(),
        "price": last,
        "open": open_price,
        "high": high,
        "low": low,
        "volume": volume,
        "turnover": turnover,
        "change": round(change, 4),
        "change_pct": round(change_pct, 2),
    }


def on_quote(symbol: str, quote):
    """WebSocket 行情回调 - 放到队列，由主线程写出"""
    try:
        data = extract_fields(quote, symbol)
        quote_queue.put((symbol, data))
    except Exception as e:
        print(f"[ws] on_quote error: {e}", flush=True)


def writer_thread():
    """主线程：不断从队列取出数据写出到磁盘"""
    while running:
        try:
            symbol, data = quote_queue.get(timeout=1)
            save_snapshot(symbol, data)
            quote_queue.task_done()
        except queue.Empty:
            continue
        except Exception as e:
            print(f"[ws] writer error: {e}", flush=True)


def save_snapshot(ticker: str, data: dict):
    market = get_market(ticker)
    market_dir = SNAPSHOT_DIR / market
    market_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{ticker.replace('.', '_')}_{ts}.json"
    filepath = market_dir / filename

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())

    print(f"[ws] {ticker} -> {filepath.name}", flush=True)


def get_all_tickers() -> list:
    config = load_config()
    watchlist = config.get("watchlist", {})
    tickers = []
    for market in ["us", "hk", "cn"]:
        tickers.extend(watchlist.get(market, []))
    return tickers


def get_client_id() -> str:
    token_dir = Path.home() / ".longbridge" / "openapi" / "tokens"
    if not token_dir.exists():
        raise FileNotFoundError("Longbridge token 目录不存在")
    files = list(token_dir.iterdir())
    if not files:
        raise FileNotFoundError("Longbridge token 文件不存在")
    return files[0].name


def run_websocket():
    from longbridge.openapi import OAuthBuilder, Config, QuoteContext, SubType

    client_id = get_client_id()
    oauth = OAuthBuilder(client_id).build(lambda url: None)
    config = Config.from_oauth(oauth)
    ctx = QuoteContext(config)

    tickers = get_all_tickers()
    print(f"[ws] 连接 Longbridge WebSocket，订阅 {len(tickers)} 个标的...", flush=True)

    # 启动时先获取昨收价缓存
    fetch_prev_close(tickers)

    ctx.set_on_quote(on_quote)
    ctx.subscribe(tickers, [SubType.Quote])
    print(f"[ws] 订阅成功，等待行情推送...", flush=True)

    # 主线程负责写出回调放入队列的数据
    while running:
        try:
            symbol, data = quote_queue.get(timeout=1)
            save_snapshot(symbol, data)
            quote_queue.task_done()
        except queue.Empty:
            continue
        except Exception as e:
            print(f"[ws] error: {e}", flush=True)


def signal_handler(signum, frame):
    global running
    print("\n[ws] 收到退出信号，正在关闭...", flush=True)
    running = False


if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    parser = argparse.ArgumentParser(description="Longbridge WebSocket 实时行情采集")
    parser.add_argument("--daemon", action="store_true", help="后台守护进程运行")
    args = parser.parse_args()

    if args.daemon:
        pid = os.fork()
        if pid > 0:
            print(f"[ws] 后台运行，PID: {pid}", flush=True)
            sys.exit(0)

        os.setsid()

        pid = os.fork()
        if pid > 0:
            sys.exit(0)

        log_dir = ROOT / "logs"
        log_dir.mkdir(exist_ok=True)
        out_fd = os.open(log_dir / "ws_collect.log", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        err_fd = os.open(log_dir / "ws_collect.err", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        os.dup2(out_fd, sys.stdout.fileno())
        os.dup2(err_fd, sys.stderr.fileno())
        os.close(out_fd)
        os.close(err_fd)

        devnull = os.open(os.devnull, os.O_RDONLY)
        os.dup2(devnull, sys.stdin.fileno())
        os.close(devnull)

        print(f"[ws] 守护进程启动，PID: {os.getpid()}", flush=True)

    run_websocket()
