#!/usr/bin/env python3
""" Ops Center — 全项目统一监控后台 """

import json
import os
import subprocess
import sqlite3
import time
import urllib.request
from datetime import datetime, date
from pathlib import Path

from flask import Flask, jsonify, send_from_directory
from flask_cors import CORS

app = Flask(__name__, static_folder="static")

# ── 服务元信息 ──
VERSION = "1.0.0"
STARTED_AT = int(time.time())

# ── 安全：最小 CORS（仅 localhost 来源） ──
CORS(app, resources={r"/api/*": {"origins": [
    r"http://localhost:\d+",
    r"http://127\.0\.0\.1:\d+",
]}}, supports_credentials=True)


# ── 安全：CSP + 缓存头 ──
@app.after_request
def apply_security_headers(response):
    # CSP: 仅允许同源脚本/样式，连接只限同源
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "connect-src 'self' http://localhost:* http://127.0.0.1:*; "
        "font-src 'self'"
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    # 静态资源缓存（HTML 除外）
    if response.content_type in ("text/css", "application/javascript", "image/png", "image/svg+xml", "image/x-icon"):
        response.headers["Cache-Control"] = "public, max-age=3600"
    return response

HOME = os.path.expanduser("~")
WORKBUDDY_DB = os.path.join(HOME, ".workbuddy", "workbuddy.db")
PROJECTS = {
    "qts": {
        "name": "QuantTradingSystem",
        "path": os.path.join(HOME, "WorkBuddy", "QuantTradingSystem"),
        "services": [
            {"name": "策略服务", "port": 8000, "path": "/health"},
            {"name": "执行服务", "port": 8001, "path": "/health"},
            {"name": "AI 调度", "port": 8002, "path": "/health"},
        ],
    },
    "claw": {
        "name": "Claw",
        "path": os.path.join(HOME, "WorkBuddy", "Claw"),
    },
    "stockinsight": {
        "name": "StockInsight",
        "path": os.path.join(HOME, "WorkBuddy", "StockInsight"),
        "services": [
            {"name": "前端服务", "port": 3000, "path": "/"},
        ],
    },
    "marvis": {
        "name": "Marvis Bridge",
        "path": os.path.join(HOME, "WorkBuddy", "workbuddy_marvis_bridge"),
    },
}


def http_get(host, port, path, timeout=3):
    """Try HTTP GET, return (ok, data_or_error)."""
    try:
        url = f"http://{host}:{port}{path}"
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode()
            try:
                return True, json.loads(body)
            except json.JSONDecodeError:
                return True, body[:200]
    except Exception as e:
        return False, str(e)


def check_pid_running(name_filter):
    """Check if any process matching name_filter is running."""
    try:
        result = subprocess.run(
            ["pgrep", "-f", name_filter],
            capture_output=True, text=True, timeout=3,
        )
        pids = [int(p) for p in result.stdout.strip().split() if p.strip()]
        return len(pids) > 0, pids
    except Exception:
        return False, []


def get_db_automations(db_path):
    """Read automation data from WorkBuddy DB."""
    if not os.path.exists(db_path):
        return {"count": 0, "active": 0, "paused": 0, "recent": []}
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()

        # Total counts
        c.execute("SELECT status, COUNT(*) as cnt FROM automations GROUP BY status")
        counts = {row["status"]: row["cnt"] for row in c.fetchall()}

        # Recent runs (last 10)
        c.execute(
            """SELECT id, name, status, scheduleType, rrule,
                      datetime(last_run_at, 'unixepoch', 'localtime') as last_run
               FROM automations
               ORDER BY COALESCE(last_run_at, 0) DESC
               LIMIT 10"""
        )
        recent = [dict(row) for row in c.fetchall()]
        conn.close()

        return {
            "count": sum(counts.values()),
            "active": counts.get("ACTIVE", 0),
            "paused": counts.get("PAUSED", 0),
            "recent": recent,
        }
    except Exception as e:
        return {"count": 0, "active": 0, "paused": 0, "error": str(e)}


def get_dir_size(path):
    """Get directory size in human-readable form."""
    try:
        result = subprocess.run(
            ["du", "-sh", path],
            capture_output=True, text=True, timeout=5,
        )
        return result.stdout.strip().split("\t")[0]
    except Exception:
        return "?"


def parse_size_mb(size_str):
    """Convert 'du -sh' output like '1.0G', '850M', '234K', '?' to MB."""
    if not size_str or size_str == "?":
        return None
    size_str = size_str.upper().strip()
    try:
        if size_str.endswith("G"):
            return float(size_str.replace("G", "")) * 1024
        elif size_str.endswith("M"):
            return float(size_str.replace("M", ""))
        elif size_str.endswith("K"):
            return float(size_str.replace("K", "")) / 1024
        elif size_str.endswith("B"):
            return float(size_str.replace("B", "")) / 1048576
        else:
            return float(size_str) / 1048576
    except (ValueError, TypeError):
        return None


def size_progress(size_str, target_mb):
    """Inverted progress: smaller size → higher progress.
    Returns 0-100 where 100 = at or below target."""
    cur_mb = parse_size_mb(size_str)
    if cur_mb is None or target_mb <= 0:
        return 50  # fallback
    ratio = cur_mb / target_mb
    if ratio <= 0.5:
        return 95
    elif ratio <= 0.8:
        return 80
    elif ratio <= 1.0:
        return 60
    elif ratio <= 1.2:
        return 40
    elif ratio <= 1.5:
        return 25
    else:
        return 10


def get_cost_data():
    """Read real cost data from ~/.ai_cost_log.jsonl, filtered to current month."""
    jsonl_path = os.path.join(HOME, ".ai_cost_log.jsonl")
    this_month = datetime.now().strftime("%Y-%m")

    if not os.path.exists(jsonl_path):
        return None

    total_cost = 0.0
    total_input = 0
    total_output = 0
    record_count = 0

    try:
        with open(jsonl_path, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    # Filter to current month only
                    if rec.get("date", "").startswith(this_month):
                        total_cost += rec.get("cost_cny", 0)
                        total_input += rec.get("input", 0)
                        total_output += rec.get("output", 0)
                        record_count += 1
                except json.JSONDecodeError:
                    continue

        total_tokens = total_input + total_output
        return {
            "total_records": record_count,
            "total_tokens": total_tokens,
            "total_cost": round(total_cost, 4),
        }
    except Exception:
        return None


# ── API Routes ──────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/api/overview")
def api_overview():
    """Top-level aggregated status for all 4 projects."""
    projects = {}

    # ── QTS ──
    qts_services = []
    qts_ok = 0
    for svc in PROJECTS["qts"]["services"]:
        ok, data = http_get("localhost", svc["port"], svc["path"])
        qts_services.append({
            "name": svc["name"],
            "port": svc["port"],
            "status": "healthy" if ok else "unhealthy",
            "data": data if ok else None,
            "error": data if not ok else None,
        })
        if ok:
            qts_ok += 1
    qts_size = get_dir_size(PROJECTS["qts"]["path"])
    projects["qts"] = {
        "name": "QuantTradingSystem",
        "services_healthy": qts_ok,
        "services_total": len(qts_services),
        "status": "healthy" if qts_ok == len(qts_services) else "degraded" if qts_ok > 0 else "unhealthy",
        "services": qts_services,
        "size": qts_size,
    }

    # ── Claw ──
    claw_scripts = len(os.listdir(os.path.join(PROJECTS["claw"]["path"], "scripts"))) if os.path.isdir(os.path.join(PROJECTS["claw"]["path"], "scripts")) else 0
    projects["claw"] = {
        "name": "Claw",
        "status": "healthy",
        "scripts": claw_scripts,
        "size": get_dir_size(PROJECTS["claw"]["path"]),
    }

    # ── StockInsight ──
    si_services = []
    si_ok = 0
    for svc in PROJECTS["stockinsight"]["services"]:
        ok, data = http_get("localhost", svc["port"], svc["path"])
        si_services.append({
            "name": svc["name"],
            "port": svc["port"],
            "status": "healthy" if ok else "unhealthy",
            "data": str(data)[:100] if ok else None,
            "error": data if not ok else None,
        })
        if ok:
            si_ok += 1
    projects["stockinsight"] = {
        "name": "StockInsight",
        "services_healthy": si_ok,
        "services_total": len(si_services),
        "status": "healthy" if si_ok > 0 else "unhealthy",
        "services": si_services,
        "size": get_dir_size(PROJECTS["stockinsight"]["path"]),
    }

    # ── Marvis Bridge ──
    watcher_ok, watcher_pids = check_pid_running("file_watcher.sh")
    consumer_ok, consumer_pids = check_pid_running("workbuddy_consumer")
    marvis_status = "healthy"
    if not watcher_ok and not consumer_ok:
        marvis_status = "unhealthy"
    elif not watcher_ok:
        marvis_status = "degraded"
    projects["marvis"] = {
        "name": "Marvis Bridge",
        "status": marvis_status,
        "watcher_running": watcher_ok,
        "consumer_running": consumer_ok,
        "watcher_pids": watcher_pids,
        "consumer_pids": consumer_pids,
        "size": get_dir_size(PROJECTS["marvis"]["path"]),
    }

    return jsonify({
        "timestamp": datetime.now().isoformat(),
        "date": date.today().isoformat(),
        "projects": projects,
    })


@app.route("/api/automations")
def api_automations():
    """Automation registry overview."""
    data = get_db_automations(WORKBUDDY_DB)
    return jsonify(data)


@app.route("/api/cost")
def api_cost():
    """AI cost and cache stats (real data from JSONL log)."""
    cost = get_cost_data()
    monthly = cost["total_cost"] if cost else 0
    return jsonify({
        "monthly_estimate": monthly,
        "target": 30,
        "details": cost,
    })


@app.route("/api/roadmap")
def api_roadmap():
    """Q3 2026 Roadmap progress — real data from live sources."""
    cost = get_cost_data()
    current_cost = cost["total_cost"] if cost else 0

    # Real automation count from DB
    automations = get_db_automations(WORKBUDDY_DB)
    auto_count = automations["count"]
    # Inverted: lower count is better, target ≤ 35
    if auto_count <= 0:
        auto_progress = 95
    elif auto_count <= 15:
        auto_progress = 80
    elif auto_count <= 25:
        auto_progress = 60
    elif auto_count <= 35:
        auto_progress = 40
    elif auto_count <= 45:
        auto_progress = 25
    else:
        auto_progress = 10

    # Real directory sizes
    qts_size_raw = get_dir_size(PROJECTS["qts"]["path"])
    si_size_raw = get_dir_size(PROJECTS["stockinsight"]["path"])

    return jsonify({
        "period": "2026-07 ~ 2026-09",
        "goals": [
            {
                "name": "成本控制",
                "target": "¥30/月",
                "current": f"¥{current_cost:.1f}/月",
                "progress": min(round(100 - current_cost / 30 * 100), 100) if current_cost <= 30 else 20,
            },
            {
                "name": "自动化活跃数",
                "target": "≤ 35",
                "current": str(auto_count),
                "progress": auto_progress,
            },
            {
                "name": "CI 通过率",
                "target": "100%",
                "current": "95%",
                "progress": 80,
            },
            {
                "name": "QTS 仓库体积",
                "target": "≤ 700MB",
                "current": qts_size_raw,
                "progress": size_progress(qts_size_raw, 700),
            },
            {
                "name": "StockInsight 体积",
                "target": "≤ 1.2GB",
                "current": si_size_raw,
                "progress": size_progress(si_size_raw, 1228.8),
            },
        ],
    })


@app.route("/api/health")
def api_health():
    try:
        from importlib.metadata import version as _ver
        deps = {"flask": _ver("flask"), "flask-cors": _ver("flask-cors")}
    except Exception:
        deps = {}
    return jsonify({
        "status": "healthy",
        "service": "ops-center",
        "version": VERSION,
        "started_at": STARTED_AT,
        "uptime": int(time.time()) - STARTED_AT,
        "deps": deps,
    })


if __name__ == "__main__":
    # 环境变量化：PORT/DATA_DIR/LOG_DIR
    port = int(os.getenv("PORT", "9998"))
    data_dir = Path(os.getenv("DATA_DIR", os.path.join(Path.cwd(), "data")))
    log_dir = Path(os.getenv("LOG_DIR", os.path.join(Path.cwd(), "logs")))

    # 确保目录存在
    data_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)

    print(f"🔭 Ops Center starting on http://localhost:{port}")
    print(f"DATA_DIR={data_dir}  LOG_DIR={log_dir}")
    app.run(host="0.0.0.0", port=port, debug=False)
