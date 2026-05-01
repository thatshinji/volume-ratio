#!/usr/bin/env python3
"""从现有 JSONL 快照回填 quote_minute_bars。"""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
SNAPSHOT_DIR = ROOT / "data" / "snapshots"

sys.path.insert(0, str(ROOT / "scripts"))

from core.config import load_config
from core.market import get_all_tickers, get_market
from compute import get_db_path, init_db, save_quote_minute_bar


def snapshot_files(ticker: str) -> list[Path]:
    market_dir = SNAPSHOT_DIR / get_market(ticker)
    if not market_dir.exists():
        return []
    prefix = ticker.replace(".", "_")
    return sorted(market_dir.glob(f"{prefix}_*.jsonl"))


def backfill_ticker(conn: sqlite3.Connection, ticker: str, batch_size: int) -> tuple[int, int]:
    files = snapshot_files(ticker)
    rows = 0
    bad_rows = 0
    for path in files:
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        bad_rows += 1
                        continue
                    save_quote_minute_bar(ticker, data, source="jsonl_backfill", conn=conn)
                    rows += 1
                    if rows % batch_size == 0:
                        conn.commit()
        except OSError as e:
            print(f"[backfill] {ticker} 读取失败 {path.name}: {e}", flush=True)
    conn.commit()
    return rows, bad_rows


def main():
    parser = argparse.ArgumentParser(description="从 JSONL 快照回填 SQLite 分钟聚合表")
    parser.add_argument("tickers", nargs="*", help="只回填指定 ticker；默认回填 config.yaml 中全部 ticker")
    parser.add_argument("--reset", action="store_true", help="回填前删除目标 ticker 的旧分钟聚合数据")
    parser.add_argument("--batch-size", type=int, default=5000, help="每多少条 JSONL 提交一次事务")
    args = parser.parse_args()

    config = load_config()
    tickers = args.tickers or get_all_tickers(config)
    init_db()

    with sqlite3.connect(get_db_path(), timeout=60) as conn:
        if args.reset:
            for ticker in tickers:
                deleted = conn.execute("DELETE FROM quote_minute_bars WHERE ticker = ?", (ticker,)).rowcount
                if deleted:
                    print(f"[backfill] {ticker} 删除旧分钟聚合 {deleted} 行", flush=True)
            conn.commit()

        total_rows = 0
        total_bad = 0
        for ticker in tickers:
            rows, bad_rows = backfill_ticker(conn, ticker, args.batch_size)
            total_rows += rows
            total_bad += bad_rows
            minutes = conn.execute(
                "SELECT COUNT(*) FROM quote_minute_bars WHERE ticker = ?",
                (ticker,),
            ).fetchone()[0]
            print(f"[backfill] {ticker}: 读取 {rows} 行 JSONL，分钟聚合 {minutes} 行", flush=True)

    print(f"[backfill] 完成：读取 {total_rows} 行，坏行 {total_bad} 行", flush=True)


if __name__ == "__main__":
    main()
