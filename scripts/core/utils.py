"""共享工具函数"""

import fcntl
import time
from pathlib import Path

import duckdb


def duckdb_connect(path, retries=3):
    """带重试的 DuckDB 连接，绕过 macOS 文件锁冲突。"""
    for attempt in range(retries):
        try:
            return duckdb.connect(str(path))
        except duckdb.IOException as e:
            if attempt < retries - 1 and "lock" in str(e).lower():
                time.sleep(0.1 * (attempt + 1))
                continue
            raise


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
