#!/usr/bin/env python3
"""
飞书机器人守护进程 - 通过 cron 每分钟检查并确保机器人运行
Cron 调用方式: */1 * * * * python3 feishu_bot_launcher.py
"""

import os
import sys
import time
import fcntl
from pathlib import Path

ROOT = Path(__file__).parent.parent
LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)

PID_FILE = LOG_DIR / "feishu_bot.pid"
LOCK_FILE = LOG_DIR / "feishu_bot.lock"
SCRIPT = ROOT / "scripts" / "feishu_bot.py"
VENV_PYTHON = ROOT / ".venv" / "bin" / "python"


def is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def lock_is_held() -> bool:
    """判断机器人实例锁是否正被持有，避免 pid 文件陈旧时重复拉起。"""
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

    # 检查飞书配置是否存在
    import yaml
    config_path = ROOT / "config.yaml"
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
        feishu = config.get("feishu", {})
        if not feishu.get("app_id") or not feishu.get("app_secret"):
            return  # 未配置，跳过
    except (OSError, yaml.YAMLError):
        return

    with open(LOG_DIR / "launcher.log", "a") as log:
        log.write(f"[launcher] {time.strftime('%Y-%m-%d %H:%M:%S')} 启动飞书机器人\n")
        log.flush()

    pid = os.fork()
    if pid > 0:
        time.sleep(0.5)
        return

    # 子进程
    os.setsid()

    pid = os.fork()
    if pid > 0:
        sys.exit(0)

    # 孙子进程：写入自己的 PID
    PID_FILE.write_text(str(os.getpid()))

    # 重定向标准 IO
    devnull = os.open(os.devnull, os.O_RDONLY)
    os.dup2(devnull, sys.stdin.fileno())
    os.close(devnull)

    out_fd = os.open(LOG_DIR / "feishu_bot.log", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    os.dup2(out_fd, sys.stdout.fileno())
    os.close(out_fd)

    err_fd = os.open(LOG_DIR / "feishu_bot.err", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    os.dup2(err_fd, sys.stderr.fileno())
    os.close(err_fd)

    # 执行 feishu_bot.py。cron 可用系统 Python 调 launcher，但机器人进程必须用项目 venv。
    python_bin = str(VENV_PYTHON) if VENV_PYTHON.exists() else sys.executable
    os.execv(python_bin, [python_bin, str(SCRIPT)])


if __name__ == "__main__":
    check_and_launch()
