#!/usr/bin/env python3
"""
命令行入口 - 随时查询任意标的的量比
支持 --ticker / --scan / --market / --status / --history / --signals / --add / --remove / --mute
"""

import argparse
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).parent.parent

# 将 scripts/ 加入 sys.path
sys.path.insert(0, str(ROOT / "scripts"))

from core.config import load_config, parse_ticker, CONFIG_PATH
from core.market import get_all_tickers, get_all_tickers_with_names, get_ticker_name
from core.display import format_ratio_display, format_ticker_line


# === LLM Prompt 模板 ===

PROMPT_ANALYSIS_TEMPLATE = """你是量比分析专家。给定以下数据：
- 标的: {ticker}
- 当前价: {price} ({change_pct:+.2f}%)
- 量比: {ratio}
- 近5日均量: {avg_vol}

请用中文简短分析：
1. 量比异常的原因
2. 是否构成买入/卖出信号
3. 风险提示
限制100字以内。"""


def query_ticker(ticker: str, analyze: bool = False) -> dict:
    """查询单个标的的量比"""
    from compute import compute_ticker, get_latest_snapshot

    result = compute_ticker(ticker)
    if not result:
        return None

    snapshot = get_latest_snapshot(ticker)
    if snapshot:
        result["price"] = snapshot.get("price", 0)
        result["change_pct"] = snapshot.get("change_pct", 0)

    if analyze:
        from llm import call_llm
        prompt = PROMPT_ANALYSIS_TEMPLATE.format(
            ticker=ticker,
            price=result.get("price", 0),
            change_pct=result.get("change_pct", 0),
            ratio=result.get("ratio", 0),
            avg_vol=result.get("volume_avg5", 0),
        )
        result["analysis"] = call_llm(prompt)

    return result


def scan_holdings() -> list:
    """扫描所有持仓"""
    from compute import compute_all

    results = compute_all()
    return results


def scan_market(market: str, min_ratio: float = 2.0) -> list:
    """扫描市场中放量标的"""
    from compute import compute_all

    market_suffix = {"US": ".US", "HK": ".HK", "CN": (".SH", ".SZ")}
    suffix = market_suffix.get(market.upper(), ".US")

    results = compute_all()

    if isinstance(suffix, tuple):
        return [r for r in results if r.get("ratio", 0) >= min_ratio and r["ticker"].endswith(suffix)]
    else:
        return [r for r in results if r.get("ratio", 0) >= min_ratio and r["ticker"].endswith(suffix)]


def format_ticker_output(result: dict, with_analysis: bool = False) -> str:
    """格式化单个标的输出"""
    ticker = result.get("ticker", "")
    name = result.get("name", ticker)
    price = result.get("price", 0)
    change = result.get("change_pct", 0)
    ratio = result.get("ratio", 0)
    signal_detail = result.get("signal_detail", "")

    output = f"{format_ticker_line(ticker, name, change, ratio)}  ${price:.2f}"

    if signal_detail:
        output += f"\n  信号: {signal_detail}"

    if with_analysis and result.get("analysis"):
        output += f"\n  [LLM分析] {result['analysis']}"

    return output


# === 新增命令 ===

def cmd_status():
    """系统健康状态检查"""
    print("=== 系统状态 ===")

    # WebSocket 采集进程
    pid_file = ROOT / "logs" / "ws_collect.pid"
    ws_ok = False
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            os.kill(pid, 0)
            ws_ok = True
            # 检查最近采集时间
            latest_time = _get_latest_snapshot_time()
            print(f"  WebSocket: ✅ PID {pid}, 最近采集 {latest_time}")
        except (ValueError, OSError):
            print(f"  WebSocket: ❌ PID 文件存在但进程不存活")
    else:
        print(f"  WebSocket: ❌ 未运行")

    # Cron 任务
    try:
        result = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=5)
        cron_lines = [l for l in result.stdout.split("\n") if "volume-ratio" in l]
        if cron_lines:
            print(f"  Cron 任务: ✅ {len(cron_lines)} 个活跃")
        else:
            print(f"  Cron 任务: ❌ 未配置")
    except subprocess.TimeoutExpired:
        print(f"  Cron 任务: ⚠️ 检查超时")

    # 飞书推送
    config = load_config()
    feishu = config.get("feishu", {})
    if feishu.get("webhook_url") or feishu.get("app_id"):
        print(f"  飞书推送: ✅ 已配置")
    else:
        print(f"  飞书推送: ❌ 未配置")

    # LLM
    llm = config.get("llm", {})
    model = llm.get("model", "未配置")
    print(f"  LLM:       ✅ {model}")

    # 数据库
    db_path = ROOT / "data" / "ratios.db"
    if db_path.exists():
        try:
            with sqlite3.connect(db_path, timeout=5) as conn:
                count = conn.execute("SELECT COUNT(*) FROM volume_ratios").fetchone()[0]
                size = db_path.stat().st_size
                print(f"  数据库:    {count:,} 条记录 ({size // 1024}KB)")
        except sqlite3.Error:
            print(f"  数据库:    ⚠️ 读取失败")
    else:
        print(f"  数据库:    ❌ 不存在")

    # 快照存储
    snapshot_dir = ROOT / "data" / "snapshots"
    if snapshot_dir.exists():
        total_size = sum(f.stat().st_size for f in snapshot_dir.rglob("*") if f.is_file())
        file_count = len(list(snapshot_dir.rglob("*")))
        print(f"  快照文件:  {file_count} 个 ({total_size // (1024*1024)}MB)")
    else:
        print(f"  快照文件:  ❌ 目录不存在")


def _get_latest_snapshot_time() -> str:
    """获取最近一次快照的时间"""
    snapshot_dir = ROOT / "data" / "snapshots"
    if not snapshot_dir.exists():
        return "无数据"

    latest = None
    for f in snapshot_dir.rglob("*.jsonl"):
        try:
            mtime = f.stat().st_mtime
            if latest is None or mtime > latest:
                latest = mtime
        except OSError:
            continue

    if latest:
        return datetime.fromtimestamp(latest).strftime("%H:%M:%S")
    return "无数据"


def cmd_history(ticker: str):
    """查询近 7 日量比趋势"""
    import sqlite3
    db_path = ROOT / "data" / "ratios.db"
    if not db_path.exists():
        print("数据库不存在")
        return

    config = load_config()
    name = get_ticker_name(config, ticker)
    cutoff = (datetime.now() - timedelta(days=7)).isoformat()

    try:
        with sqlite3.connect(db_path, timeout=10) as conn:
            rows = conn.execute("""
                SELECT timestamp, historical_ratio, price, change_pct, historical_signal,
                       historical_today_volume, historical_avg_volume, historical_sample_days
                FROM volume_ratios
                WHERE ticker = ? AND timestamp > ?
                ORDER BY timestamp
            """, (ticker, cutoff)).fetchall()
    except sqlite3.Error as e:
        print(f"查询失败: {e}")
        return

    if not rows:
        print(f"{ticker} {name}: 近 7 日无数据")
        return

    print(f"=== {ticker} {name} 近 7 日量比趋势 ===")
    print(f"{'时间':<20} {'量比':>6} {'价格':>10} {'涨跌':>8} {'样本':>6} {'信号':<10}")
    print("-" * 72)

    for ts, ratio, price, change, signal, today_vol, avg_vol, sample_days in rows:
        dt = datetime.fromisoformat(ts)
        direction = "↑" if change > 0 else "↓"
        print(f"{dt.strftime('%m-%d %H:%M'):<20} {ratio or 0:>6.2f} {price or 0:>10.2f} {direction}{abs(change or 0):>6.2f}% {sample_days or 0:>6} {signal or '-':<10}")

    print(f"\n共 {len(rows)} 条记录")


def cmd_signals():
    """查询今日信号"""
    import sqlite3
    db_path = ROOT / "data" / "ratios.db"
    if not db_path.exists():
        print("数据库不存在")
        return

    today = datetime.now().strftime("%Y-%m-%d")

    try:
        with sqlite3.connect(db_path, timeout=10) as conn:
            rows = conn.execute("""
                SELECT ticker, name, signal_type, ratio, price, change_pct, source, timestamp
                FROM signals
                WHERE timestamp LIKE ?
                ORDER BY timestamp
            """, (f"{today}%",)).fetchall()
    except sqlite3.Error as e:
        print(f"查询失败: {e}")
        return

    if not rows:
        print("今日无触发信号")
        return

    print(f"=== 今日信号 ({today}) ===")
    for ticker, name, sig_type, ratio, price, change, source, ts in rows:
        name = name or ticker
        direction = "↑" if change > 0 else "↓"
        dt = datetime.fromisoformat(ts).strftime("%H:%M:%S")
        ratio_display = format_ratio_display(ratio or 0)
        src_label = "日内" if source == "intraday" else "5日"
        print(f"  [{dt}] {ticker} {name} {direction}{abs(change):.1f}% {ratio_display} ({sig_type}) [{src_label}]")

    print(f"\n共 {len(rows)} 个信号")


def cmd_add_ticker(raw: str):
    """添加监控标的到 config.yaml"""
    import yaml

    ticker, name = parse_ticker(raw)
    config = load_config()

    # 判断市场
    if ticker.endswith(".US"):
        market_key = "us"
    elif ticker.endswith(".HK"):
        market_key = "hk"
    elif ticker.endswith(".SH") or ticker.endswith(".SZ"):
        market_key = "cn"
    else:
        print(f"无法识别市场: {ticker}")
        return

    watchlist = config.get("watchlist", {})
    market_list = watchlist.get(market_key, [])

    # 检查是否已存在（比较 ticker 部分）
    for existing in market_list:
        existing_ticker, _ = parse_ticker(existing)
        if existing_ticker == ticker:
            print(f"已存在: {ticker}")
            return

    # 添加
    market_list.append(raw)
    watchlist[market_key] = market_list
    config["watchlist"] = watchlist

    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        yaml.dump(config, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

    print(f"已添加: {ticker} {name}")


def cmd_remove_ticker(ticker: str):
    """从 config.yaml 移除监控标的"""
    import yaml

    config = load_config()
    watchlist = config.get("watchlist", {})
    removed = False

    for market_key in ["us", "hk", "cn"]:
        market_list = watchlist.get(market_key, [])
        new_list = []
        for raw in market_list:
            existing_ticker, _ = parse_ticker(raw)
            if existing_ticker == ticker:
                removed = True
            else:
                new_list.append(raw)
        watchlist[market_key] = new_list

    if not removed:
        print(f"未找到: {ticker}")
        return

    config["watchlist"] = watchlist
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        yaml.dump(config, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

    print(f"已移除: {ticker}")


def cmd_mute(ticker: str, duration: str):
    """静默指定标的"""
    import yaml

    # 解析时长 (2h, 30m, etc.)
    if duration.endswith("h"):
        hours = float(duration[:-1])
    elif duration.endswith("m"):
        hours = float(duration[:-1]) / 60
    else:
        hours = float(duration)

    until = datetime.now() + timedelta(hours=hours)

    config = load_config()
    mute_list = config.get("mute", {})
    mute_list[ticker] = until.isoformat()
    config["mute"] = mute_list

    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        yaml.dump(config, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

    print(f"已静默: {ticker} 至 {until.strftime('%H:%M:%S')} ({duration})")


def main():
    parser = argparse.ArgumentParser(description="跨市场量比监控 CLI")

    # 查询命令
    parser.add_argument("--ticker", type=str, help="查询单个标的")
    parser.add_argument("--scan", choices=["holdings"], help="扫描持仓列表")
    parser.add_argument("--market", type=str, help="扫描市场 (US/HK/CN)")
    parser.add_argument("--min-ratio", type=float, default=2.0, help="最小量比阈值")
    parser.add_argument("--analyze", action="store_true", help="调用 LLM AI 分析")
    parser.add_argument("--collect", action="store_true", help="先采集最新行情再查询")

    # 新增命令
    parser.add_argument("--status", action="store_true", help="系统健康状态")
    parser.add_argument("--history", type=str, help="近 7 日量比趋势")
    parser.add_argument("--signals", action="store_true", help="今日信号列表")
    parser.add_argument("--add", type=str, help="添加监控标的 (格式: CLF.US-克利夫兰)")
    parser.add_argument("--remove", type=str, help="移除监控标的")
    parser.add_argument("--mute", nargs=2, metavar=("TICKER", "DURATION"), help="静默标的 (例: CLF.US 2h)")

    args = parser.parse_args()

    if args.collect:
        from collect import collect_all
        print("=== 采集行情 ===")
        collect_all()
        print()

    if args.status:
        cmd_status()

    elif args.history:
        cmd_history(args.history)

    elif args.signals:
        cmd_signals()

    elif args.add:
        cmd_add_ticker(args.add)

    elif args.remove:
        cmd_remove_ticker(args.remove)

    elif args.mute:
        cmd_mute(args.mute[0], args.mute[1])

    elif args.ticker:
        print("=== 查询单个标的 ===")
        result = query_ticker(args.ticker, analyze=args.analyze)
        if result:
            print(format_ticker_output(result, with_analysis=args.analyze))
        else:
            print(f"无法获取 {args.ticker} 的数据", file=sys.stderr)
            sys.exit(1)

    elif args.scan == "holdings":
        print("=== 扫描持仓 ===")
        results = scan_holdings()
        if not results:
            print("无数据")
            sys.exit(1)

        for r in sorted(results, key=lambda x: x.get("ratio", 0), reverse=True):
            print(format_ticker_output(r))
        print(f"\n共 {len(results)} 个标的")

    elif args.market:
        print(f"=== 扫描 {args.market} 市场 (量比≥{args.min_ratio}) ===")
        results = scan_market(args.market, min_ratio=args.min_ratio)
        if not results:
            print("无放量标的")
            sys.exit(1)

        for r in sorted(results, key=lambda x: x.get("ratio", 0), reverse=True):
            print(format_ticker_output(r))
        print(f"\n共 {len(results)} 个放量标的")

    else:
        parser.print_help()
        print("\n=== 示例 ===")
        print("python3 cli.py --status                          # 系统状态")
        print("python3 cli.py --ticker CLF.US --analyze         # 查询+AI分析")
        print("python3 cli.py --scan holdings                   # 扫描持仓")
        print("python3 cli.py --history CLF.US                  # 历史量比")
        print("python3 cli.py --signals                         # 今日信号")
        print("python3 cli.py --add CLF.US-克利夫兰             # 添加标的")
        print("python3 cli.py --remove CLF.US                   # 移除标的")
        print("python3 cli.py --mute CLF.US 2h                  # 静默2小时")


if __name__ == "__main__":
    main()
