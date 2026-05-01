#!/usr/bin/env python3
"""
一键启动跨市场量比监控服务
启动 cron 任务 + WebSocket 采集进程 + 飞书机器人
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
LOG_DIR = ROOT / "logs"
VENV_PYTHON = ROOT / ".venv" / "bin" / "python"
SCRIPTS_DIR = ROOT / "scripts"


def add_cron(line: str):
    """添加 cron 任务（如果不存在）"""
    result = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=30)
    current = result.stdout
    if line in current:
        print(f"[start] cron 已存在: {line.strip()}")
        return
    new_cron = current + line + "\n"
    result = subprocess.run(["crontab", "-"], input=new_cron, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        print(f"[start] cron 添加失败: {line.strip()}")
        if result.stderr:
            print(result.stderr.strip())
        return
    print(f"[start] 已添加 cron: {line.strip()}")


def start_websocket():
    """通过 launcher 启动或确认 WebSocket 采集进程。"""
    launcher = SCRIPTS_DIR / "collect_ws_launcher.py"
    python_bin = "/usr/bin/python3"
    subprocess.run([python_bin, str(launcher)], check=False)
    pid_file = LOG_DIR / "ws_collect.pid"
    if pid_file.exists():
        print(f"[start] WebSocket PID: {pid_file.read_text().strip()}")
    else:
        print("[start] WebSocket 未启动，请检查 launcher.log / ws_collect.err")


def start_feishu_bot():
    """通过 launcher 启动或确认飞书机器人进程。"""
    launcher = SCRIPTS_DIR / "feishu_bot_launcher.py"
    python_bin = str(VENV_PYTHON) if VENV_PYTHON.exists() else sys.executable
    subprocess.run([python_bin, str(launcher)], check=False)
    pid_file = LOG_DIR / "feishu_bot.pid"
    if pid_file.exists():
        print(f"[start] 飞书机器人 PID: {pid_file.read_text().strip()}")
    else:
        print("[start] 飞书机器人未启动，请检查配置和 feishu_bot.err")


def main():
    print("=== 一键启动跨市场量比监控服务 ===")
    print()

    ws_launcher_script = SCRIPTS_DIR / "collect_ws_launcher.py"
    bot_launcher_script = SCRIPTS_DIR / "feishu_bot_launcher.py"
    alert_script = SCRIPTS_DIR / "alert.py"
    cleanup_script = SCRIPTS_DIR / "cleanup.py"
    python_bin = str(VENV_PYTHON) if VENV_PYTHON.exists() else sys.executable
    launcher_python = "/usr/bin/python3"  # launcher 必须用系统 Python（longbridge 未装在 venv）

    sync_script = SCRIPTS_DIR / "longbridge_sync.py"

    # 1. 添加 cron 任务
    print("[1/4] 配置 cron 任务...")
    add_cron(f"*/1 * * * 1-5 {launcher_python} {ws_launcher_script} >> {LOG_DIR}/launcher.log 2>&1")
    add_cron(f"*/1 * * * * {python_bin} {bot_launcher_script} >> {LOG_DIR}/launcher.log 2>&1")
    add_cron(f"*/1 * * * 1-5 {python_bin} {alert_script} >> {LOG_DIR}/alert.log 2>&1")
    add_cron(f"*/30 * * * 1-5 {python_bin} {alert_script} --brief >> {LOG_DIR}/brief.log 2>&1")
    add_cron(f"*/30 * * * 1-5 {python_bin} {sync_script} >> {LOG_DIR}/sync.log 2>&1")
    add_cron(f"0 * * * * {python_bin} {cleanup_script} >> {LOG_DIR}/cleanup.log 2>&1")
    print()

    # 2. 启动 WebSocket 采集进程
    print("[2/4] 启动 WebSocket 采集进程...")
    start_websocket()
    print()

    # 3. 启动飞书机器人
    print("[3/4] 启动飞书机器人...")
    start_feishu_bot()
    print()

    # 4. 验证
    print("[4/4] 验证运行状态...")
    import time
    time.sleep(2)
    result = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=30)
    print("当前 cron 配置:")
    for line in result.stdout.split("\n"):
        if "volume-ratio" in line:
            print(f"  {line}")

    print()
    print("=== 启动完成 ===")


if __name__ == "__main__":
    main()
