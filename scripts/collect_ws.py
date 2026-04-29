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
SNAPSHOT_DIR = ROOT / "data" / "snapshots"

# 将 scripts/ 加入 sys.path
sys.path.insert(0, str(ROOT / "scripts"))

from core.config import load_config
from core.market import get_market, get_all_tickers

# 线程安全的全局变量
running = threading.Event()
running.set()
quote_queue = queue.Queue()
_prev_close_cache = {}  # ticker -> prev_close
_cache_lock = threading.Lock()


def fetch_prev_close(tickers: list):
    """启动时从 ctx.quote() 获取昨收价，缓存到 _prev_close_cache"""
    from longbridge.openapi import OAuthBuilder, Config, QuoteContext

    try:
        client_id = Path.home() / ".longbridge" / "openapi" / "tokens"
        files = list(client_id.iterdir())
        if not files:
            print("[ws] prev_close 缓存失败: token 目录为空", flush=True)
            return
        cid = files[0].name
        oauth = OAuthBuilder(cid).build(lambda url: None)
        config = Config.from_oauth(oauth)
        ctx = QuoteContext(config)

        quotes = ctx.quote(tickers)
        with _cache_lock:
            for q in quotes:
                _prev_close_cache[q.symbol] = float(q.prev_close or 0)
        print(f"[ws] prev_close 缓存完成: {len(_prev_close_cache)} 个标的", flush=True)
    except (OSError, ValueError, KeyError) as e:
        print(f"[ws] prev_close 缓存失败: {e}", flush=True)


def get_prev_close(ticker: str) -> float:
    """从最新 JSONL 快照获取昨收价"""
    jsonl_path = get_jsonl_path(ticker)
    if not jsonl_path.exists():
        return 0.0

    last_price = 0.0
    try:
        with open(jsonl_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    last_price = float(data.get("price", 0))
                except (json.JSONDecodeError, ValueError):
                    continue
    except OSError:
        pass
    return last_price


def extract_fields(quote, ticker: str) -> dict:
    """从 PushQuote 提取字段（prev_close 从缓存获取）"""
    last = float(quote.last_done or 0)
    open_price = float(quote.open or 0)
    high = float(quote.high or 0)
    low = float(quote.low or 0)
    volume = int(quote.volume or 0)
    turnover = float(quote.turnover or 0)

    with _cache_lock:
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
    except (OSError, ValueError, KeyError) as e:
        print(f"[ws] on_quote error: {e}", flush=True)


def get_jsonl_path(ticker: str, day: datetime = None) -> Path:
    """获取 JSONL 文件路径：data/snapshots/{market}/{TICKER}_{YYYYMMDD}.jsonl"""
    if day is None:
        day = datetime.now()
    market = get_market(ticker)
    market_dir = SNAPSHOT_DIR / market
    market_dir.mkdir(parents=True, exist_ok=True)
    day_str = day.strftime("%Y%m%d")
    filename = f"{ticker.replace('.', '_')}_{day_str}.jsonl"
    return market_dir / filename


def save_snapshot(ticker: str, data: dict):
    """追加写入 JSONL 文件（一行一条快照）"""
    filepath = get_jsonl_path(ticker)
    line = json.dumps(data, ensure_ascii=False)
    with open(filepath, "a", encoding="utf-8") as f:
        f.write(line + "\n")
        f.flush()

    print(f"[ws] {ticker} -> {filepath.name}", flush=True)


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

    max_retries = 5
    retry_delays = [30, 60, 120, 300, 600]  # 秒：30s, 1m, 2m, 5m, 10m

    for attempt in range(max_retries):
        try:
            client_id = get_client_id()
            oauth = OAuthBuilder(client_id).build(lambda url: None)
            config = Config.from_oauth(oauth)
            ctx = QuoteContext(config)

            config_data = load_config()
            tickers = get_all_tickers(config_data)
            print(f"[ws] 连接 Longbridge WebSocket，订阅 {len(tickers)} 个标的...", flush=True)

            # 启动时先获取昨收价缓存
            fetch_prev_close(tickers)

            ctx.set_on_quote(on_quote)
            ctx.subscribe(tickers, [SubType.Quote])
            print(f"[ws] 订阅成功，等待行情推送...", flush=True)

            # 连接成功，重置重试计数
            # 主线程负责写出回调放入队列的数据
            while running.is_set():
                try:
                    symbol, data = quote_queue.get(timeout=1)
                    save_snapshot(symbol, data)
                    quote_queue.task_done()
                except queue.Empty:
                    continue
                except (OSError, ValueError, KeyError) as e:
                    print(f"[ws] error: {e}", flush=True)

            # running 被清除，正常退出
            return

        except (OSError, ConnectionError, TimeoutError) as e:
            delay = retry_delays[min(attempt, len(retry_delays) - 1)]
            print(f"[ws] 连接失败 (第 {attempt + 1}/{max_retries} 次): {e}", flush=True)
            if attempt < max_retries - 1:
                print(f"[ws] {delay} 秒后重试...", flush=True)
                for _ in range(delay):
                    if not running.is_set():
                        return
                    time.sleep(1)
            else:
                print(f"[ws] 已达最大重试次数，停止采集。launcher 将在下次检查时重启", flush=True)
                return

        except Exception as e:
            print(f"[ws] 未知异常: {e}", flush=True)
            return


def signal_handler(signum, frame):
    print("\n[ws] 收到退出信号，正在关闭...", flush=True)
    running.clear()
    sys.exit(0)


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
        out_fd = os.open(log_dir / "ws_collect.log", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        err_fd = os.open(log_dir / "ws_collect.err", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        os.dup2(out_fd, sys.stdout.fileno())
        os.dup2(err_fd, sys.stderr.fileno())
        os.close(out_fd)
        os.close(err_fd)

        devnull = os.open(os.devnull, os.O_RDONLY)
        os.dup2(devnull, sys.stdin.fileno())
        os.close(devnull)

        print(f"[ws] 守护进程启动，PID: {os.getpid()}", flush=True)

    run_websocket()
