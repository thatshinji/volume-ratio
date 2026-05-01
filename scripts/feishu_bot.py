#!/usr/bin/env python3
"""
飞书自建应用机器人 - WebSocket 长连接模式
接收用户指令，发送富文本卡片

Usage:
    python3 feishu_bot.py              # 前台运行
    python3 feishu_bot.py --daemon     # 后台守护进程
"""

import json
import os
import signal
import sqlite3
import subprocess
import sys
import threading
import time
import fcntl
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
GIB = 1024 * 1024 * 1024
SNAPSHOT_MAX_BYTES = 3 * GIB
DB_MAX_BYTES = 1 * GIB

import lark_oapi as lark
from lark_oapi.api.im.v1 import *
from lark_oapi.ws import Client as WsClient
from lark_oapi.ws.enum import MessageType
from lark_oapi.core.json import JSON
from lark_oapi.core.const import UTF_8

from core.config import load_config, parse_ticker, remove_ticker_from_config, save_config
from core.market import get_all_tickers_with_names, get_ticker_name, get_market
from core.display import format_ratio_display, format_ticker_line, build_market_table, build_brief_elements, format_size

# 全局变量
running = threading.Event()
running.set()
_instance_lock_file = None
_processed_messages = {}
_processed_lock = threading.Lock()
_stdout_capture_lock = threading.Lock()
MESSAGE_DEDUPE_TTL = 600


def acquire_instance_lock() -> bool:
    """确保只有一个飞书机器人实例消费事件。"""
    global _instance_lock_file
    lock_path = ROOT / "logs" / "feishu_bot.lock"
    lock_path.parent.mkdir(exist_ok=True)
    _instance_lock_file = open(lock_path, "a+")
    try:
        fcntl.flock(_instance_lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _instance_lock_file.seek(0)
        _instance_lock_file.truncate()
        _instance_lock_file.write(str(os.getpid()))
        _instance_lock_file.flush()
        return True
    except BlockingIOError:
        print("[bot] 已有飞书机器人实例运行，当前进程退出", flush=True)
        _instance_lock_file.close()
        _instance_lock_file = None
        return False


def _message_dedupe_key(data, msg, text: str) -> str:
    message_id = getattr(msg, "message_id", "") or ""
    if message_id:
        return f"message:{message_id}"
    header = getattr(data, "header", None)
    event_id = getattr(header, "event_id", "") or ""
    if event_id:
        return f"event:{event_id}"
    chat_id = getattr(msg, "chat_id", "") or ""
    create_time = getattr(msg, "create_time", "") or ""
    return f"fallback:{chat_id}:{create_time}:{text}"


def mark_message_seen(key: str) -> bool:
    """返回 True 表示首次看到；False 表示短时间内重复投递。"""
    now = time.time()
    with _processed_lock:
        expired = [k for k, ts in _processed_messages.items() if now - ts > MESSAGE_DEDUPE_TTL]
        for k in expired:
            del _processed_messages[k]
        if key in _processed_messages:
            return False
        _processed_messages[key] = now
        return True


def capture_stdout(func, *args) -> str:
    """串行捕获 CLI helper 的 stdout，避免并发事件改写全局 sys.stdout。"""
    import io
    with _stdout_capture_lock:
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            func(*args)
            return sys.stdout.getvalue()
        finally:
            sys.stdout = old_stdout


def get_feishu_config() -> dict:
    """获取飞书配置"""
    config = load_config()
    feishu = config.get("feishu", {})
    app_id = feishu.get("app_id", "")
    app_secret = feishu.get("app_secret", "")
    if not app_id or not app_secret:
        print("[bot] 飞书 app_id/app_secret 未配置", flush=True)
        sys.exit(1)
    return {"app_id": app_id, "app_secret": app_secret}


def create_client() -> lark.Client:
    """创建飞书 API 客户端"""
    cfg = get_feishu_config()
    return lark.Client.builder() \
        .app_id(cfg["app_id"]) \
        .app_secret(cfg["app_secret"]) \
        .log_level(lark.LogLevel.INFO) \
        .build()


def send_text(client: lark.Client, chat_id: str, text: str):
    """发送文本消息"""
    content = json.dumps({"text": text})
    req = CreateMessageRequest.builder() \
        .receive_id_type("chat_id") \
        .request_body(CreateMessageRequestBody.builder()
            .receive_id(chat_id)
            .msg_type("text")
            .content(content)
            .build()) \
        .build()

    resp = client.im.v1.message.create(req)
    if resp.success():
        print(f"[bot] 文本发送成功 -> {chat_id}", flush=True)
    else:
        print(f"[bot] 文本发送失败: code={resp.code} msg={resp.msg}", flush=True)


def send_card(client: lark.Client, chat_id: str, card: dict):
    """发送卡片消息"""
    content = json.dumps(card)
    req = CreateMessageRequest.builder() \
        .receive_id_type("chat_id") \
        .request_body(CreateMessageRequestBody.builder()
            .receive_id(chat_id)
            .msg_type("interactive")
            .content(content)
            .build()) \
        .build()

    resp = client.im.v1.message.create(req)
    if resp.success():
        print(f"[bot] 卡片发送成功 -> {chat_id}", flush=True)
    else:
        print(f"[bot] 卡片发送失败: code={resp.code} msg={resp.msg}", flush=True)


def build_status_card() -> dict:
    """构建系统状态卡片：服务 + 数据 + 市场 + 算法 + 推送健康。"""
    from core.market import get_all_tickers, get_market, is_market_trading

    config = load_config()
    llm = config.get("llm", {})
    db_path = ROOT / "data" / "ratios.db"
    tickers = get_all_tickers(config)
    now = datetime.now()

    service_status = _check_component_status()
    schema_version = "-"
    ratio_count = signal_count = llm_count = 0
    db_size = db_path.stat().st_size if db_path.exists() else 0
    latest_signal = None
    algo = {
        "total": len(tickers),
        "historical_ready": 0,
        "historical_insufficient": 0,
        "intraday_ready": 0,
        "latest_calc": None,
    }

    if db_path.exists():
        try:
            with sqlite3.connect(db_path, timeout=5) as conn:
                row = conn.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchone()
                schema_version = row[0] if row else "legacy"
                ratio_count = conn.execute("SELECT COUNT(*) FROM volume_ratios").fetchone()[0]
                today = now.strftime("%Y-%m-%d")
                signal_count = conn.execute(
                    "SELECT COUNT(*) FROM signals WHERE timestamp LIKE ?",
                    (f"{today}%",)
                ).fetchone()[0]
                llm_count = conn.execute(
                    "SELECT COUNT(*) FROM llm_calls WHERE timestamp LIKE ?",
                    (f"{today}%",)
                ).fetchone()[0]
                latest_signal = conn.execute("""
                    SELECT ticker, signal_type, source, timestamp
                    FROM signals
                    ORDER BY timestamp DESC
                    LIMIT 1
                """).fetchone()
                rows = conn.execute("""
                    SELECT ticker, MAX(timestamp), historical_ratio, historical_sample_days,
                           intraday_ratio, historical_signal
                    FROM volume_ratios
                    GROUP BY ticker
                """).fetchall()
                for ticker, ts, hist_ratio, sample_days, intraday_ratio, hist_signal in rows:
                    if (sample_days or 0) >= 3 and (hist_ratio or 0) > 0 and not str(hist_signal or "").startswith("样本不足"):
                        algo["historical_ready"] += 1
                    else:
                        algo["historical_insufficient"] += 1
                    if (intraday_ratio or 0) > 0:
                        algo["intraday_ready"] += 1
                    if ts and (algo["latest_calc"] is None or ts > algo["latest_calc"][1]):
                        algo["latest_calc"] = (ticker, ts, hist_ratio or 0, intraday_ratio or 0)
        except sqlite3.Error:
            schema_version = "读取失败"

    snapshot_summary = _get_snapshot_summary(tickers)
    snapshot_size = _get_snapshot_size()
    params = config.get("params", {})

    lines = [f"**检查时间:** {now.strftime('%Y-%m-%d %H:%M:%S')}", ""]

    lines.append("**服务**")
    for key, label in [("ws", "WebSocket"), ("bot", "飞书机器人"), ("cron", "Cron"), ("db", "数据库")]:
        icon, detail = service_status.get(key, ("❓", "未知"))
        if key == "db":
            db_warn = "，接近上限" if db_size >= DB_MAX_BYTES * 0.8 else ""
            detail = f"schema {schema_version}，量比 {ratio_count:,} 条，{format_size(db_size)}/{format_size(DB_MAX_BYTES)}{db_warn}"
        lines.append(f"{icon} {label}: {detail}")
    lines.append(f"✅ LLM: {llm.get('model', '未配置')}，今日调用 {llm_count} 次")
    lines.append("")

    lines.append("**市场 / 数据新鲜度**")
    for market, label in [("US", "🇺🇸 US"), ("HK", "🇭🇰 HK"), ("CN", "🇨🇳 CN")]:
        trading = "交易中" if is_market_trading(market) else "休市"
        snap = snapshot_summary.get(market)
        if snap:
            age = _format_age((now - snap["mtime"]).total_seconds())
            lines.append(f"{label}: {trading}，最近 {snap['ticker']} {snap['time']}（{age}前）")
        else:
            lines.append(f"{label}: {trading}，无快照")
    snapshot_warn = "，接近上限" if snapshot_size >= SNAPSHOT_MAX_BYTES * 0.8 else ""
    lines.append(f"快照占用: {format_size(snapshot_size)} / {format_size(SNAPSHOT_MAX_BYTES)}{snapshot_warn}")
    lines.append("")

    lines.append("**算法**")
    lines.append(f"监控标的: {algo['total']} 个")
    lines.append(f"5日量比可用: {algo['historical_ready']} 个，样本不足: {algo['historical_insufficient']} 个")
    lines.append(f"日内量比可用: {algo['intraday_ready']} 个")
    if algo["latest_calc"]:
        ticker, ts, hist_ratio, intraday_ratio = algo["latest_calc"]
        calc_time = datetime.fromisoformat(ts).strftime("%H:%M:%S")
        lines.append(f"最新计算: {ticker} 5日 {hist_ratio:.2f} / 日内 {intraday_ratio:.2f} ({calc_time})")
    lines.append("")

    lines.append("**信号 / 配置**")
    if latest_signal:
        ticker, sig_type, source, ts = latest_signal
        src_label = "日内+5日" if source == "mixed" else ("日内" if source == "intraday" else "5日")
        sig_time = datetime.fromisoformat(ts).strftime("%H:%M:%S")
        lines.append(f"今日信号: {signal_count} 个，最近 {ticker} {sig_type} [{src_label}] {sig_time}")
    else:
        lines.append(f"今日信号: {signal_count} 个，最近无信号")
    lines.append(
        "参数: "
        f"历史 {params.get('volume_ratio_window', 5)}日，"
        f"日内 {params.get('intraday_signal_window_minutes', 5)}m/"
        f"基线 {params.get('intraday_baseline_minutes', 30)}m/"
        f"{params.get('intraday_baseline_method', 'mean')}，"
        f"阈值 H>{params.get('alert_threshold', 2.0)} / I>{params.get('intraday_alert_threshold', 1.5)}"
    )

    return {
        "config": {"wide_screen_mode": True},
        "header": {"title": {"tag": "plain_text", "content": f"📊 量比系统状态 {now.strftime('%H:%M')}"}},
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(lines)}}
        ]
    }


def _get_snapshot_summary(tickers: list) -> dict:
    """按市场返回最近 JSONL 快照文件信息。"""
    snapshot_dir = ROOT / "data" / "snapshots"
    summary = {}
    if not snapshot_dir.exists():
        return summary

    ticker_set = {t.replace(".", "_"): t for t in tickers}
    for path in snapshot_dir.glob("*/*.jsonl"):
        stem_parts = path.stem.rsplit("_", 1)
        if len(stem_parts) != 2:
            continue
        ticker_key = stem_parts[0]
        ticker = ticker_set.get(ticker_key, ticker_key.replace("_", "."))
        market = path.parent.name
        try:
            mtime = datetime.fromtimestamp(path.stat().st_mtime)
        except OSError:
            continue
        current = summary.get(market)
        if current is None or mtime > current["mtime"]:
            summary[market] = {
                "ticker": ticker,
                "mtime": mtime,
                "time": mtime.strftime("%H:%M:%S"),
            }
    return summary


def _get_snapshot_size() -> int:
    snapshot_dir = ROOT / "data" / "snapshots"
    if not snapshot_dir.exists():
        return 0
    return sum(f.stat().st_size for f in snapshot_dir.rglob("*") if f.is_file())


def _format_age(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}秒"
    if seconds < 3600:
        return f"{seconds // 60}分钟"
    return f"{seconds // 3600}小时{(seconds % 3600) // 60}分钟"


def build_scan_card() -> dict:
    """构建量比扫描卡片"""
    from compute import compute_all

    results = compute_all()
    if not results:
        return {
            "config": {"wide_screen_mode": True},
            "header": {"title": {"tag": "plain_text", "content": "📊 量比扫描"}},
            "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": "无数据"}}]
        }

    sorted_results = sorted(results, key=lambda x: x.get("ratio", 0), reverse=True)

    us = [r for r in sorted_results if r["ticker"].endswith(".US")]
    hk = [r for r in sorted_results if r["ticker"].endswith(".HK")]
    cn = [r for r in sorted_results if r["ticker"].endswith((".SH", ".SZ"))]

    elements = [
        {"tag": "markdown", "content": f"**扫描时间:** {datetime.now().strftime('%H:%M:%S')}"},
    ]

    for label, tickers in [("🇺🇸 美股", us), ("🇭🇰 港股", hk), ("🇨🇳 A股", cn)]:
        if not tickers:
            continue
        elements.extend(build_market_table(label, tickers))

    return {
        "config": {"wide_screen_mode": True},
        "header": {"title": {"tag": "plain_text", "content": "📊 量比扫描"}},
        "elements": elements,
    }


def build_signals_card() -> dict:
    """构建今日信号卡片"""
    import sqlite3

    db_path = ROOT / "data" / "ratios.db"
    today = datetime.now().strftime("%Y-%m-%d")

    if not db_path.exists():
        return {
            "config": {"wide_screen_mode": True},
            "header": {"title": {"tag": "plain_text", "content": "📈 今日信号"}},
            "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": "数据库不存在"}}],
        }

    try:
        with sqlite3.connect(db_path, timeout=10) as conn:
            rows = conn.execute("""
                SELECT ticker, name, signal_type, ratio, price, change_pct, source, timestamp
                FROM signals WHERE timestamp LIKE ? ORDER BY timestamp
            """, (f"{today}%",)).fetchall()
    except sqlite3.Error:
        rows = []

    if not rows:
        return {
            "config": {"wide_screen_mode": True},
            "header": {"title": {"tag": "plain_text", "content": "📈 今日信号"}},
            "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": "今日无触发信号"}}],
        }

    elements = [
        {"tag": "markdown", "content": f"**{today}** 共 {len(rows)} 个信号"},
    ]

    columns = [
        {"name": "time", "display_name": "时间", "width": "auto", "horizontal_align": "right", "data_type": "text"},
        {"name": "ticker", "display_name": "标的", "width": "auto", "horizontal_align": "left", "data_type": "text"},
        {"name": "change", "display_name": "涨跌", "width": "auto", "horizontal_align": "right", "data_type": "text"},
        {"name": "ratio", "display_name": "触发量比", "width": "auto", "horizontal_align": "right", "data_type": "text"},
        {"name": "signal", "display_name": "信号", "width": "auto", "horizontal_align": "left", "data_type": "text"},
        {"name": "source", "display_name": "来源", "width": "auto", "horizontal_align": "center", "data_type": "text"},
    ]

    table_rows = []
    for ticker, name, sig_type, ratio, price, change, source, ts in rows:
        name = name or ticker
        change = float(change or 0)
        direction = "↑" if change > 0 else ("↓" if change < 0 else "─")
        dt = datetime.fromisoformat(ts).strftime("%H:%M")
        ratio_display = format_ratio_display(ratio or 0)
        src = "日内+5日" if source == "mixed" else ("日内" if source == "intraday" else "5日")
        table_rows.append({
            "time": dt,
            "ticker": f"{ticker}-{name}",
            "change": f"{direction}{abs(change):.1f}%",
            "ratio": ratio_display,
            "signal": sig_type or "",
            "source": src,
        })

    elements.append({
        "tag": "table",
        "page_size": len(table_rows),
        "row_height": "low",
        "header_style": {
            "text_align": "left",
            "text_size": "normal",
            "background_style": "grey",
            "bold": True,
            "lines": 1,
        },
        "columns": columns,
        "rows": table_rows,
    })

    return {
        "config": {"wide_screen_mode": True},
        "header": {"title": {"tag": "plain_text", "content": "📈 今日信号"}},
        "elements": elements,
    }


def build_sync_card() -> dict:
    """构建同步结果卡片"""
    from longbridge_sync import run_sync

    try:
        result = run_sync()
    except Exception as e:
        return {
            "config": {"wide_screen_mode": True},
            "header": {"title": {"tag": "plain_text", "content": "❌ 同步失败"}},
            "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": f"错误: {e}"}}],
        }

    summary_lines = []
    summary_lines.append(f"**持仓 ({len(result['positions'])}):** {', '.join(result['positions']) or '无'}")
    summary_lines.append(f"**自选股 ({len(result['watchlist'])}):** {', '.join(result['watchlist']) or '无'}")
    summary_lines.append("")
    if result["added"]:
        summary_lines.append(f"**新增:** {', '.join(result['added'])}")
    if result["removed"]:
        summary_lines.append(f"**移除:** {', '.join(result['removed'])}")
    if not result["added"] and not result["removed"]:
        summary_lines.append("**无变更**")

    elements = [{"tag": "markdown", "content": "\n".join(summary_lines)}]

    # 最终列表用原生表格
    columns = [
        {"name": "ticker", "display_name": "标的", "width": "auto", "horizontal_align": "left", "data_type": "text"},
    ]
    for market_label, key in [("🇺🇸 美股", "us"), ("🇭🇰 港股", "hk"), ("🇨🇳 A股", "cn")]:
        tickers = result["final"].get(key, [])
        if not tickers:
            continue
        rows = [{"ticker": t} for t in tickers]
        elements.append({"tag": "markdown", "content": f"**{market_label} ({len(tickers)})**"})
        elements.append({
            "tag": "table",
            "page_size": len(rows),
            "row_height": "low",
            "header_style": {
                "text_align": "left",
                "text_size": "normal",
                "background_style": "grey",
                "bold": True,
                "lines": 1,
            },
            "columns": columns,
            "rows": rows,
        })

    return {
        "config": {"wide_screen_mode": True},
        "header": {"title": {"tag": "plain_text", "content": "🔄 同步监控列表"}},
        "elements": elements,
    }


def build_brief_card() -> dict:
    """构建量比简报卡片（飞书原生表格）"""
    from compute import compute_all

    results = compute_all()
    if not results:
        return {
            "config": {"wide_screen_mode": True},
            "header": {"title": {"tag": "plain_text", "content": "📋 量比简报"}},
            "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": "无数据"}}],
        }

    sorted_results = sorted(results, key=lambda x: x.get("ratio", 0), reverse=True)
    elements = build_brief_elements(sorted_results)

    return {
        "config": {"wide_screen_mode": True},
        "header": {"title": {"tag": "plain_text", "content": f"📋 量比简报 {datetime.now().strftime('%H:%M')}"}},
        "elements": elements,
    }


def build_watchlist_card() -> dict:
    """构建关注列表卡片（带删除按钮）"""
    config = load_config()
    elements = []

    for market, label in [("us", "🇺🇸 美股"), ("hk", "🇭🇰 港股"), ("cn", "🇨🇳 A股")]:
        items = config.get("watchlist", {}).get(market, [])
        if not items:
            continue

        elements.append({"tag": "markdown", "content": f"**{label}**"})

        rows = []
        for raw in items:
            ticker, name = parse_ticker(raw)
            rows.append({"ticker": f"{ticker}-{name}", "action": "删除"})

        elements.append({
            "tag": "table",
            "page_size": len(rows),
            "row_height": "low",
            "header_style": {
                "text_align": "left",
                "text_size": "normal",
                "background_style": "grey",
                "bold": True,
                "lines": 1,
            },
            "columns": [
                {"name": "ticker", "display_name": "标的", "width": "auto", "horizontal_align": "left", "data_type": "text"},
                {"name": "action", "display_name": "操作", "width": "auto", "horizontal_align": "center", "data_type": "text"},
            ],
            "rows": rows,
        })

        buttons = []
        for raw in items:
            ticker, name = parse_ticker(raw)
            buttons.append({
                "tag": "button",
                "text": {"tag": "plain_text", "content": f"❌ {ticker}"},
                "type": "danger",
                "value": {"action": "remove", "ticker": ticker, "name": name},
            })
        elements.append({"tag": "action", "actions": buttons})

    if not elements:
        elements = [{"tag": "div", "text": {"tag": "lark_md", "content": "关注列表为空"}}]

    return {
        "config": {"wide_screen_mode": True},
        "header": {"title": {"tag": "plain_text", "content": "📋 关注列表"}},
        "elements": elements,
    }


def build_allstock_card() -> dict:
    """构建全部股票分组列表卡片（一级导航）"""
    from longbridge_sync import fetch_other_groups

    try:
        groups = fetch_other_groups(exclude_names=["量比监控"])
    except Exception as e:
        return {
            "config": {"wide_screen_mode": True},
            "header": {"title": {"tag": "plain_text", "content": "📈 全部股票"}},
            "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": f"获取失败: {e}"}}],
        }

    if not groups:
        return {
            "config": {"wide_screen_mode": True},
            "header": {"title": {"tag": "plain_text", "content": "📈 全部股票"}},
            "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": "无其他自选股分组"}}],
        }

    # 分组列表
    lines = []
    for group_name, stocks in groups.items():
        lines.append(f"**📁 {group_name}** — {len(stocks)} 只")
    elements = [{"tag": "markdown", "content": "\n".join(lines)}]

    # 每个分组一个按钮
    buttons = []
    for group_name in groups.keys():
        buttons.append({
            "tag": "button",
            "text": {"tag": "plain_text", "content": f"📁 {group_name}"},
            "type": "primary",
            "value": {"action": "view_group", "group": group_name},
        })
    elements.append({"tag": "action", "actions": buttons})

    return {
        "config": {"wide_screen_mode": True},
        "header": {"title": {"tag": "plain_text", "content": "📈 全部股票"}},
        "elements": elements,
    }


def build_group_stocks_card(group_name: str) -> dict:
    """构建指定分组的股票列表卡片（二级导航，带添加+返回按钮）"""
    from longbridge_sync import _get_longbridge_context

    try:
        quote_ctx, _ = _get_longbridge_context()
        stocks = []
        for group in quote_ctx.watchlist():
            if group.name == group_name:
                stocks = [(sec.symbol, sec.name) for sec in group.securities]
                break
    except Exception as e:
        return {
            "config": {"wide_screen_mode": True},
            "header": {"title": {"tag": "plain_text", "content": f"📁 {group_name}"}},
            "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": f"获取失败: {e}"}}],
        }

    if not stocks:
        return {
            "config": {"wide_screen_mode": True},
            "header": {"title": {"tag": "plain_text", "content": f"📁 {group_name}"}},
            "elements": [
                {"tag": "div", "text": {"tag": "lark_md", "content": "该分组为空"}},
                {"tag": "action", "actions": [
                    {"tag": "button", "text": {"tag": "plain_text", "content": "⬅ 返回列表"},
                     "type": "default", "value": {"action": "back_to_groups"}},
                ]},
            ],
        }

    # 股票文本列表
    lines = []
    for ticker, name in stocks:
        lines.append(f"**{ticker}**-{name}")
    elements = [
        {"tag": "markdown", "content": f"**{group_name}**（{len(stocks)} 只）\n" + "\n".join(lines)},
    ]

    # 添加按钮（每行 5 个，分批）
    for i in range(0, len(stocks), 5):
        chunk = stocks[i:i + 5]
        buttons = []
        for ticker, name in chunk:
            buttons.append({
                "tag": "button",
                "text": {"tag": "plain_text", "content": f"➕ {ticker}"},
                "type": "primary",
                "value": {"action": "add_to_monitor", "ticker": ticker, "name": name, "group": group_name},
            })
        elements.append({"tag": "action", "actions": buttons})

    # 返回按钮
    elements.append({"tag": "action", "actions": [
        {"tag": "button", "text": {"tag": "plain_text", "content": "⬅ 返回列表"},
         "type": "default", "value": {"action": "back_to_groups"}},
    ]})

    return {
        "config": {"wide_screen_mode": True},
        "header": {"title": {"tag": "plain_text", "content": f"📁 {group_name}"}},
        "elements": elements,
    }


def handle_card_action(data) -> "P2CardActionTriggerResponse":
    """处理卡片按钮点击回调"""
    from longbridge_sync import remove_from_watchlist, add_to_monitor
    from lark_oapi.event.callback.model.p2_card_action_trigger import (
        P2CardActionTriggerResponse, CallBackToast, CallBackCard,
    )

    action = data.event.action
    value = action.value or {}
    action_type = value.get("action", "")

    if action_type == "remove":
        ticker = value.get("ticker", "")
        name = value.get("name", "")

        # 从 config.yaml 移除
        remove_ticker_from_config(ticker)

        # 从长桥自选股移除
        try:
            remove_from_watchlist(ticker)
        except Exception as e:
            print(f"[bot] 长桥删除失败: {e}", flush=True)

        print(f"[bot] 已移除: {ticker}-{name}", flush=True)

        resp = P2CardActionTriggerResponse()
        resp.toast = CallBackToast()
        resp.toast.type = "info"
        resp.toast.content = f"已移除 {ticker}-{name}"
        resp.card = CallBackCard()
        resp.card.type = "raw"
        resp.card.data = build_watchlist_card()
        return resp

    elif action_type == "add_to_monitor":
        ticker = value.get("ticker", "")
        name = value.get("name", "")
        group_name = value.get("group", "")

        # 添加到长桥"量比监控"分组
        success = False
        try:
            success = add_to_monitor(ticker, name)
        except Exception as e:
            print(f"[bot] 长桥添加失败: {e}", flush=True)

        if success:
            # 同步添加到 config.yaml
            config = load_config()
            market = get_market(ticker).lower()
            if market in ("us", "hk", "cn"):
                items = config.get("watchlist", {}).get(market, [])
                entry = f"{ticker}-{name}" if name else ticker
                if not any(item.startswith(ticker + "-") or item == ticker for item in items):
                    items.append(entry)
                    config["watchlist"][market] = items
                    save_config(config)
            print(f"[bot] 已添加到量比监控: {ticker}-{name}", flush=True)
        else:
            print(f"[bot] 添加失败: {ticker}", flush=True)

        resp = P2CardActionTriggerResponse()
        resp.toast = CallBackToast()
        resp.toast.type = "success" if success else "error"
        resp.toast.content = f"已添加 {ticker}-{name} 到量比监控" if success else f"添加失败: {ticker}"
        # 添加后留在当前分组页面
        resp.card = CallBackCard()
        resp.card.type = "raw"
        resp.card.data = build_group_stocks_card(group_name) if group_name else build_allstock_card()
        return resp

    elif action_type == "view_group":
        group_name = value.get("group", "")
        resp = P2CardActionTriggerResponse()
        resp.card = CallBackCard()
        resp.card.type = "raw"
        resp.card.data = build_group_stocks_card(group_name)
        return resp

    elif action_type == "back_to_groups":
        resp = P2CardActionTriggerResponse()
        resp.card = CallBackCard()
        resp.card.type = "raw"
        resp.card.data = build_allstock_card()
        return resp

    return None


def _check_component_status() -> dict:
    """检查各组件运行状态"""
    import sqlite3

    status = {}

    def locked_pid(lock_path: Path, pid_path: Path) -> str:
        if not lock_path.exists():
            return ""
        try:
            with open(lock_path, "r+") as lock_file:
                try:
                    fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    fcntl.flock(lock_file, fcntl.LOCK_UN)
                    return ""
                except BlockingIOError:
                    pid_text = lock_file.read().strip()
                    if pid_text:
                        pid_path.write_text(pid_text)
                    return pid_text
        except OSError:
            return ""

    # WebSocket 采集
    ws_pid_file = ROOT / "logs" / "ws_collect.pid"
    ws_lock_pid = locked_pid(ROOT / "logs" / "ws_collect.lock", ws_pid_file)
    if ws_lock_pid:
        status["ws"] = ("✅", f"运行中 (PID {ws_lock_pid})")
    elif ws_pid_file.exists():
        try:
            pid = int(ws_pid_file.read_text().strip())
            os.kill(pid, 0)
            status["ws"] = ("✅", f"运行中 (PID {pid})")
        except (ValueError, OSError):
            status["ws"] = ("❌", "PID 文件存在但进程不存活")
    else:
        status["ws"] = ("❌", "未运行")

    # 飞书机器人
    bot_pid_file = ROOT / "logs" / "feishu_bot.pid"
    bot_lock_pid = locked_pid(ROOT / "logs" / "feishu_bot.lock", bot_pid_file)
    if bot_lock_pid:
        status["bot"] = ("✅", f"运行中 (PID {bot_lock_pid})")
    elif bot_pid_file.exists():
        try:
            pid = int(bot_pid_file.read_text().strip())
            os.kill(pid, 0)
            status["bot"] = ("✅", f"运行中 (PID {pid})")
        except (ValueError, OSError):
            status["bot"] = ("❌", "PID 文件存在但进程不存活")
    else:
        status["bot"] = ("❌", "未运行")

    # Cron 任务
    try:
        result = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=10)
        cron_lines = [l for l in result.stdout.split("\n") if "volume-ratio" in l]
        if cron_lines:
            status["cron"] = ("✅", f"{len(cron_lines)} 个任务")
        else:
            status["cron"] = ("❌", "无任务")
    except Exception:
        status["cron"] = ("⚠️", "检查失败")

    # 数据库
    db_path = ROOT / "data" / "ratios.db"
    if db_path.exists():
        try:
            with sqlite3.connect(str(db_path), timeout=5) as conn:
                count = conn.execute("SELECT COUNT(*) FROM volume_ratios").fetchone()[0]
                status["db"] = ("✅", f"{count:,} 条记录")
        except sqlite3.Error:
            status["db"] = ("⚠️", "读取失败")
    else:
        status["db"] = ("❌", "数据库不存在")

    return status


def build_start_card() -> dict:
    """构建启动结果卡片"""
    status = _check_component_status()
    lines = []
    for key, label in [("ws", "WebSocket 采集"), ("bot", "飞书机器人"), ("cron", "Cron 任务"), ("db", "数据库")]:
        icon, detail = status.get(key, ("❓", "未知"))
        lines.append(f"{icon} **{label}**: {detail}")
    content = "\n".join(lines)

    return {
        "config": {"wide_screen_mode": True},
        "header": {"title": {"tag": "plain_text", "content": "🚀 启动量比系统"}},
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": content}}
        ]
    }


def build_stop_card() -> dict:
    """构建关停结果卡片"""
    status = _check_component_status()
    lines = ["关停指令已执行，以下为当前状态：", ""]
    for key, label in [("ws", "WebSocket 采集"), ("bot", "飞书机器人"), ("cron", "Cron 任务"), ("db", "数据库")]:
        icon, detail = status.get(key, ("❓", "未知"))
        lines.append(f"{icon} **{label}**: {detail}")
    lines.append("")
    lines.append("_机器人将在数秒后停止，如需重启请在终端执行 `python3 scripts/start_all.py`_")
    content = "\n".join(lines)

    return {
        "config": {"wide_screen_mode": True},
        "header": {"title": {"tag": "plain_text", "content": "🛑 关停量比系统"}},
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": content}}
        ]
    }


def run_service_command_async(
    client: lark.Client,
    chat_id: str,
    script_name: str,
    success_card_builder=None,
    timeout_text: str = "执行超时（>60秒），请检查日志",
    error_prefix: str = "执行失败",
    delay_seconds: float = 0,
):
    """后台执行服务管理脚本，避免阻塞飞书 WebSocket 事件回调。"""
    def worker():
        try:
            if delay_seconds > 0:
                time.sleep(delay_seconds)
            subprocess.run(
                [sys.executable, str(ROOT / "scripts" / script_name)],
                capture_output=True, text=True, timeout=60
            )
            if success_card_builder:
                send_card(client, chat_id, success_card_builder())
        except subprocess.TimeoutExpired:
            send_text(client, chat_id, timeout_text)
        except Exception as e:
            send_text(client, chat_id, f"{error_prefix}: {e}")

    thread = threading.Thread(target=worker, name=f"bot-{script_name}", daemon=True)
    thread.start()


def handle_command(client: lark.Client, chat_id: str, text: str):
    """处理用户指令"""
    text = text.strip()
    print(f"[bot] 收到指令: {text}", flush=True)

    if text == "/start":
        run_service_command_async(
            client, chat_id, "start_all.py",
            success_card_builder=build_start_card,
            timeout_text="启动超时（>60秒），请检查日志",
            error_prefix="启动失败",
        )

    elif text == "/stop":
        try:
            # 先发送结果卡片，再执行关停（因为关停会杀掉机器人自身）
            card = build_stop_card()
            send_card(client, chat_id, card)
            run_service_command_async(
                client, chat_id, "stop_all.py",
                timeout_text="关停超时（>60秒），请检查日志",
                error_prefix="关停失败",
                delay_seconds=1,
            )
        except Exception as e:
            send_text(client, chat_id, f"关停失败: {e}")

    elif text in ("/status", "/statsu"):
        card = build_status_card()
        send_card(client, chat_id, card)

    elif text == "/scan":
        card = build_scan_card()
        send_card(client, chat_id, card)

    elif text == "/signals":
        card = build_signals_card()
        send_card(client, chat_id, card)

    elif text == "/brief":
        card = build_brief_card()
        send_card(client, chat_id, card)

    elif text.startswith("/add "):
        raw = text[5:].strip()
        from cli import cmd_add_ticker
        output = capture_stdout(cmd_add_ticker, raw)
        send_text(client, chat_id, output.strip())

    elif text.startswith("/remove "):
        ticker = text[8:].strip()
        from cli import cmd_remove_ticker
        output = capture_stdout(cmd_remove_ticker, ticker)
        send_text(client, chat_id, output.strip())

    elif text.startswith("/mute "):
        parts = text[6:].strip().split()
        if len(parts) >= 2:
            from cli import cmd_mute
            output = capture_stdout(cmd_mute, parts[0], parts[1])
            send_text(client, chat_id, output.strip())
        else:
            send_text(client, chat_id, "格式: /mute TICKER DURATION (例: /mute CLF.US 2h)")

    elif text.startswith("/history "):
        ticker = text[9:].strip()
        from cli import cmd_history
        output = capture_stdout(cmd_history, ticker)
        if len(output) > 4000:
            output = output[:4000] + "\n...(截断)"
        send_text(client, chat_id, output.strip())

    elif text == "/sync":
        card = build_sync_card()
        send_card(client, chat_id, card)

    elif text == "/watchlist":
        card = build_watchlist_card()
        send_card(client, chat_id, card)

    elif text == "/allstock":
        card = build_allstock_card()
        send_card(client, chat_id, card)

    else:
        send_text(client, chat_id, f"未知指令: {text}\n\n可用指令:\n/start - 启动量比系统\n/stop - 关停量比系统\n/sync - 同步长桥持仓+自选股\n/watchlist - 关注列表（可删除）\n/allstock - 全部股票（可添加到量比）\n/status - 系统状态\n/scan - 量比扫描\n/signals - 今日信号\n/brief - 量比简报\n/add CLF.US-名称 - 添加标的\n/remove CLF.US - 移除标的\n/mute CLF.US 2h - 静默\n/history CLF.US - 历史量比")


def on_message(client: lark.Client, event: lark.EventDispatcherHandler):
    """消息事件处理"""
    pass  # 由 event handler 处理


def main():
    import argparse
    parser = argparse.ArgumentParser(description="飞书自建应用机器人")
    parser.add_argument("--daemon", action="store_true", help="后台守护进程运行")
    args = parser.parse_args()

    if args.daemon:
        pid = os.fork()
        if pid > 0:
            print(f"[bot] 后台运行，PID: {pid}", flush=True)
            sys.exit(0)

        os.setsid()
        pid = os.fork()
        if pid > 0:
            sys.exit(0)

        # 孙子进程：写入 PID 文件
        pid_file = ROOT / "logs" / "feishu_bot.pid"
        pid_file.parent.mkdir(exist_ok=True)
        pid_file.write_text(str(os.getpid()))

        log_dir = ROOT / "logs"
        log_dir.mkdir(exist_ok=True)

        devnull = os.open(os.devnull, os.O_RDONLY)
        os.dup2(devnull, 0)
        os.close(devnull)

        out_fd = os.open(log_dir / "feishu_bot.log", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        os.dup2(out_fd, 1)
        os.close(out_fd)

        err_fd = os.open(log_dir / "feishu_bot.err", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        os.dup2(err_fd, 2)
        os.close(err_fd)

        # 用 execv 替换进程，避免 asyncio kqueue 文件描述符问题
        # 注意：不带 --daemon 参数，避免无限循环
        os.execv(sys.executable, [sys.executable, str(Path(__file__).resolve())])

    if not acquire_instance_lock():
        return

    pid_file = ROOT / "logs" / "feishu_bot.pid"
    pid_file.parent.mkdir(exist_ok=True)
    pid_file.write_text(str(os.getpid()))

    cfg = get_feishu_config()
    api_client = create_client()

    # 消息事件处理
    def handle_p2_im_message_receive_v1(data):
        """处理接收到的消息"""
        try:
            msg = data.event.message
            chat_id = msg.chat_id
            msg_type = msg.message_type

            if msg_type == "text":
                content = json.loads(msg.content)
                text = content.get("text", "").strip()
                # 移除 @机器人 的提及
                if text.startswith("@"):
                    text = text.split(" ", 1)[-1] if " " in text else text
                dedupe_key = _message_dedupe_key(data, msg, text)
                if not mark_message_seen(dedupe_key):
                    print(f"[bot] 忽略重复消息: {text} ({dedupe_key})", flush=True)
                    return
                print(f"[bot] 收到消息: {text}", flush=True)
                handle_command(api_client, chat_id, text)
        except Exception as e:
            print(f"[bot] 消息处理异常: {e}", flush=True)

    # 注册事件处理器（消息 + 卡片回调）
    event_handler = lark.EventDispatcherHandler.builder(
        "", ""  # encrypt_key, verification_token（长连接模式不需要）
    ).register_p2_im_message_receive_v1(
        handle_p2_im_message_receive_v1
    ).register_p2_card_action_trigger(
        handle_card_action
    ).build()

    # 创建 WebSocket 客户端
    ws_client = WsClient(
        cfg["app_id"],
        cfg["app_secret"],
        event_handler=event_handler,
        log_level=lark.LogLevel.INFO,
    )

    # Monkey-patch: 让 WsClient 处理 CARD 消息（原版会静默丢弃）
    from lark_oapi.ws.const import HEADER_TYPE, HEADER_MESSAGE_ID, HEADER_TRACE_ID, HEADER_SUM, HEADER_SEQ, HEADER_BIZ_RT
    from lark_oapi.ws.model import Response as WsResponse

    original_handle = ws_client._handle_data_frame

    async def patched_handle(frame):
        hs = frame.headers
        msg_id = None
        sum_ = "1"
        seq = "0"
        type_ = None
        for h in hs:
            if h.key == HEADER_MESSAGE_ID:
                msg_id = h.value
            elif h.key == HEADER_SUM:
                sum_ = h.value
            elif h.key == HEADER_SEQ:
                seq = h.value
            elif h.key == HEADER_TYPE:
                type_ = h.value

        resp = WsResponse(code=200)

        try:
            pl = frame.payload
            if int(sum_) > 1:
                pl = ws_client._combine(msg_id, int(sum_), int(seq), pl)
                if pl is None:
                    return

            message_type = MessageType(type_)
            import time
            start = int(round(time.time() * 1000))
            if message_type == MessageType.EVENT:
                result = ws_client._event_handler.do_without_validation(pl)
            elif message_type == MessageType.CARD:
                result = ws_client._event_handler.do_without_validation(pl)
            else:
                return

            end = int(round(time.time() * 1000))
            header = hs.add()
            header.key = HEADER_BIZ_RT
            header.value = str(end - start)
            if result is not None:
                import base64
                resp.data = base64.b64encode(JSON.marshal(result).encode(UTF_8))
        except Exception as e:
            print(f"[bot] 消息处理异常: {e}", flush=True)
            resp = WsResponse(code=500)

        frame.payload = JSON.marshal(resp).encode(UTF_8)
        await ws_client._write_message(frame.SerializeToString())

    ws_client._handle_data_frame = patched_handle

    def signal_handler(signum, frame):
        print("\n[bot] 收到退出信号，正在关闭...", flush=True)
        running.clear()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    print("[bot] 飞书机器人启动，等待消息...", flush=True)
    ws_client.start()


if __name__ == "__main__":
    main()
