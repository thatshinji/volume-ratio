#!/usr/bin/env python3
"""
FastAPI HTTP API server for Volume Ratio Desktop client.
Exposes the same functionality as the CLI and Feishu bot via REST + WebSocket.
"""

import argparse
import asyncio
import json
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from core.config import load_config, save_config, parse_ticker, remove_ticker_from_config
from core.market import get_market, get_all_tickers, get_all_tickers_with_names, is_market_trading, market_now

app = FastAPI(title="Volume Ratio API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- WebSocket connection manager ---

class ConnectionManager:
    def __init__(self):
        self.alert_connections: list[WebSocket] = []
        self.quote_connections: list[WebSocket] = []

    async def connect_alerts(self, ws: WebSocket):
        await ws.accept()
        self.alert_connections.append(ws)

    async def connect_quotes(self, ws: WebSocket):
        await ws.accept()
        self.quote_connections.append(ws)

    def disconnect_alerts(self, ws: WebSocket):
        if ws in self.alert_connections:
            self.alert_connections.remove(ws)

    def disconnect_quotes(self, ws: WebSocket):
        if ws in self.quote_connections:
            self.quote_connections.remove(ws)

    async def broadcast_alert(self, data: dict):
        dead = []
        for ws in self.alert_connections:
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect_alerts(ws)

    async def broadcast_quote(self, data: dict):
        dead = []
        for ws in self.quote_connections:
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect_quotes(ws)

manager = ConnectionManager()

# --- Request models ---

class AddTickerRequest(BaseModel):
    raw: str

class MuteRequest(BaseModel):
    ticker: str
    duration: str

class AnalyzeRequest(BaseModel):
    ticker: str

class ConfigUpdateRequest(BaseModel):
    params: Optional[dict] = None

class LLMConfigUpdate(BaseModel):
    provider: Optional[str] = None
    model: Optional[str] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    max_tokens: Optional[int] = None
    temperature: Optional[float] = None

class LLMSwitchRequest(BaseModel):
    profile: str

class LongbridgeConfigUpdate(BaseModel):
    app_key: Optional[str] = None
    app_secret: Optional[str] = None
    access_token: Optional[str] = None

class FeishuConfigUpdate(BaseModel):
    app_id: Optional[str] = None
    app_secret: Optional[str] = None
    chat_id: Optional[str] = None
    webhook_url: Optional[str] = None

# --- Helper ---

def get_db_path() -> Path:
    return ROOT / "data" / "ratios.db"


def query_signals_today() -> list[dict]:
    db = get_db_path()
    if not db.exists():
        return []
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        with sqlite3.connect(str(db), timeout=10) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM signals WHERE timestamp >= ? ORDER BY timestamp DESC",
                (today,),
            ).fetchall()
            return [dict(r) for r in rows]
    except sqlite3.Error:
        return []


def query_signal_history(ticker: str, days: int = 7) -> list[dict]:
    db = get_db_path()
    if not db.exists():
        return []
    since = (datetime.now() - timedelta(days=days)).isoformat()
    try:
        with sqlite3.connect(str(db), timeout=10) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM signals WHERE ticker = ? AND timestamp >= ? ORDER BY timestamp DESC",
                (ticker, since),
            ).fetchall()
            return [dict(r) for r in rows]
    except sqlite3.Error:
        return []


def query_volume_ratios(ticker: str, days: int = 7) -> list[dict]:
    db = get_db_path()
    if not db.exists():
        return []
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    try:
        with sqlite3.connect(str(db), timeout=10) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM volume_ratios WHERE ticker = ? AND market_date >= ? ORDER BY timestamp DESC",
                (ticker, since),
            ).fetchall()
            return [dict(r) for r in rows]
    except sqlite3.Error:
        return []

# --- Routes ---

@app.get("/api/health")
async def health():
    return {"status": "ok", "timestamp": datetime.now().isoformat()}


@app.post("/api/scan")
async def scan():
    from compute import compute_all
    results = compute_all()
    return results


@app.get("/api/scan/{ticker}")
async def scan_ticker(ticker: str):
    from compute import compute_ticker
    result = compute_ticker(ticker)
    if result is None:
        return {"error": f"No data for {ticker}"}
    return result


@app.get("/api/signals")
async def signals():
    return query_signals_today()


@app.get("/api/signals/history/{ticker}")
async def signal_history(ticker: str):
    return query_signal_history(ticker)


@app.get("/api/signals/ratios/{ticker}")
async def ratio_history(ticker: str):
    return query_volume_ratios(ticker)


@app.get("/api/status")
async def status():
    from compute import init_db, get_db_path as compute_db_path
    config = load_config()

    # WebSocket status
    ws_lock = ROOT / "logs" / "ws_collect.lock"
    ws_running = False
    ws_pid = None
    if ws_lock.exists():
        try:
            pid_text = ws_lock.read_text().strip()
            if pid_text.isdigit():
                import os
                os.kill(int(pid_text), 0)
                ws_running = True
                ws_pid = int(pid_text)
        except (OSError, ValueError):
            pass

    # Database
    db_path = compute_db_path()
    db_records = 0
    db_size = 0
    if db_path.exists():
        db_size = db_path.stat().st_size
        try:
            with sqlite3.connect(str(db_path), timeout=5) as conn:
                row = conn.execute("SELECT COUNT(*) FROM volume_ratios").fetchone()
                db_records = row[0] if row else 0
        except sqlite3.Error:
            pass

    # Snapshots
    snap_dir = ROOT / "data" / "snapshots"
    snap_files = 0
    snap_size = 0
    if snap_dir.exists():
        for f in snap_dir.rglob("*.jsonl"):
            snap_files += 1
            snap_size += f.stat().st_size

    # LLM calls today
    llm_today = 0
    if db_path.exists():
        today = datetime.now().strftime("%Y-%m-%d")
        try:
            with sqlite3.connect(str(db_path), timeout=5) as conn:
                row = conn.execute(
                    "SELECT COUNT(*) FROM llm_calls WHERE timestamp >= ?", (today,)
                ).fetchone()
                llm_today = row[0] if row else 0
        except sqlite3.Error:
            pass

    # Markets
    tickers = get_all_tickers(config)
    active_markets = set()
    for t in tickers:
        active_markets.add(get_market(t))

    markets = []
    for m in sorted(active_markets):
        markets.append({
            "market": m,
            "is_trading": is_market_trading(m),
        })

    # Params
    params = config.get("params", {})

    return {
        "websocket": {"running": ws_running, "pid": ws_pid},
        "database": {
            "records": db_records,
            "size_bytes": db_size,
            "max_bytes": 1 * 1024 * 1024 * 1024,
        },
        "snapshots": {
            "files": snap_files,
            "size_bytes": snap_size,
            "max_bytes": 3 * 1024 * 1024 * 1024,
        },
        "llm_calls_today": llm_today,
        "markets": markets,
        "params": params,
    }


@app.get("/api/watchlist")
async def get_watchlist():
    config = load_config()
    wl = config.get("watchlist", {})
    return {
        "us": wl.get("us", []),
        "hk": wl.get("hk", []),
        "cn": wl.get("cn", []),
    }


@app.post("/api/watchlist")
async def add_ticker(req: AddTickerRequest):
    from cli import cmd_add_ticker
    import io, contextlib

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        cmd_add_ticker(req.raw)
    output = buf.getvalue().strip()
    return {"ok": True, "message": output}


@app.delete("/api/watchlist/{ticker}")
async def remove_ticker(ticker: str):
    config = load_config()
    remove_ticker_from_config(config, ticker)
    return {"ok": True, "message": f"Removed {ticker}"}


@app.post("/api/watchlist/sync")
async def sync_watchlist():
    from longbridge_sync import run_sync
    import io, contextlib

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        result = run_sync(restart_ws=False)
    result["log"] = buf.getvalue().strip()
    return result


@app.post("/api/mute")
async def mute_ticker(req: MuteRequest):
    from cli import cmd_mute
    import io, contextlib

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        cmd_mute(req.ticker, req.duration)
    output = buf.getvalue().strip()
    return {"ok": True, "message": output}


@app.post("/api/brief")
async def brief():
    from compute import compute_all
    from llm import call_llm

    config = load_config()
    results = compute_all()

    # Build text summary
    lines = []
    for r in sorted(results, key=lambda x: x.get("ratio", 0), reverse=True):
        lines.append(
            f"{r['ticker']} {r.get('name', '')} "
            f"price={r.get('price', 0)} change={r.get('change_pct', 0)}% "
            f"ratio={r.get('ratio', 0)} intraday={r.get('ratio_intraday', 0)}"
        )
    brief_text = "\n".join(lines)

    # Call LLM
    llm_analysis = None
    try:
        prompt = f"""以下是持仓组合的实时量比数据：
{brief_text}

请用中文简要分析：1)整体市场情绪 2)有无异常信号 3)风险提示。控制在150字以内。"""
        llm_analysis = call_llm(prompt)
    except Exception as e:
        llm_analysis = f"LLM analysis failed: {e}"

    return {"brief": brief_text, "llm_analysis": llm_analysis}


@app.post("/api/analyze")
async def analyze(req: AnalyzeRequest):
    from compute import compute_ticker
    from llm import call_llm

    result = compute_ticker(req.ticker)
    if not result:
        return {"error": f"No data for {req.ticker}"}

    try:
        prompt = f"""分析以下标的的量比异动：
标的：{result['ticker']}-{result.get('name', '')}
价格：{result.get('price', 0)} 涨跌：{result.get('change_pct', 0)}%
5日历史量比：{result.get('ratio', 0)} ({result.get('signal', '')})
日内滚动量比：{result.get('ratio_intraday', 0)}
5日平均成交量：{result.get('volume_avg5', 0)}

请用中文分析：1)量比异动原因 2)买卖信号评估 3)风险提示。控制在100字以内。"""
        analysis = call_llm(prompt)
    except Exception as e:
        analysis = f"LLM analysis failed: {e}"

    return {"analysis": analysis}


@app.get("/api/config")
async def get_config():
    config = load_config()
    # Redact secrets
    safe = dict(config)
    if "llm" in safe:
        safe["llm"] = {**safe["llm"]}
        if "api_key" in safe["llm"]:
            key = safe["llm"]["api_key"]
            safe["llm"]["api_key"] = key[:6] + "***" + key[-4:] if len(key) > 10 else "***"
    for profile in safe.get("llm_profiles", {}).values():
        if "api_key" in profile:
            key = profile["api_key"]
            profile["api_key"] = key[:6] + "***" + key[-4:] if len(key) > 10 else "***"
    if "feishu" in safe:
        safe["feishu"] = {**safe["feishu"]}
        for k in ("app_secret",):
            if k in safe["feishu"]:
                safe["feishu"][k] = "***"
    return safe


@app.put("/api/config/params")
async def update_params(req: ConfigUpdateRequest):
    config = load_config()
    if req.params:
        config.setdefault("params", {}).update(req.params)
        save_config(config)
    return {"ok": True, "params": config.get("params", {})}


@app.put("/api/config/llm")
async def update_llm(req: LLMConfigUpdate):
    config = load_config()
    llm = config.setdefault("llm", {})
    for field in ("provider", "model", "base_url", "api_key", "max_tokens", "temperature"):
        val = getattr(req, field)
        if val is not None:
            llm[field] = val
    save_config(config)
    return {"ok": True}


@app.put("/api/config/llm/switch")
async def switch_llm(req: LLMSwitchRequest):
    config = load_config()
    profiles = config.get("llm_profiles", {})
    if req.profile not in profiles:
        return {"error": f"Profile '{req.profile}' not found"}
    profile = dict(profiles[req.profile])
    # Copy profile values into active llm config
    config.setdefault("llm", {}).update(profile)
    save_config(config)
    return {"ok": True, "profile": req.profile, "llm": {k: v for k, v in profile.items() if k != "api_key"}}


@app.put("/api/config/longbridge")
async def update_longbridge(req: LongbridgeConfigUpdate):
    config = load_config()
    lb = config.setdefault("longbridge", {})
    for field in ("app_key", "app_secret", "access_token"):
        val = getattr(req, field)
        if val is not None:
            lb[field] = val
    save_config(config)
    return {"ok": True}


@app.put("/api/config/feishu")
async def update_feishu(req: FeishuConfigUpdate):
    config = load_config()
    if config.get("feishu") is None:
        config["feishu"] = {}
    feishu = config["feishu"]
    for field in ("app_id", "app_secret", "chat_id", "webhook_url"):
        val = getattr(req, field)
        if val is not None:
            if val == "":
                feishu.pop(field, None)
            else:
                feishu[field] = val
    save_config(config)
    return {"ok": True}


@app.get("/api/config/llm/profiles")
async def get_llm_profiles():
    config = load_config()
    profiles = config.get("llm_profiles", {})
    active = config.get("llm", {})
    active_provider = active.get("provider", "")
    result = []
    for name, profile in profiles.items():
        result.append({
            "name": name,
            "provider": profile.get("provider", ""),
            "model": profile.get("model", ""),
            "base_url": profile.get("base_url", ""),
            "active": profile.get("provider", "") == active_provider and profile.get("model", "") == active.get("model", ""),
        })
    return {"profiles": result, "active_provider": active_provider}


# --- WebSocket endpoints ---

@app.websocket("/ws/alerts")
async def ws_alerts(ws: WebSocket):
    await manager.connect_alerts(ws)
    try:
        while True:
            # Keep alive, read any incoming messages (ping/pong)
            await ws.receive_text()
    except WebSocketDisconnect:
        manager.disconnect_alerts(ws)


@app.websocket("/ws/quotes")
async def ws_quotes(ws: WebSocket):
    await manager.connect_quotes(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        manager.disconnect_quotes(ws)


# --- Notification queue reader (for alert.py to push via WebSocket) ---

NOTIFICATION_FILE = ROOT / "data" / "notifications.jsonl"


@app.get("/api/notifications/pending")
async def get_pending_notifications():
    if not NOTIFICATION_FILE.exists():
        return []
    notifications = []
    try:
        with open(NOTIFICATION_FILE, "r") as f:
            for line in f:
                line = line.strip()
                if line:
                    notifications.append(json.loads(line))
        # Clear after reading
        NOTIFICATION_FILE.write_text("")
    except (json.JSONDecodeError, OSError):
        pass
    return notifications


# --- Main ---

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Volume Ratio API Server")
    parser.add_argument("--port", type=int, default=9720)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()

    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
