"""Runtime configuration, all overridable via environment variables."""
import os

DATA_DIR = os.environ.get("DATA_DIR", "/data")
TOPOLOGY_FILE = os.path.join(DATA_DIR, "topology.json")
HISTORY_DIR = os.path.join(DATA_DIR, "history")

# Optional shared token. When set, every mutating endpoint requires the
# X-Auth-Token header. Read-only endpoints stay open (lab tool).
API_TOKEN = os.environ.get("API_TOKEN", "")

# How many timestamped config backups to keep in HISTORY_DIR.
HISTORY_KEEP = int(os.environ.get("HISTORY_KEEP", "20"))

# Timeout (seconds) for calls to the VyOS HTTP API and reachability probes.
VYOS_TIMEOUT = float(os.environ.get("VYOS_TIMEOUT", "10"))
PROBE_TIMEOUT = float(os.environ.get("PROBE_TIMEOUT", "5"))

REDACTED = "__REDACTED__"
