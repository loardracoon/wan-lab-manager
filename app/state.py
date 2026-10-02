"""Server-side poller: reconciles router reality into a cached state
that any number of browser sessions can read cheaply, plus the apply
and environment-test actions (they share a lock with the poller so a
poll never races a write)."""
import asyncio
import datetime as dt

import httpx

from . import config, vyos


class Poller:
    def __init__(self):
        self.topology: dict = {}
        self.state: dict = {"routers": {}, "links": {}, "updatedAt": None}
        self.busy = asyncio.Lock()   # held during apply/test
        self._task: asyncio.Task | None = None
        self._wake = asyncio.Event()

    # ------------------------------------------------------------------
    def set_topology(self, topology: dict) -> None:
        self.topology = topology
        self._wake.set()  # poll soon after any config change

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self) -> None:
        await asyncio.sleep(2)
        while True:
            try:
                await self.poll_once()
            except Exception:  # noqa: BLE001 — the poller must never die
                pass
            interval = float((self.topology.get("settings") or {}).get("pollIntervalSec", 15) or 0)
            if interval <= 0:
                interval = 3600
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass

    # ------------------------------------------------------------------
    async def poll_once(self) -> None:
        if self.busy.locked():
            return
        routers = (self.topology.get("vyos") or {}).get("routers", {}) or {}
        links = self.topology.get("links", [])
        router_state: dict = {}
        link_state: dict = dict(self.state.get("links", {}))

        for rid, router in routers.items():
            try:
                await vyos.exists(router, ["system"])  # proves API + key
                qos_cfg = await vyos.show_config(router, ["qos", "interface"])
                if_cfg = await vyos.show_config(router, ["interfaces", "ethernet"])
                router_state[rid] = {"ok": True, "error": None}
            except vyos.VyOSError as exc:
                router_state[rid] = {"ok": False, "error": str(exc)}
                continue

            for link in links:
                ctl = link.get("control")
                if not ctl or ctl.get("router") != rid:
                    continue
                if ctl.get("vif") is not None:
                    eth = ((if_cfg.get(ctl["interface"]) or {}).get("vif") or {}).get(str(ctl["vif"]))
                else:
                    eth = if_cfg.get(ctl["interface"])
                disabled = isinstance(eth, dict) and "disable" in eth
                egress = (qos_cfg.get(vyos.qos_if(ctl)) or {}).get("egress")
                link_state[link["id"]] = {
                    "disabled": bool(disabled),
                    "egress": egress if isinstance(egress, str) else None,
                }

        self.state = {
            "routers": router_state,
            "links": link_state,
            "updatedAt": dt.datetime.now().isoformat(timespec="seconds"),
        }

    # ------------------------------------------------------------------
    async def apply_link(self, link: dict, enabled: bool, profile: str, persist: bool) -> list[dict]:
        """Push enable/disable + egress profile to the owning router."""
        lines: list[dict] = []
        ctl = link.get("control")
        routers = (self.topology.get("vyos") or {}).get("routers", {}) or {}
        router = routers.get(ctl.get("router")) if ctl else None
        if not router:
            raise vyos.VyOSError("link has no VyOS control configuration")

        ifp = vyos.if_path(ctl)
        qif = vyos.qos_if(ctl)

        async with self.busy:
            ops: list[dict] = []
            if enabled:
                lines.append({"level": "info", "text": f"→ {router['name']}: checking state of {qif}…"})
                if await vyos.exists(router, [*ifp, "disable"]):
                    ops.append({"op": "delete", "path": [*ifp, "disable"]})
            else:
                ops.append({"op": "set", "path": [*ifp, "disable"]})
            ops.append({"op": "set", "path": ["qos", "interface", qif, "egress", profile]})

            lines.append({"level": "info", "text": f"→ {router['name']}: profile \"{profile}\" on {qif} + commit…"})
            await vyos.configure(router, ops)
            lines.append({"level": "ok", "text": "✓ commit completed"})

            if persist:
                await vyos.save_config(router)
                lines.append({"level": "ok", "text": "✓ configuration saved to the router disk"})

        # Reflect immediately in the cached state
        self.state.setdefault("links", {})[link["id"]] = {"disabled": not enabled, "egress": profile}
        self.state["updatedAt"] = dt.datetime.now().isoformat(timespec="seconds")
        lines.append({"level": "ok", "text": f"✓ link {'enabled' if enabled else 'disabled'}"})
        return lines

    # ------------------------------------------------------------------
    async def run_tests(self) -> list[dict]:
        """API up? key accepted? profiles present (create missing)?
        WebAdmin and SSH gateway reachable?"""
        lines: list[dict] = []
        topo = self.topology
        profiles = topo.get("linkProfiles", {}) or {}
        routers = (topo.get("vyos") or {}).get("routers", {}) or {}

        async with self.busy:
            for router in routers.values():
                lines.append({"level": "info", "text": f"— {router['name']} ({router['apiUrl']})"})
                try:
                    await vyos.exists(router, ["system"])
                    lines.append({"level": "ok", "text": "  ✓ HTTPS API responding and key accepted"})
                except vyos.VyOSError as exc:
                    lines.append({"level": "err", "text": f"  ✗ API unreachable: {exc}"})
                    lines.append({"level": "err", "text": "    Check 'set service https api' and the key in vyos.routers."})
                    continue
                for key, prof in profiles.items():
                    try:
                        if await vyos.exists(router, ["qos", "policy", "network-emulator", key]):
                            lines.append({"level": "ok", "text": f"  ✓ profile \"{key}\" present"})
                        else:
                            lines.append({"level": "warn", "text": f"  … profile \"{key}\" missing — creating"})
                            await vyos.configure(router, vyos.profile_ops(key, prof))
                            lines.append({"level": "ok",
                                          "text": f"  ✓ profile \"{key}\" created "
                                                  f"({prof.get('delayMs', 0)}ms · {prof.get('lossPct', 0)}% loss)"})
                    except vyos.VyOSError as exc:
                        lines.append({"level": "err", "text": f"  ✗ profile \"{key}\": {exc}"})

            async with httpx.AsyncClient(verify=False, timeout=config.PROBE_TIMEOUT) as probe:
                for site in topo.get("sites", []):
                    url = (site.get("firewall") or {}).get("mgmtUrl")
                    if not url:
                        continue
                    host = (site.get("firewall") or {}).get("hostname", site["id"])
                    try:
                        res = await probe.get(url)
                        lines.append({"level": "ok", "text": f"✓ WebAdmin {host} reachable (HTTP {res.status_code})"})
                    except httpx.HTTPError as exc:
                        lines.append({"level": "warn",
                                      "text": f"⚠ WebAdmin {host} did not respond ({url}): {exc.__class__.__name__}"})

                tpl = (topo.get("sshGateway") or {}).get("urlTemplate", "")
                if tpl:
                    origin = tpl.split("?")[0].replace("{origin-host}", "webssh")
                    try:
                        await probe.get(origin)
                        lines.append({"level": "ok", "text": f"✓ SSH gateway reachable ({origin})"})
                    except httpx.HTTPError:
                        lines.append({"level": "warn", "text": "⚠ SSH gateway did not respond — the SSH console tab will not load."})
                else:
                    lines.append({"level": "info", "text": "· SSH gateway not configured (sshGateway.urlTemplate is empty)."})

        lines.append({"level": "info", "text": "— test completed"})
        return lines


poller = Poller()
