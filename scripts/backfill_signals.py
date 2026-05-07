#!/usr/bin/env python3
"""
信号结果回填脚本
从 quote_minute_bars 查询信号触发后的 T+1/T+3/T+5 收盘价，回填到 signals 表。

Usage:
    python3 scripts/backfill_signals.py          # 回填所有未完成的信号
    python3 scripts/backfill_signals.py --dry-run # 干跑，不写入
"""

import argparse
import sqlite3
import sys
from datetime import datetime, date, timedelta
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

DB_PATH = ROOT / "data" / "ratios.db"

from core.market import get_market, is_trading_day_on


def get_nth_trading_day(market: str, from_date: date, n: int) -> date:
    """从 from_date 开始（不含），找到第 n 个交易日。"""
    d = from_date
    count = 0
    while count < n:
        d += timedelta(days=1)
        if is_trading_day_on(market, d):
            count += 1
    return d


def get_close_price(ticker: str, market_date: str) -> float:
    """从 quote_minute_bars 获取指定交易日的收盘价（最后一根分钟 bar 的 close）。"""
    try:
        with sqlite3.connect(DB_PATH, timeout=10) as conn:
            row = conn.execute("""
                SELECT close FROM quote_minute_bars
                WHERE ticker = ? AND market_date = ?
                ORDER BY market_minute DESC LIMIT 1
            """, (ticker, market_date)).fetchone()
            return row[0] if row and row[0] and row[0] > 0 else 0
    except sqlite3.Error:
        return 0


def backfill_signal_results(dry_run: bool = False):
    """扫描未回填的信号，查询后续收盘价并更新。"""
    if not DB_PATH.exists():
        print("[backfill] 数据库不存在")
        return

    with sqlite3.connect(DB_PATH, timeout=30) as conn:
        # 查找需要回填的信号：return_5d 为 NULL，且信号时间足够久（至少 5 个交易日前）
        rows = conn.execute("""
            SELECT id, ticker, timestamp, price, market
            FROM signals
            WHERE return_5d IS NULL
            ORDER BY timestamp
        """).fetchall()

    if not rows:
        print("[backfill] 无待回填信号")
        return

    updated = 0
    skipped = 0
    insufficient = 0

    for signal_id, ticker, timestamp_str, entry_price, market in rows:
        if not entry_price or entry_price <= 0:
            skipped += 1
            continue

        if not market:
            market = get_market(ticker)

        try:
            signal_dt = datetime.fromisoformat(timestamp_str)
        except (ValueError, TypeError):
            skipped += 1
            continue

        signal_date = signal_dt.date()

        # 计算 T+1, T+3, T+5 交易日
        try:
            d1 = get_nth_trading_day(market, signal_date, 1)
            d3 = get_nth_trading_day(market, signal_date, 3)
            d5 = get_nth_trading_day(market, signal_date, 5)
        except Exception:
            skipped += 1
            continue

        today = date.today()

        # T+5 还没到，跳过（数据不完整）
        if d5 > today:
            # 但如果 T+1 或 T+3 已到，可以部分回填
            if d1 > today:
                insufficient += 1
                continue

        # 查询后续收盘价
        p1 = get_close_price(ticker, d1.isoformat())
        p3 = get_close_price(ticker, d3.isoformat()) if d3 <= today else 0
        p5 = get_close_price(ticker, d5.isoformat()) if d5 <= today else 0

        # 至少需要 T+1 数据
        if p1 <= 0:
            insufficient += 1
            continue

        # 计算收益率
        r1 = round((p1 - entry_price) / entry_price * 100, 2) if p1 > 0 else None
        r3 = round((p3 - entry_price) / entry_price * 100, 2) if p3 > 0 else None
        r5 = round((p5 - entry_price) / entry_price * 100, 2) if p5 > 0 else None

        if dry_run:
            parts = [f"  [dry-run] {ticker} {signal_date} 入场{entry_price:.2f}",
                     f"T+1={p1:.2f}({r1:+.2f}%)"]
            if p3 > 0:
                parts.append(f"T+3={p3:.2f}({r3:+.2f}%)")
            if p5 > 0:
                parts.append(f"T+5={p5:.2f}({r5:+.2f}%)")
            print(" ".join(parts))
            updated += 1
            continue

        try:
            with sqlite3.connect(DB_PATH, timeout=30) as conn:
                conn.execute("""
                    UPDATE signals SET
                        market = ?,
                        exit_price_1d = ?, exit_price_3d = ?, exit_price_5d = ?,
                        return_1d = ?, return_3d = ?, return_5d = ?
                    WHERE id = ?
                """, (market, p1, p3 if p3 > 0 else None, p5 if p5 > 0 else None,
                      r1, r3, r5, signal_id))
            updated += 1
        except sqlite3.Error as e:
            print(f"  [backfill] 更新失败 {ticker}: {e}", flush=True)

    print(f"[backfill] 完成: 更新 {updated} 条, 跳过 {skipped} 条, 数据不足 {insufficient} 条")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="信号结果回填")
    parser.add_argument("--dry-run", action="store_true", help="干跑，不写入")
    args = parser.parse_args()
    backfill_signal_results(dry_run=args.dry_run)
