#!/usr/bin/env python3
"""
Local notification queue for desktop client alerts.
Replaces Feishu push as the primary notification channel.
Writes alert dicts to data/notifications.jsonl for the API server to serve via WebSocket.
"""

import json
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent.parent
NOTIFICATION_FILE = ROOT / "data" / "notifications.jsonl"


def push_notification(alert: dict):
    """Push an alert notification to the local queue."""
    NOTIFICATION_FILE.parent.mkdir(parents=True, exist_ok=True)
    notification = {
        "type": "alert",
        "timestamp": datetime.now().isoformat(),
        "ticker": alert.get("ticker", ""),
        "name": alert.get("name", ""),
        "signal": alert.get("signal", ""),
        "signal_detail": alert.get("signal_detail", ""),
        "ratio": alert.get("ratio", 0),
        "historical_ratio": alert.get("historical_ratio", 0),
        "intraday_ratio": alert.get("intraday_ratio", 0),
        "price": alert.get("price", 0),
        "change_pct": alert.get("change_pct", 0),
        "source": alert.get("source", ""),
        "analysis": alert.get("_analysis", None),
    }
    try:
        with open(NOTIFICATION_FILE, "a") as f:
            f.write(json.dumps(notification, ensure_ascii=False) + "\n")
            f.flush()
    except OSError as e:
        print(f"[notification_queue] Failed to write: {e}")


def push_brief(brief_text: str, llm_analysis: str = None):
    """Push a brief report notification."""
    NOTIFICATION_FILE.parent.mkdir(parents=True, exist_ok=True)
    notification = {
        "type": "brief",
        "timestamp": datetime.now().isoformat(),
        "brief": brief_text,
        "llm_analysis": llm_analysis,
    }
    try:
        with open(NOTIFICATION_FILE, "a") as f:
            f.write(json.dumps(notification, ensure_ascii=False) + "\n")
            f.flush()
    except OSError as e:
        print(f"[notification_queue] Failed to write: {e}")


def get_pending() -> list[dict]:
    """Read and clear pending notifications."""
    if not NOTIFICATION_FILE.exists():
        return []
    notifications = []
    try:
        with open(NOTIFICATION_FILE, "r") as f:
            for line in f:
                line = line.strip()
                if line:
                    notifications.append(json.loads(line))
        NOTIFICATION_FILE.write_text("")
    except (json.JSONDecodeError, OSError):
        pass
    return notifications
