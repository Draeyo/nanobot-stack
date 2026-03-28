"""System metrics API — CPU, RAM, disk, latency, alerts via psutil.

Mounted at /api/metrics by app.py.  Provides the backend for the
admin Monitoring tab designed in Stitch.
"""
from __future__ import annotations

import logging
import os
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter

logger = logging.getLogger("rag-bridge.system-metrics")

router = APIRouter(prefix="/api/metrics", tags=["system-metrics"])

# ---------------------------------------------------------------------------
# History ring-buffers (60-minute window, 1 sample/minute)
# ---------------------------------------------------------------------------
_cpu_history: deque[dict] = deque(maxlen=60)
_ram_history: deque[dict] = deque(maxlen=60)
_alerts: deque[dict] = deque(maxlen=50)

# 24-hour heatmap (one entry per 15min = 96 slots)
_load_heatmap: deque[dict] = deque(maxlen=96)

_boot_time: float | None = None


def _try_import_psutil():
    """Import psutil lazily — gracefully degrade if not installed."""
    try:
        import psutil  # type: ignore[import-untyped]
        return psutil
    except ImportError:
        return None


def _snap() -> dict[str, Any]:
    """Take a point-in-time system snapshot."""
    psutil = _try_import_psutil()
    if psutil is None:
        return {"error": "psutil not installed"}

    global _boot_time
    if _boot_time is None:
        _boot_time = psutil.boot_time()

    cpu_pct = psutil.cpu_percent(interval=0.1)
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage("/")

    # Disk I/O (delta since boot — best-effort)
    try:
        dio = psutil.disk_io_counters()
        disk_read_mb = round(dio.read_bytes / 1024 / 1024, 1) if dio else 0
        disk_write_mb = round(dio.write_bytes / 1024 / 1024, 1) if dio else 0
    except Exception:
        disk_read_mb = disk_write_mb = 0

    # Network
    try:
        net = psutil.net_io_counters()
        net_sent_mb = round(net.bytes_sent / 1024 / 1024, 1)
        net_recv_mb = round(net.bytes_recv / 1024 / 1024, 1)
    except Exception:
        net_sent_mb = net_recv_mb = 0

    now_iso = datetime.now(timezone.utc).isoformat()
    uptime_s = time.time() - _boot_time if _boot_time else 0

    return {
        "timestamp": now_iso,
        "cpu_percent": cpu_pct,
        "ram_used_gb": round(mem.used / 1024**3, 2),
        "ram_total_gb": round(mem.total / 1024**3, 2),
        "ram_percent": mem.percent,
        "disk_used_gb": round(disk.used / 1024**3, 2),
        "disk_total_gb": round(disk.total / 1024**3, 2),
        "disk_percent": disk.percent,
        "disk_read_mb": disk_read_mb,
        "disk_write_mb": disk_write_mb,
        "net_sent_mb": net_sent_mb,
        "net_recv_mb": net_recv_mb,
        "uptime_seconds": round(uptime_s),
    }


def record_sample() -> None:
    """Called periodically (e.g. every 60s) to store a history sample."""
    snap = _snap()
    if "error" in snap:
        return
    ts = snap["timestamp"]
    _cpu_history.append({"t": ts, "v": snap["cpu_percent"]})
    _ram_history.append({"t": ts, "v": snap["ram_percent"]})
    _load_heatmap.append({"t": ts, "cpu": snap["cpu_percent"], "ram": snap["ram_percent"]})

    # Auto-generate alerts
    if snap["cpu_percent"] > 90:
        _alerts.append({"ts": ts, "level": "high", "title": "High CPU Usage",
                        "detail": f"CPU at {snap['cpu_percent']}%"})
    if snap["ram_percent"] > 90:
        _alerts.append({"ts": ts, "level": "high", "title": "High Memory Pressure",
                        "detail": f"RAM at {snap['ram_percent']}% ({snap['ram_used_gb']}/{snap['ram_total_gb']} GB)"})
    if snap["disk_percent"] > 90:
        _alerts.append({"ts": ts, "level": "high", "title": "Disk Space Low",
                        "detail": f"Disk at {snap['disk_percent']}%"})


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/snapshot")
def snapshot():
    """Current system metrics snapshot."""
    return _snap()


@router.get("/history")
def history():
    """60-minute CPU and RAM history for the live resource chart."""
    return {
        "cpu": list(_cpu_history),
        "ram": list(_ram_history),
    }


@router.get("/heatmap")
def heatmap():
    """24-hour system load heatmap (96 slots of 15min)."""
    return {"slots": list(_load_heatmap)}


@router.get("/alerts")
def alerts():
    """Recent system alerts."""
    return {"alerts": list(_alerts)}
