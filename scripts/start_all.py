#!/usr/bin/env python3
"""
一键启动跨市场量比监控服务
启动 cron 任务 + WebSocket 采集进程 + 飞书机器人
"""

import fcntl
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
    subprocess.run(["crontab", "-"], input=new_cron.encode(), capture_output=True, timeout=30)
    print(f"[start] 已添加 cron: {line.strip()}")


def runtime_lock_is_held(lock_file: Path, pid_file: Path) -> bool:
    if not lock_file.exists():
        return False
    try:
        with open(lock_file, "r+") as lf:
            try:
                fcntl.flock(lf, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(lf, fcntl.LOCK_UN)
                return False
            except BlockingIOError:
                pid_text = lf.read().strip()
                if pid_text:
                    pid_file.write_text(pid_text)
                    print(f"[start] 已在运行，PID: {pid_text}")
                else:
                    print("[start] 已在运行")
                return True
    except OSError:
        return False


def start_websocket():
    """直接启动 WebSocket 采集进程"""
    pid_file = LOG_DIR / "ws_collect.pid"
    lock_file = LOG_DIR / "ws_collect.lock"

    if runtime_lock_is_held(lock_file, pid_file):
        return

    # 使用文件锁防止竞态条件
    with open(lock_file, "w") as lf:
        try:
            fcntl.flock(lf, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("[start] WebSocket 已在运行")
            return
        try:
            if pid_file.exists():
                try:
                    pid = int(pid_file.read_text().strip())
                    os.kill(pid, 0)
                    print(f"[start] WebSocket 已在运行，PID: {pid}")
                    return
                except (ValueError, OSError):
                    pass

            print("[start] 启动 WebSocket 采集进程...")
            python_bin = str(VENV_PYTHON) if VENV_PYTHON.exists() else sys.executable
            proc = subprocess.Popen(
                [python_bin, str(SCRIPTS_DIR / "collect_ws.py")],
                stdin=subprocess.DEVNULL,
                stdout=open(LOG_DIR / "ws_collect.log", "a"),
                stderr=open(LOG_DIR / "ws_collect.err", "a"),
                start_new_session=True,
            )
            pid_file.write_text(str(proc.pid))
            print(f"[start] WebSocket 启动，PID: {proc.pid}")
            return
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)


def start_feishu_bot():
    """启动飞书机器人进程"""
    pid_file = LOG_DIR / "feishu_bot.pid"
    lock_file = LOG_DIR / "feishu_bot.lock"

    if runtime_lock_is_held(lock_file, pid_file):
        return

    # 使用文件锁防止竞态条件
    with open(lock_file, "w") as lf:
        try:
            fcntl.flock(lf, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("[start] 飞书机器人已在运行")
            return
        try:
            if pid_file.exists():
                try:
                    pid = int(pid_file.read_text().strip())
                    os.kill(pid, 0)
                    print(f"[start] 飞书机器人已在运行，PID: {pid}")
                    return
                except (ValueError, OSError):
                    pass

            # 检查飞书配置
            import yaml
            config_path = ROOT / "config.yaml"
            with open(config_path, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f)
            feishu = config.get("feishu", {})
            if not feishu.get("app_id") or not feishu.get("app_secret"):
                print("[start] 飞书 app_id/app_secret 未配置，跳过机器人启动")
                return

            print("[start] 启动飞书机器人...")
            python_bin = str(VENV_PYTHON) if VENV_PYTHON.exists() else sys.executable

            proc = subprocess.Popen(
                [python_bin, str(SCRIPTS_DIR / "feishu_bot.py")],
                stdin=subprocess.DEVNULL,
                stdout=open(LOG_DIR / "feishu_bot.log", "a"),
                stderr=open(LOG_DIR / "feishu_bot.err", "a"),
                start_new_session=True,
            )
            pid_file.write_text(str(proc.pid))
            print(f"[start] 飞书机器人启动，PID: {proc.pid}")
            return
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)


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
    add_cron(f"*/1 * * * {python_bin} {bot_launcher_script} >> {LOG_DIR}/launcher.log 2>&1")
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
