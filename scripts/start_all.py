#!/usr/bin/env python3
"""
一键启动跨市场量比监控服务
启动 3 个 cron 任务 + 直接启动 WebSocket 采集进程
"""

import os
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
    p = subprocess.run(["crontab", "-"], input=new_cron.encode(), capture_output=True, timeout=30)
    print(f"[start] 已添加 cron: {line.strip()}")


def start_websocket():
    """直接启动 WebSocket 采集进程"""
    pid_file = LOG_DIR / "ws_collect.pid"
    # 检查是否已在运行
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            os.kill(pid, 0)
            print(f"[start] WebSocket 已在运行，PID: {pid}")
            return
        except (ValueError, OSError):
            pass

    print("[start] 启动 WebSocket 采集进程...")
    pid = os.fork()
    if pid > 0:
        pid_file.write_text(str(pid))
        print(f"[start] WebSocket 启动，PID: {pid}")
        return

    os.setsid()
    pid = os.fork()
    if pid > 0:
        os._exit(0)

    devnull = os.open(os.devnull, os.O_RDONLY)
    os.dup2(devnull, 0)
    os.close(devnull)

    out_fd = os.open(LOG_DIR / "ws_collect.log", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    os.dup2(out_fd, 1)
    os.close(out_fd)

    err_fd = os.open(LOG_DIR / "ws_collect.err", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    os.dup2(err_fd, 2)
    os.close(err_fd)

    os.execv(sys.executable, [sys.executable, str(SCRIPTS_DIR / "collect_ws.py")])


def main():
    print("=== 一键启动跨市场量比监控服务 ===")
    print()

    # 动态构建 cron 命令（使用 ROOT 和 sys.executable）
    launcher_script = SCRIPTS_DIR / "collect_ws_launcher.py"
    alert_script = SCRIPTS_DIR / "alert.py"
    python_bin = str(VENV_PYTHON) if VENV_PYTHON.exists() else sys.executable
    launcher_python = "/usr/bin/python3"  # launcher 必须用系统 Python（longbridge 未装在 venv）

    # 1. 添加 cron 任务
    print("[1/3] 配置 cron 任务...")
    add_cron(f"*/1 * * * 1-5 {launcher_python} {launcher_script} >> {LOG_DIR}/launcher.log 2>&1")
    add_cron(f"*/1 * * * 1-5 {python_bin} {alert_script} >> {LOG_DIR}/alert.log 2>&1")
    add_cron(f"*/30 * * * 1-5 {python_bin} {alert_script} --brief >> {LOG_DIR}/brief.log 2>&1")
    print()

    # 2. 启动 WebSocket 采集进程
    print("[2/3] 启动 WebSocket 采集进程...")
    start_websocket()
    print()

    # 3. 验证
    print("[3/3] 验证运行状态...")
    import time
    time.sleep(2)
    result = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=30)
    print("当前 cron 配置:")
    for line in result.stdout.split("\n"):
        if "volume-ratio" in line:
            print(f"  {line}")

    print()
    print("=== 启动完成 ===")
    print("WebSocket 采集进程会在下一分钟被 cron 守护进程自动启动")


if __name__ == "__main__":
    main()
