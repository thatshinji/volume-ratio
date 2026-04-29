#!/usr/bin/env python3
"""
一键关停跨市场量比监控服务
杀掉所有进程 + 移除 cron 任务
"""

import os
import subprocess
import signal
from pathlib import Path

ROOT = Path(__file__).parent.parent
LOG_DIR = ROOT / "logs"


def remove_cron(keyword: str):
    """移除包含关键字的 cron 任务"""
    result = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=30)
    if not result.stdout:
        return

    lines = result.stdout.split("\n")
    new_lines = [line for line in lines if keyword not in line]
    new_cron = "\n".join(new_lines)

    subprocess.run(["crontab", "-"], input=new_cron.encode(), capture_output=True, timeout=30)
    print(f"[stop] 已移除 cron: {keyword[:50]}...")


def kill_process_by_name(name: str):
    """杀掉指定名称的进程"""
    result = subprocess.run(["ps", "aux"], capture_output=True, text=True, timeout=30)
    killed = []
    for line in result.stdout.split("\n"):
        if name in line and "grep" not in line:
            parts = line.split()
            if len(parts) >= 2:
                pid = int(parts[1])
                try:
                    os.kill(pid, signal.SIGTERM)
                    killed.append(pid)
                except OSError:
                    pass

    if killed:
        print(f"[stop] 已杀掉 {name} 进程: {killed}")
    else:
        print(f"[stop] 没有 {name} 进程在运行")


def kill_pid_file(pid_file: Path):
    """通过 PID 文件杀进程"""
    if not pid_file.exists():
        return
    try:
        pid = int(pid_file.read_text().strip())
        os.kill(pid, signal.SIGTERM)
        print(f"[stop] 已杀掉进程 PID: {pid}")
    except (ValueError, OSError):
        pass
    pid_file.unlink(missing_ok=True)


def main():
    print("=== 一键关停跨市场量比监控服务 ===")
    print()

    # 1. 杀掉进程
    print("[1/3] 杀掉进程...")
    kill_process_by_name("collect_ws.py")
    kill_pid_file(LOG_DIR / "feishu_bot.pid")
    kill_process_by_name("feishu_bot.py")
    print()

    # 2. 移除 cron 任务
    print("[2/3] 移除 cron 任务...")
    remove_cron("collect_ws_launcher.py")
    remove_cron("feishu_bot_launcher.py")
    remove_cron("volume-ratio/scripts/alert.py")
    remove_cron("volume-ratio/scripts/cleanup.py")
    print()

    # 3. 验证
    print("[3/3] 验证状态...")
    result = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=30)
    if result.stdout:
        cron_lines = [l for l in result.stdout.split("\n") if l.strip()]
        if cron_lines:
            print("  剩余 cron 任务:")
            for line in cron_lines:
                print(f"    {line}")
        else:
            print("  cron 任务: 已清空")
    else:
        print("  cron 任务: 已清空")

    # 清理 pid 文件
    for pid_file in LOG_DIR.glob("*.pid"):
        pid_file.unlink(missing_ok=True)

    print()
    print("=== 关停完成 ===")


if __name__ == "__main__":
    main()
