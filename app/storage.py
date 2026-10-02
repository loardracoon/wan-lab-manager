"""Topology persistence: atomic writes plus timestamped history backups."""
import asyncio
import json
import os
import re
import shutil
import time

from . import config

_lock = asyncio.Lock()

DEFAULT_TOPOLOGY: dict = {
    "settings": {"pollIntervalSec": 15, "defaultProfile": "OK"},
    "sshGateway": {
        "urlTemplate": "http://{origin-host}:8888/?hostname={host}&username={user}&port={port}"
    },
    "vyos": {
        "routers": {
            "rt-a": {"name": "RT-A", "apiUrl": "https://10.255.255.11", "apiKey": ""},
            "rt-b": {"name": "RT-B", "apiUrl": "https://10.255.255.12", "apiKey": ""},
        }
    },
    "linkProfiles": {
        "OK":         {"label": "OK",         "delayMs": 2,   "lossPct": 0, "reorderingPct": 0,  "jitterMs": 0,  "bandwidthMbps": 1000},
        "Latency":    {"label": "Latency",    "delayMs": 250, "lossPct": 0, "reorderingPct": 0,  "jitterMs": 0,  "bandwidthMbps": 1000},
        "PacketLoss": {"label": "PacketLoss", "delayMs": 10,  "lossPct": 8, "reorderingPct": 0,  "jitterMs": 0,  "bandwidthMbps": 1000},
        "Jitter":     {"label": "Jitter",     "delayMs": 60,  "lossPct": 0, "reorderingPct": 30, "jitterMs": 25, "bandwidthMbps": 1000},
    },
    "isps": [
        {"id": "isp-a", "name": "ISP Alpha", "asn": "AS26599", "color": "#4fc3f7", "grid": [3.0, 1.2]},
        {"id": "isp-b", "name": "ISP Beta",  "asn": "AS28573", "color": "#34d399", "grid": [7.8, 1.2]},
    ],
    "sites": [
        {"id": "site-sp", "name": "Site São Paulo", "grid": [1.4, 8.6],
         "firewall": {"hostname": "fw-sp-01", "model": "Sophos XGS 2300", "mgmtUrl": "https://10.10.1.1:4444",
                      "sshHost": "10.10.1.1", "sshUser": "admin", "sshPort": 22}},
        {"id": "site-rj", "name": "Site Rio de Janeiro", "grid": [5.4, 9.3],
         "firewall": {"hostname": "fw-rj-01", "model": "Sophos XGS 2100", "mgmtUrl": "https://10.20.1.1:4444",
                      "sshHost": "10.20.1.1", "sshUser": "admin", "sshPort": 22}},
        {"id": "site-bh", "name": "Site Belo Horizonte", "grid": [9.4, 8.6],
         "firewall": {"hostname": "fw-bh-01", "model": "Sophos XGS 136", "mgmtUrl": "https://10.30.1.1:4444",
                      "sshHost": "10.30.1.1", "sshUser": "admin", "sshPort": 22}},
    ],
    "links": [
        {"id": "a-sp", "from": "isp-a", "to": "site-sp", "circuit": "MPLS-0142",
         "control": {"router": "rt-a", "interface": "eth1"},
         "stats": {"bandwidthMbps": 500, "usagePct": 62, "latencyMs": 4.2, "lossPct": 0.0, "jitterMs": 0.8, "status": "up", "profile": "OK"}},
        {"id": "a-rj", "from": "isp-a", "to": "site-rj", "circuit": "MPLS-0177",
         "control": {"router": "rt-a", "interface": "eth2"},
         "stats": {"bandwidthMbps": 300, "usagePct": 41, "latencyMs": 9.6, "lossPct": 0.1, "jitterMs": 1.4, "status": "up", "profile": "OK"}},
        {"id": "a-bh", "from": "isp-a", "to": "site-bh", "circuit": "MPLS-0203",
         "control": {"router": "rt-a", "interface": "eth3"},
         "stats": {"bandwidthMbps": 200, "usagePct": 55, "latencyMs": 14.8, "lossPct": 0.0, "jitterMs": 2.2, "status": "up", "profile": "OK"}},
        {"id": "b-sp", "from": "isp-b", "to": "site-sp", "circuit": "DIA-9010",
         "control": {"router": "rt-b", "interface": "eth1"},
         "stats": {"bandwidthMbps": 400, "usagePct": 35, "latencyMs": 5.1, "lossPct": 0.0, "jitterMs": 0.6, "status": "up", "profile": "OK"}},
        {"id": "b-rj", "from": "isp-b", "to": "site-rj", "circuit": "DIA-9022",
         "control": {"router": "rt-b", "interface": "eth2"},
         "stats": {"bandwidthMbps": 300, "usagePct": 28, "latencyMs": 8.9, "lossPct": 0.0, "jitterMs": 1.1, "status": "up", "profile": "OK"}},
        {"id": "b-bh", "from": "isp-b", "to": "site-bh", "circuit": "DIA-9031",
         "control": {"router": "rt-b", "interface": "eth3"},
         "stats": {"bandwidthMbps": 200, "usagePct": 27, "latencyMs": 11.3, "lossPct": 0.0, "jitterMs": 2.1, "status": "up", "profile": "OK"}},
    ],
}


def _ensure_dirs() -> None:
    os.makedirs(config.HISTORY_DIR, exist_ok=True)


def load() -> dict:
    """Load the persisted topology; seed the default on first run."""
    _ensure_dirs()
    if not os.path.exists(config.TOPOLOGY_FILE):
        _atomic_write(config.TOPOLOGY_FILE, DEFAULT_TOPOLOGY)
    with open(config.TOPOLOGY_FILE, encoding="utf-8") as fh:
        return json.load(fh)


def _atomic_write(path: str, data: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


def _backup_current() -> None:
    if not os.path.exists(config.TOPOLOGY_FILE):
        return
    stamp = time.strftime("%Y%m%d-%H%M%S")
    shutil.copy2(config.TOPOLOGY_FILE, os.path.join(config.HISTORY_DIR, f"topology-{stamp}.json"))
    backups = sorted(os.listdir(config.HISTORY_DIR))
    for name in backups[: max(0, len(backups) - config.HISTORY_KEEP)]:
        os.remove(os.path.join(config.HISTORY_DIR, name))


async def save(data: dict) -> None:
    async with _lock:
        _ensure_dirs()
        _backup_current()
        _atomic_write(config.TOPOLOGY_FILE, data)


def list_history() -> list[dict]:
    _ensure_dirs()
    out = []
    for name in sorted(os.listdir(config.HISTORY_DIR), reverse=True):
        full = os.path.join(config.HISTORY_DIR, name)
        out.append({"name": name, "size": os.path.getsize(full),
                    "modified": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(os.path.getmtime(full)))})
    return out


async def restore(name: str) -> dict:
    """Bring a history snapshot back as the live topology."""
    if not re.fullmatch(r"topology-\d{8}-\d{6}\.json", name):
        raise FileNotFoundError(name)
    full = os.path.join(config.HISTORY_DIR, name)
    if not os.path.exists(full):
        raise FileNotFoundError(name)
    with open(full, encoding="utf-8") as fh:
        data = json.load(fh)
    await save(data)
    return data
