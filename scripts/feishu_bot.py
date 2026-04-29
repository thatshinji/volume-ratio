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
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import lark_oapi as lark
from lark_oapi.api.im.v1 import *
from lark_oapi.ws import Client as WsClient

from core.config import load_config
from core.market import get_all_tickers_with_names, get_ticker_name
from core.display import format_ratio_display, format_ticker_line, build_market_table, build_brief_elements

# 全局变量
running = threading.Event()
running.set()


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
    """构建系统状态卡片"""
    from cli import _get_latest_snapshot_time

    config = load_config()
    feishu = config.get("feishu", {})
    llm = config.get("llm", {})
    db_path = ROOT / "data" / "ratios.db"

    lines = []

    # WebSocket
    pid_file = ROOT / "logs" / "ws_collect.pid"
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            os.kill(pid, 0)
            latest = _get_latest_snapshot_time()
            lines.append(f"**WebSocket:** ✅ PID {pid}, 最近采集 {latest}")
        except (ValueError, OSError):
            lines.append("**WebSocket:** ❌ PID 文件存在但进程不存活")
    else:
        lines.append("**WebSocket:** ❌ 未运行")

    # LLM
    llm_model = llm.get('model', '未配置')
    lines.append(f"**LLM:** ✅ {llm_model}")

    # LLM 调用次数
    if db_path.exists():
        try:
            with sqlite3.connect(db_path, timeout=5) as conn:
                today = datetime.now().strftime("%Y-%m-%d")
                row = conn.execute(
                    "SELECT COUNT(*) FROM llm_calls WHERE timestamp LIKE ?",
                    (f"{today}%",)
                ).fetchone()
                llm_count = row[0] if row else 0
                lines.append(f"**LLM 调用:** 今日 {llm_count} 次")
        except sqlite3.Error:
            pass

    # 数据库
    if db_path.exists():
        try:
            with sqlite3.connect(db_path, timeout=5) as conn:
                count = conn.execute("SELECT COUNT(*) FROM volume_ratios").fetchone()[0]
                lines.append(f"**数据库:** {count:,} 条记录")
        except sqlite3.Error:
            lines.append("**数据库:** ⚠️ 读取失败")

    # 快照
    snapshot_dir = ROOT / "data" / "snapshots"
    if snapshot_dir.exists():
        total_size = sum(f.stat().st_size for f in snapshot_dir.rglob("*") if f.is_file())
        lines.append(f"**快照:** {total_size // (1024*1024)}MB")

    return {
        "config": {"wide_screen_mode": True},
        "header": {"title": {"tag": "plain_text", "content": "📊 系统状态"}},
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(lines)}}
        ]
    }


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

    lines = [f"**扫描时间:** {datetime.now().strftime('%H:%M:%S')}", ""]

    us = [r for r in sorted_results if r["ticker"].endswith(".US")]
    hk = [r for r in sorted_results if r["ticker"].endswith(".HK")]
    cn = [r for r in sorted_results if r["ticker"].endswith((".SH", ".SZ"))]

    for label, tickers in [("🇺🇸 美股", us), ("🇭🇰 港股", hk), ("🇨🇳 A股", cn)]:
        if not tickers:
            continue
        lines.append(f"**{label}:**")
        for r in tickers:
            name = r.get("name", r["ticker"])
            lines.append(format_ticker_line(r["ticker"], name, r.get("change_pct", 0), r.get("ratio", 0)))
        lines.append("")

    return {
        "config": {"wide_screen_mode": True},
        "header": {"title": {"tag": "plain_text", "content": "📊 量比扫描"}},
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(lines)}}
        ]
    }


def build_signals_card() -> dict:
    """构建今日信号卡片"""
    import sqlite3

    db_path = ROOT / "data" / "ratios.db"
    today = datetime.now().strftime("%Y-%m-%d")

    if not db_path.exists():
        content = "数据库不存在"
    else:
        try:
            with sqlite3.connect(db_path, timeout=10) as conn:
                rows = conn.execute("""
                    SELECT ticker, name, signal_type, ratio, price, change_pct, source, timestamp
                    FROM signals WHERE timestamp LIKE ? ORDER BY timestamp
                """, (f"{today}%",)).fetchall()
        except sqlite3.Error:
            rows = []

        if not rows:
            content = "今日无触发信号"
        else:
            lines = [f"**{today}** 共 {len(rows)} 个信号", ""]
            for ticker, name, sig_type, ratio, price, change, source, ts in rows:
                name = name or ticker
                direction = "↑" if change > 0 else "↓"
                dt = datetime.fromisoformat(ts).strftime("%H:%M")
                ratio_display = format_ratio_display(ratio or 0)
                src = "日内" if source == "intraday" else "5日"
                lines.append(f"[{dt}] {ticker} {name} {direction}{abs(change):.1f}% {ratio_display} ({sig_type}) [{src}]")
            content = "\n".join(lines)

    return {
        "config": {"wide_screen_mode": True},
        "header": {"title": {"tag": "plain_text", "content": "📈 今日信号"}},
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": content}}
        ]
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

    lines = []
    # 持仓
    lines.append(f"**持仓 ({len(result['positions'])}):** {', '.join(result['positions']) or '无'}")
    lines.append(f"**自选股 ({len(result['watchlist'])}):** {', '.join(result['watchlist']) or '无'}")
    lines.append("")

    # 变更
    if result["added"]:
        lines.append(f"**新增:** {', '.join(result['added'])}")
    if result["removed"]:
        lines.append(f"**移除:** {', '.join(result['removed'])}")
    if not result["added"] and not result["removed"]:
        lines.append("**无变更**")
    lines.append("")

    # 最终列表
    for market_label, key in [("🇺🇸 美股", "us"), ("🇭🇰 港股", "hk"), ("🇨🇳 A股", "cn")]:
        tickers = result["final"].get(key, [])
        if tickers:
            lines.append(f"**{market_label} ({len(tickers)}):** {', '.join(tickers)}")

    return {
        "config": {"wide_screen_mode": True},
        "header": {"title": {"tag": "plain_text", "content": "🔄 同步监控列表"}},
        "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(lines)}}],
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


def _check_component_status() -> dict:
    """检查各组件运行状态"""
    import sqlite3

    status = {}

    # WebSocket 采集
    ws_pid_file = ROOT / "logs" / "ws_collect.pid"
    if ws_pid_file.exists():
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
    if bot_pid_file.exists():
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


def handle_command(client: lark.Client, chat_id: str, text: str):
    """处理用户指令"""
    text = text.strip()
    print(f"[bot] 收到指令: {text}", flush=True)

    if text == "/start":
        try:
            subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "start_all.py")],
                capture_output=True, text=True, timeout=60
            )
            card = build_start_card()
            send_card(client, chat_id, card)
        except subprocess.TimeoutExpired:
            send_text(client, chat_id, "启动超时（>60秒），请检查日志")
        except Exception as e:
            send_text(client, chat_id, f"启动失败: {e}")

    elif text == "/stop":
        try:
            # 先发送结果卡片，再执行关停（因为关停会杀掉机器人自身）
            card = build_stop_card()
            send_card(client, chat_id, card)
            import time
            time.sleep(1)  # 等待卡片发送完成
            subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "stop_all.py")],
                capture_output=True, text=True, timeout=60
            )
        except subprocess.TimeoutExpired:
            send_text(client, chat_id, "关停超时（>60秒），请检查日志")
        except Exception as e:
            send_text(client, chat_id, f"关停失败: {e}")

    elif text == "/status":
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
        import io
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            cmd_add_ticker(raw)
            output = sys.stdout.getvalue()
        finally:
            sys.stdout = old_stdout
        send_text(client, chat_id, output.strip())

    elif text.startswith("/remove "):
        ticker = text[8:].strip()
        from cli import cmd_remove_ticker
        import io
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            cmd_remove_ticker(ticker)
            output = sys.stdout.getvalue()
        finally:
            sys.stdout = old_stdout
        send_text(client, chat_id, output.strip())

    elif text.startswith("/mute "):
        parts = text[6:].strip().split()
        if len(parts) >= 2:
            from cli import cmd_mute
            import io
            old_stdout = sys.stdout
            sys.stdout = io.StringIO()
            try:
                cmd_mute(parts[0], parts[1])
                output = sys.stdout.getvalue()
            finally:
                sys.stdout = old_stdout
            send_text(client, chat_id, output.strip())
        else:
            send_text(client, chat_id, "格式: /mute TICKER DURATION (例: /mute CLF.US 2h)")

    elif text.startswith("/history "):
        ticker = text[9:].strip()
        from cli import cmd_history
        import io
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            cmd_history(ticker)
            output = sys.stdout.getvalue()
        finally:
            sys.stdout = old_stdout
        if len(output) > 4000:
            output = output[:4000] + "\n...(截断)"
        send_text(client, chat_id, output.strip())

    elif text == "/sync":
        card = build_sync_card()
        send_card(client, chat_id, card)

    else:
        send_text(client, chat_id, f"未知指令: {text}\n\n可用指令:\n/start - 启动量比系统\n/stop - 关停量比系统\n/sync - 同步长桥持仓+自选股\n/status - 系统状态\n/scan - 量比扫描\n/signals - 今日信号\n/brief - 量比简报\n/add CLF.US-名称 - 添加标的\n/remove CLF.US - 移除标的\n/mute CLF.US 2h - 静默\n/history CLF.US - 历史量比")


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
                print(f"[bot] 收到消息: {text}", flush=True)
                handle_command(api_client, chat_id, text)
        except Exception as e:
            print(f"[bot] 消息处理异常: {e}", flush=True)

    # 注册事件处理器
    event_handler = lark.EventDispatcherHandler.builder(
        "", ""  # encrypt_key, verification_token（长连接模式不需要）
    ).register_p2_im_message_receive_v1(handle_p2_im_message_receive_v1).build()

    # 创建 WebSocket 客户端
    ws_client = WsClient(
        cfg["app_id"],
        cfg["app_secret"],
        event_handler=event_handler,
        log_level=lark.LogLevel.INFO,
    )

    def signal_handler(signum, frame):
        print("\n[bot] 收到退出信号，正在关闭...", flush=True)
        running.clear()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    print("[bot] 飞书机器人启动，等待消息...", flush=True)
    ws_client.start()


if __name__ == "__main__":
    main()
