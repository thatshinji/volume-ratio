#!/usr/bin/env python3
"""
一键启动飞书机器人
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
LOG_DIR = ROOT / "logs"
VENV_PYTHON = ROOT / ".venv" / "bin" / "python"
LAUNCHER = ROOT / "scripts" / "feishu_bot_launcher.py"


def main():
    LOG_DIR.mkdir(exist_ok=True)

    python_bin = str(VENV_PYTHON) if VENV_PYTHON.exists() else sys.executable
    subprocess.run([python_bin, str(LAUNCHER)], check=False)
    pid_file = LOG_DIR / "feishu_bot.pid"
    if pid_file.exists():
        print(f"[bot] 当前 PID: {pid_file.read_text().strip()}")
    else:
        print("[bot] 未启动，请检查 feishu 配置和日志")


if __name__ == "__main__":
    main()
