#!/usr/bin/env python3
"""
数据清理脚本 - 动态检测市场收盘后清理过期数据
每小时由 cron 调用，脚本内部判断各市场是否已收盘

清理规则：
  - JSONL 快照：保留 20 天
  - volume_ratios：保留 20 天
  - signals：保留 20 天
  - daily_summary：保留 90 天
"""

import os
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).parent.parent
SNAPSHOT_DIR = ROOT / "data" / "snapshots"
DB_PATH = ROOT / "data" / "ratios.db"

sys.path.insert(0, str(ROOT / "scripts"))

from core.config import load_config

# 保留天数
SNAPSHOT_KEEP_DAYS = 20
RATIO_KEEP_DAYS = 20
SIGNAL_KEEP_DAYS = 20
SUMMARY_KEEP_DAYS = 90


def get_et_now() -> datetime:
    """获取美东时间（自动处理 EDT/EST）"""
    try:
        import zoneinfo
        et = zoneinfo.ZoneInfo("America/New_York")
        return datetime.now(et)
    except ImportError:
        import pytz
        et = pytz.timezone("America/New_York")
        return datetime.now(et)


def is_market_closed(market: str) -> bool:
    """动态判断市场是否已收盘（收盘后 1 小时开始清理）"""
    now = datetime.now()

    if market == "CN":
        # A股 15:00 收盘，16:30 后清理
        return now.hour >= 16 and now.minute >= 30
    elif market == "HK":
        # 港股 16:00 收盘，17:00 后清理
        return now.hour >= 17
    elif market == "US":
        # 美股 16:00 ET 收盘，17:00 ET 后清理
        et_now = get_et_now()
        return et_now.hour >= 17
    return False


def cleanup_jsonl_snapshots(market: str, keep_days: int):
    """清理过期的 JSONL 快照文件"""
    market_dir = SNAPSHOT_DIR / market
    if not market_dir.exists():
        return

    cutoff = datetime.now() - timedelta(days=keep_days)
    cutoff_str = cutoff.strftime("%Y%m%d")
    removed = 0

    for f in market_dir.iterdir():
        if not f.name.endswith(".jsonl"):
            continue
        # 文件名格式: TICKER_YYYYMMDD.jsonl
        parts = f.stem.rsplit("_", 1)
        if len(parts) < 2:
            continue
        day_str = parts[-1]
        if day_str < cutoff_str:
            try:
                f.unlink()
                removed += 1
            except OSError as e:
                print(f"[cleanup] 删除失败 {f.name}: {e}")

    if removed > 0:
        print(f"[cleanup] {market} JSONL: 删除 {removed} 个过期文件")


def cleanup_old_json_snapshots(market: str, keep_days: int):
    """清理旧格式的单个 JSON 快照文件（兼容过渡期）"""
    market_dir = SNAPSHOT_DIR / market
    if not market_dir.exists():
        return

    cutoff = datetime.now() - timedelta(days=keep_days)
    cutoff_str = cutoff.strftime("%Y%m%d")
    removed = 0

    for f in market_dir.iterdir():
        if not f.name.endswith(".json"):
            continue
        # 文件名格式: TICKER_YYYYMMDD_HHMMSS_ffffff.json
        parts = f.stem.split("_")
        if len(parts) < 3:
            continue
        day_str = parts[1] if len(parts) >= 2 else ""
        if day_str < cutoff_str:
            f.unlink()
            removed += 1

    if removed > 0:
        print(f"[cleanup] {market} JSON: 删除 {removed} 个过期旧格式文件")


def cleanup_database(table: str, keep_days: int):
    """清理数据库中的过期记录"""
    if not DB_PATH.exists():
        return

    cutoff = (datetime.now() - timedelta(days=keep_days)).isoformat()

    try:
        with sqlite3.connect(DB_PATH, timeout=30) as conn:
            cursor = conn.execute(f"DELETE FROM {table} WHERE timestamp < ?", (cutoff,))
            if cursor.rowcount > 0:
                print(f"[cleanup] {table}: 删除 {cursor.rowcount} 条过期记录")
    except sqlite3.OperationalError as e:
        print(f"[cleanup] {table} 清理失败: {e}")


def get_disk_usage() -> dict:
    """获取数据目录磁盘占用"""
    result = {"snapshots": 0, "database": 0, "total": 0}

    if SNAPSHOT_DIR.exists():
        for f in SNAPSHOT_DIR.rglob("*"):
            if f.is_file():
                result["snapshots"] += f.stat().st_size

    if DB_PATH.exists():
        result["database"] = DB_PATH.stat().st_size

    result["total"] = result["snapshots"] + result["database"]
    return result


def format_size(size_bytes: int) -> str:
    """格式化文件大小"""
    if size_bytes < 1024:
        return f"{size_bytes}B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f}KB"
    else:
        return f"{size_bytes / (1024 * 1024):.1f}MB"


def main():
    import argparse
    parser = argparse.ArgumentParser(description="数据清理脚本")
    parser.add_argument("--dry-run", action="store_true", help="只显示将要清理的内容，不实际删除")
    parser.add_argument("--force", action="store_true", help="强制清理所有市场（忽略收盘检测）")
    parser.add_argument("--status", action="store_true", help="显示磁盘占用状态")
    args = parser.parse_args()

    if args.status:
        usage = get_disk_usage()
        print(f"快照文件: {format_size(usage['snapshots'])}")
        print(f"数据库:   {format_size(usage['database'])}")
        print(f"总计:     {format_size(usage['total'])}")

        # 统计文件数
        for market in ["US", "HK", "CN"]:
            market_dir = SNAPSHOT_DIR / market
            if market_dir.exists():
                jsonl_count = len(list(market_dir.glob("*.jsonl")))
                json_count = len(list(market_dir.glob("*.json")))
                print(f"  {market}: {jsonl_count} JSONL + {json_count} JSON")
        return

    print(f"[cleanup] 开始清理检查 ({datetime.now().strftime('%Y-%m-%d %H:%M')})")

    for market in ["CN", "HK", "US"]:
        if not args.force and not is_market_closed(market):
            print(f"[cleanup] {market} 市场尚未收盘，跳过")
            continue

        print(f"[cleanup] {market} 市场已收盘，开始清理...")

        if not args.dry_run:
            cleanup_jsonl_snapshots(market, SNAPSHOT_KEEP_DAYS)
            cleanup_old_json_snapshots(market, SNAPSHOT_KEEP_DAYS)

    # 数据库清理（不分市场，按时间戳）
    if not args.dry_run:
        cleanup_database("volume_ratios", RATIO_KEEP_DAYS)
        cleanup_database("signals", SIGNAL_KEEP_DAYS)
        # daily_summary 使用 date 字段而非 timestamp，单独处理
        if DB_PATH.exists():
            cutoff_date = (datetime.now() - timedelta(days=SUMMARY_KEEP_DAYS)).strftime("%Y-%m-%d")
            try:
                with sqlite3.connect(DB_PATH, timeout=30) as conn:
                    cursor = conn.execute("DELETE FROM daily_summary WHERE date < ?", (cutoff_date,))
                    if cursor.rowcount > 0:
                        print(f"[cleanup] daily_summary: 删除 {cursor.rowcount} 条过期记录")
            except sqlite3.OperationalError:
                pass

    # 显示清理后状态
    usage = get_disk_usage()
    print(f"[cleanup] 当前占用: {format_size(usage['total'])} (快照 {format_size(usage['snapshots'])} + DB {format_size(usage['database'])})")
    print(f"[cleanup] 清理完成")


if __name__ == "__main__":
    main()
