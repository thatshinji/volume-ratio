#!/usr/bin/env python3
"""
守护进程启动器 - 通过 cron 每分钟检查并确保 WebSocket 采集进程运行
Cron 调用方式: */1 * * * * python3 collect_ws_launcher.py
"""

import os
import sys
import time
import signal
import fcntl
from pathlib import Path

ROOT = Path(__file__).parent.parent
LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)

PID_FILE = LOG_DIR / "ws_collect.pid"
LOCK_FILE = LOG_DIR / "ws_collect.lock"
SCRIPT = ROOT / "scripts" / "collect_ws.py"
VENV_PYTHON = ROOT / ".venv" / "bin" / "python"


def is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def lock_is_held() -> bool:
    """判断采集进程实例锁是否正被持有，避免 pid 文件陈旧时重复拉起。"""
    if not LOCK_FILE.exists():
        return False
    try:
        with open(LOCK_FILE, "r+") as lock_file:
            try:
                fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(lock_file, fcntl.LOCK_UN)
                return False
            except BlockingIOError:
                pid_text = lock_file.read().strip()
                if pid_text:
                    PID_FILE.write_text(pid_text)
                return True
    except OSError:
        return False


def check_and_launch():
    if lock_is_held():
        return

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
        time.sleep(1)
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

    # 执行 collect_ws.py。cron 可用系统 Python 调 launcher，但采集进程必须用项目 venv。
    python_bin = str(VENV_PYTHON) if VENV_PYTHON.exists() else sys.executable
    os.execv(python_bin, [python_bin, str(SCRIPT)])


if __name__ == "__main__":
    check_and_launch()
