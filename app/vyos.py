"""Async client for the VyOS HTTP API (POST multipart: data + key).

The routers live on an isolated management network and typically run
self-signed certificates, so TLS verification is disabled here — keep
this service on the lab side, never exposed to the internet.
"""
import json
from typing import Any

import httpx

from . import config

_client: httpx.AsyncClient | None = None


class VyOSError(Exception):
    pass


def client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(verify=False, timeout=config.VYOS_TIMEOUT)
    return _client


async def close() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


async def call(router: dict, endpoint: str, payload: Any) -> Any:
    url = router["apiUrl"].rstrip("/") + endpoint
    try:
        res = await client().post(
            url, data={"data": json.dumps(payload), "key": router.get("apiKey", "")}
        )
    except (httpx.HTTPError, httpx.InvalidURL) as exc:
        raise VyOSError(f"unreachable ({exc.__class__.__name__}: {exc})") from exc
    try:
        body = res.json()
    except ValueError as exc:
        raise VyOSError(f"invalid API response (HTTP {res.status_code})") from exc
    if not body.get("success"):
        raise VyOSError(body.get("error") or f"API error (HTTP {res.status_code})")
    return body.get("data")


async def exists(router: dict, path: list[str]) -> bool:
    return bool(await call(router, "/retrieve", {"op": "exists", "path": path}))


async def show_config(router: dict, path: list[str]) -> dict:
    """showConfig on an unconfigured subtree raises — treat as empty."""
    try:
        data = await call(router, "/retrieve", {"op": "showConfig", "path": path})
        return data if isinstance(data, dict) else {}
    except VyOSError:
        return {}


async def configure(router: dict, ops: list[dict]) -> Any:
    return await call(router, "/configure", ops)


async def save_config(router: dict) -> Any:
    return await call(router, "/config-file", {"op": "save"})


# ---------------------------------------------------------------------------
# Path helpers shared by apply / poll / test
# ---------------------------------------------------------------------------

def if_path(ctl: dict) -> list[str]:
    if ctl.get("vif") is not None:
        return ["interfaces", "ethernet", ctl["interface"], "vif", str(ctl["vif"])]
    return ["interfaces", "ethernet", ctl["interface"]]


def qos_if(ctl: dict) -> str:
    return f"{ctl['interface']}.{ctl['vif']}" if ctl.get("vif") is not None else ctl["interface"]


def profile_ops(name: str, p: dict) -> list[dict]:
    """Materialize a link profile as a network-emulator policy
    (VyOS 1.4+/1.5 syntax). delay only takes effect when the policy
    also carries bandwidth, hence the mandatory bandwidth op."""
    base = ["qos", "policy", "network-emulator", name]
    ops = [
        {"op": "set", "path": [*base, "delay", f"{p.get('delayMs', 0)}"]},
        {"op": "set", "path": [*base, "bandwidth", f"{p.get('bandwidthMbps', 1000)}mbit"]},
        {"op": "set", "path": [*base, "loss", str(p.get("lossPct", 0))]},
    ]
    if p.get("reorderingPct"):
        ops.append({"op": "set", "path": [*base, "reordering", str(p["reorderingPct"])]})
    if p.get("corruptionPct"):
        ops.append({"op": "set", "path": [*base, "corruption", str(p["corruptionPct"])]})
    return ops
