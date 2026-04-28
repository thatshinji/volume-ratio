#!/usr/bin/env python3
"""
一键关停跨市场量比监控服务
杀掉所有 collect_ws 进程 + 移除 3 个 cron 任务
"""

import os
import subprocess
import signal
from pathlib import Path

ROOT = Path(__file__).parent.parent
LOG_DIR = ROOT / "logs"

def remove_cron(keyword: str):
    """移除包含关键字的 cron 任务"""
    result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    if not result.stdout:
        return

    lines = result.stdout.split("\n")
    new_lines = [line for line in lines if keyword not in line]
    new_cron = "\n".join(new_lines)

    p = subprocess.run(["crontab", "-"], input=new_cron.encode(), capture_output=True)
    print(f"[stop] 已移除 cron: {keyword[:50]}...")


def kill_collect_ws():
    """杀掉所有 collect_ws 进程"""
    result = subprocess.run(["ps", "aux"], capture_output=True, text=True)
    killed = []
    for line in result.stdout.split("\n"):
        if "collect_ws.py" in line and "grep" not in line:
            parts = line.split()
            if len(parts) >= 2:
                pid = int(parts[1])
                try:
                    os.kill(pid, signal.SIGTERM)
                    killed.append(pid)
                except OSError:
                    pass

    if killed:
        print(f"[stop] 已杀掉 collect_ws 进程: {killed}")
    else:
        print("[stop] 没有 collect_ws 进程在运行")


def main():
    print("=== 一键关停跨市场量比监控服务 ===")
    print()

    # 1. 杀掉进程
    print("[1/2] 杀掉 collect_ws 进程...")
    kill_collect_ws()
    print()

    # 2. 移除 cron 任务
    print("[2/2] 移除 cron 任务...")
    remove_cron("collect_ws_launcher.py")
    remove_cron("volume-ratio/scripts/alert.py")
    print()

    # 3. 验证
    print("[验证] 当前 cron 配置:")
    result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    if result.stdout:
        for line in result.stdout.split("\n"):
            if line.strip():
                print(f"  {line}")
    else:
        print("  (空)")

    # 清理 pid 文件
    pid_file = LOG_DIR / "ws_collect.pid"
    if pid_file.exists():
        pid_file.unlink()
        print("[stop] 已删除 PID 文件")

    print()
    print("=== 关停完成 ===")
    print("所有监控服务已停止，cron 任务已清除")


if __name__ == "__main__":
    main()