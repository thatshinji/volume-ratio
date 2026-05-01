#!/usr/bin/env python3
"""
数据清理脚本 - 动态检测市场收盘后清理过期数据
每小时由 cron 调用，脚本内部判断各市场是否已收盘

清理规则：
  - JSONL 快照：保留 20 天
  - quote_snapshots：保留 20 天
  - quote_minute_bars：保留 20 天
  - volume_ratios：保留 20 天
  - signals：保留 20 天
  - 快照目录：超过 3GB 时删除最旧快照，降到 2.7GB 以下
  - SQLite 数据库：超过 1GB 时清理旧数据并 VACUUM
"""

import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).parent.parent
SNAPSHOT_DIR = ROOT / "data" / "snapshots"
DB_PATH = ROOT / "data" / "ratios.db"

sys.path.insert(0, str(ROOT / "scripts"))

from core.market import market_now
from core.display import format_size

# 保留天数
SNAPSHOT_KEEP_DAYS = 20
RATIO_KEEP_DAYS = 20
SIGNAL_KEEP_DAYS = 20
LLM_KEEP_DAYS = 90

GIB = 1024 * 1024 * 1024
SNAPSHOT_MAX_BYTES = 3 * GIB
SNAPSHOT_TARGET_BYTES = int(SNAPSHOT_MAX_BYTES * 0.9)
DB_MAX_BYTES = 1 * GIB


def is_market_closed(market: str) -> bool:
    """动态判断市场是否已收盘（收盘后 1 小时开始清理）"""
    now = market_now(market)

    if market == "CN":
        # A股 15:00 收盘，16:30 后清理
        return now.hour > 16 or (now.hour == 16 and now.minute >= 30)
    elif market == "HK":
        # 港股 16:00 收盘，17:00 后清理
        return now.hour >= 17
    elif market == "US":
        # 美股 16:00 ET 收盘，17:00 ET 后清理
        return now.hour >= 17
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
    timestamp_column = "last_timestamp" if table == "quote_minute_bars" else "timestamp"

    try:
        with sqlite3.connect(DB_PATH, timeout=30) as conn:
            cursor = conn.execute(f"DELETE FROM {table} WHERE {timestamp_column} < ?", (cutoff,))
            if cursor.rowcount > 0:
                print(f"[cleanup] {table}: 删除 {cursor.rowcount} 条过期记录")
    except sqlite3.OperationalError as e:
        print(f"[cleanup] {table} 清理失败: {e}")


def cleanup_optional_database_table(table: str, keep_days: int, timestamp_column: str = "timestamp"):
    """清理可选表；表不存在时静默跳过。"""
    if not DB_PATH.exists():
        return

    cutoff = (datetime.now() - timedelta(days=keep_days)).isoformat()
    try:
        with sqlite3.connect(DB_PATH, timeout=30) as conn:
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone()
            if not exists:
                return
            cursor = conn.execute(f"DELETE FROM {table} WHERE {timestamp_column} < ?", (cutoff,))
            if cursor.rowcount > 0:
                print(f"[cleanup] {table}: 删除 {cursor.rowcount} 条过期记录")
    except sqlite3.OperationalError as e:
        print(f"[cleanup] {table} 清理失败: {e}")


def cleanup_snapshot_size_limit(dry_run: bool = False):
    """快照目录容量兜底：超过 3GB 时删除最旧快照到 2.7GB 以下。"""
    if not SNAPSHOT_DIR.exists():
        return

    files = [f for f in SNAPSHOT_DIR.rglob("*") if f.is_file() and f.suffix in (".jsonl", ".json")]
    total = sum(f.stat().st_size for f in files)
    if total <= SNAPSHOT_MAX_BYTES:
        return

    removed = 0
    removed_size = 0
    for f in sorted(files, key=lambda p: (p.stat().st_mtime, str(p))):
        if total - removed_size <= SNAPSHOT_TARGET_BYTES:
            break
        size = f.stat().st_size
        if dry_run:
            print(f"[cleanup] dry-run: 将删除快照 {f.relative_to(ROOT)} ({format_size(size)})")
        else:
            try:
                f.unlink()
            except OSError as e:
                print(f"[cleanup] 删除失败 {f.name}: {e}")
                continue
        removed += 1
        removed_size += size

    action = "将删除" if dry_run else "删除"
    print(
        f"[cleanup] 快照容量超限: {format_size(total)} > {format_size(SNAPSHOT_MAX_BYTES)}，"
        f"{action} {removed} 个最旧文件，释放约 {format_size(removed_size)}"
    )


def vacuum_database():
    """压缩 SQLite 文件，把 DELETE 释放的页真正还给磁盘。"""
    if not DB_PATH.exists():
        return
    before = DB_PATH.stat().st_size
    try:
        with sqlite3.connect(DB_PATH, timeout=60) as conn:
            conn.execute("VACUUM")
    except sqlite3.OperationalError as e:
        print(f"[cleanup] VACUUM 失败: {e}")
        return
    after = DB_PATH.stat().st_size
    if after < before:
        print(f"[cleanup] 数据库 VACUUM: {format_size(before)} -> {format_size(after)}")


def cleanup_database_size_limit(dry_run: bool = False):
    """数据库容量兜底：超过 1GB 时清理旧数据并压缩。"""
    if not DB_PATH.exists():
        return

    size = DB_PATH.stat().st_size
    if size <= DB_MAX_BYTES:
        return

    print(f"[cleanup] 数据库容量超限: {format_size(size)} > {format_size(DB_MAX_BYTES)}")
    if dry_run:
        print("[cleanup] dry-run: 将清理过期数据库记录并执行 VACUUM")
        return

    cleanup_database("quote_snapshots", SNAPSHOT_KEEP_DAYS)
    cleanup_database("quote_minute_bars", SNAPSHOT_KEEP_DAYS)
    cleanup_database("volume_ratios", RATIO_KEEP_DAYS)
    cleanup_database("signals", SIGNAL_KEEP_DAYS)
    cleanup_optional_database_table("llm_calls", LLM_KEEP_DAYS)
    vacuum_database()

    size = DB_PATH.stat().st_size
    if size > DB_MAX_BYTES:
        emergency_trim_database(dry_run)


def emergency_trim_database(dry_run: bool = False):
    """数据库仍超限时，按重要性从低到高删除最旧记录，直到低于 1GB。"""
    if not DB_PATH.exists():
        return

    # 越靠前越先删；quote_minute_bars 是核心计算数据，最后才动。
    tables = [
        ("llm_calls", "timestamp"),
        ("quote_snapshots", "timestamp"),
        ("volume_ratios", "timestamp"),
        ("signals", "timestamp"),
        ("quote_minute_bars", "last_timestamp"),
    ]
    batch_size = 50_000

    if dry_run:
        print("[cleanup] dry-run: 数据库 VACUUM 后若仍超限，将按最旧记录分批裁剪")
        return

    def delete_oldest_batch(table: str, timestamp_column: str) -> int:
        with sqlite3.connect(DB_PATH, timeout=60) as conn:
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone()
            if not exists:
                return 0
            cursor = conn.execute(
                f"""
                DELETE FROM {table}
                WHERE rowid IN (
                    SELECT rowid FROM {table}
                    ORDER BY {timestamp_column}
                    LIMIT ?
                )
                """,
                (batch_size,),
            )
            return cursor.rowcount

    for table, timestamp_column in tables:
        removed_total = 0
        while DB_PATH.stat().st_size > DB_MAX_BYTES:
            removed = delete_oldest_batch(table, timestamp_column)
            if removed <= 0:
                break
            removed_total += removed
            vacuum_database()
            if removed_total > 0:
                print(f"[cleanup] {table}: 容量兜底删除最旧记录 {removed_total} 条")
        if DB_PATH.stat().st_size <= DB_MAX_BYTES:
            break

    size = DB_PATH.stat().st_size
    if size > DB_MAX_BYTES:
        print(f"[cleanup] 警告: 容量兜底后数据库仍超过上限 ({format_size(size)})，请检查异常写入")


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


def main():
    import argparse
    parser = argparse.ArgumentParser(description="数据清理脚本")
    parser.add_argument("--dry-run", action="store_true", help="只显示将要清理的内容，不实际删除")
    parser.add_argument("--force", action="store_true", help="强制清理所有市场（忽略收盘检测）")
    parser.add_argument("--status", action="store_true", help="显示磁盘占用状态")
    args = parser.parse_args()

    if args.status:
        usage = get_disk_usage()
        print(f"快照文件: {format_size(usage['snapshots'])} / 上限 {format_size(SNAPSHOT_MAX_BYTES)}")
        print(f"数据库:   {format_size(usage['database'])} / 上限 {format_size(DB_MAX_BYTES)}")
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
        cleanup_database("quote_snapshots", SNAPSHOT_KEEP_DAYS)
        cleanup_database("quote_minute_bars", SNAPSHOT_KEEP_DAYS)
        cleanup_database("volume_ratios", RATIO_KEEP_DAYS)
        cleanup_database("signals", SIGNAL_KEEP_DAYS)
        cleanup_optional_database_table("llm_calls", LLM_KEEP_DAYS)

    # 容量兜底不依赖市场收盘状态。
    cleanup_snapshot_size_limit(args.dry_run)
    cleanup_database_size_limit(args.dry_run)

    # 显示清理后状态
    usage = get_disk_usage()
    print(f"[cleanup] 当前占用: {format_size(usage['total'])} (快照 {format_size(usage['snapshots'])} + DB {format_size(usage['database'])})")
    print(f"[cleanup] 清理完成")


if __name__ == "__main__":
    main()
