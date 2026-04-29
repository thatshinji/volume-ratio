#!/usr/bin/env python3
"""
一键停止飞书机器人
"""

import os
import signal
import subprocess
from pathlib import Path

ROOT = Path(__file__).parent.parent
PID_FILE = ROOT / "logs" / "feishu_bot.pid"


def is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def main():
    killed = False

    # 方式一：通过 PID 文件
    if PID_FILE.exists():
        try:
            pid = int(PID_FILE.read_text().strip())
            if is_running(pid):
                os.kill(pid, signal.SIGTERM)
                print(f"[bot] 已停止，PID: {pid}")
                killed = True
            else:
                print(f"[bot] PID {pid} 已不存在")
        except ValueError:
            print("[bot] PID 文件内容无效")
        PID_FILE.unlink()
    else:
        print("[bot] PID 文件不存在")

    # 方式二：兜底，按进程名查找
    result = subprocess.run(["ps", "aux"], capture_output=True, text=True, timeout=10)
    for line in result.stdout.split("\n"):
        if "feishu_bot.py" in line and "grep" not in line:
            parts = line.split()
            if len(parts) >= 2:
                pid = int(parts[1])
                try:
                    os.kill(pid, signal.SIGTERM)
                    print(f"[bot] 已停止残留进程，PID: {pid}")
                    killed = True
                except OSError:
                    pass

    if not killed:
        print("[bot] 没有运行中的飞书机器人进程")


if __name__ == "__main__":
    main()
