"""共享工具函数"""

import fcntl
from pathlib import Path


def locked_pid(lock_path: Path, pid_path: Path) -> str:
    """检查 lock 文件是否被持有，返回持有者 PID。"""
    if not lock_path.exists():
        return ""
    try:
        with open(lock_path, "r+") as lock_file:
            try:
                fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(lock_file, fcntl.LOCK_UN)
                return ""
            except BlockingIOError:
                pid_text = lock_file.read().strip()
                if pid_text:
                    pid_path.write_text(pid_text)
                return pid_text
    except OSError:
        return ""
