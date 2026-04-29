#!/usr/bin/env python3
"""
一键启动飞书机器人
"""

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
LOG_DIR = ROOT / "logs"
PID_FILE = LOG_DIR / "feishu_bot.pid"
SCRIPT = ROOT / "scripts" / "feishu_bot.py"


def is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def main():
    LOG_DIR.mkdir(exist_ok=True)

    # 检查是否已在运行
    if PID_FILE.exists():
        try:
            pid = int(PID_FILE.read_text().strip())
            if is_running(pid):
                print(f"[bot] 已在运行，PID: {pid}")
                return
        except ValueError:
            pass

    # 检查飞书配置
    import yaml
    config_path = ROOT / "config.yaml"
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    feishu = config.get("feishu", {})
    if not feishu.get("app_id") or not feishu.get("app_secret"):
        print("[bot] 错误: config.yaml 中 feishu.app_id / app_secret 未配置")
        sys.exit(1)

    print("[bot] 启动飞书机器人...")

    # 双 fork 守护进程
    pid = os.fork()
    if pid > 0:
        time.sleep(1)
        if PID_FILE.exists():
            real_pid = PID_FILE.read_text().strip()
            print(f"[bot] 启动成功，PID: {real_pid}")
        else:
            print(f"[bot] 启动成功，PID: {pid}")
        return

    os.setsid()
    pid = os.fork()
    if pid > 0:
        sys.exit(0)

    # 孙子进程
    PID_FILE.write_text(str(os.getpid()))

    devnull = os.open(os.devnull, os.O_RDONLY)
    os.dup2(devnull, 0)
    os.close(devnull)

    out_fd = os.open(LOG_DIR / "feishu_bot.log", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    os.dup2(out_fd, 1)
    os.close(out_fd)

    err_fd = os.open(LOG_DIR / "feishu_bot.err", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    os.dup2(err_fd, 2)
    os.close(err_fd)

    os.execv(sys.executable, [sys.executable, str(SCRIPT)])


if __name__ == "__main__":
    main()
