#!/usr/bin/env python3
"""
守护进程启动器 - 通过 cron 每分钟检查并确保 WebSocket 采集进程运行
Cron 调用方式: */1 * * * * python3 collect_ws_launcher.py
"""

import os
import sys
import time
import signal
from pathlib import Path

ROOT = Path(__file__).parent.parent
LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)

PID_FILE = LOG_DIR / "ws_collect.pid"
SCRIPT = ROOT / "scripts" / "collect_ws.py"


def is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def check_and_launch():
    pid = None
    if PID_FILE.exists():
        try:
            pid = int(PID_FILE.read_text().strip())
        except ValueError:
            pid = None

    # 检查是否已运行
    if pid and is_running(pid):
        return

    log_path = LOG_DIR / "ws_collect.log"
    err_path = LOG_DIR / "ws_collect.err"

    with open(LOG_DIR / "launcher.log", "a") as log:
        log.write(f"[launcher] {time.strftime('%Y-%m-%d %H:%M:%S')} 启动 WebSocket 采集进程\n")
        log.flush()

    pid = os.fork()
    if pid > 0:
        # 等待子进程完成 fork，确保 PID 文件写入
        time.sleep(0.5)
        return

    # 子进程：创建新 session 成为后台进程
    os.setsid()

    pid = os.fork()
    if pid > 0:
        sys.exit(0)

    # 孙子进程：写入自己的 PID（而非中间进程的 PID）
    PID_FILE.write_text(str(os.getpid()))

    # 重定向标准 IO
    devnull = os.open(os.devnull, os.O_RDONLY)
    os.dup2(devnull, sys.stdin.fileno())
    os.close(devnull)

    out_fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    os.dup2(out_fd, sys.stdout.fileno())
    os.close(out_fd)

    err_fd = os.open(err_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    os.dup2(err_fd, sys.stderr.fileno())
    os.close(err_fd)

    # 执行 collect_ws.py
    os.execv(sys.executable, [sys.executable, str(SCRIPT)])


if __name__ == "__main__":
    check_and_launch()
