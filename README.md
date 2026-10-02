# WAN Lab Manager

Isometric WAN topology console for a VyOS + Sophos lab: two ISPs, three sites,
one Sophos XGS firewall per site. The canvas renders the topology in isometric
projection with live traffic animation; clicking a firewall opens its WebAdmin
(and a web SSH console) in a 65% drawer; clicking a link opens a control panel
that switches pre-defined link-quality profiles (OK / Latency / PacketLoss /
Jitter) or disables the circuit — all executed against the VyOS HTTP API.

This version runs as a containerized Python service that centralizes
configuration upload/export, persistence, VyOS access and state polling.

## Architecture

```
 Browser (canvas UI) ──HTTP──> FastAPI app (port 8080)
                                 ├── serves the static UI
                                 ├── /api/topology     persistence + history (/data volume)
                                 ├── /api/links/apply  VyOS writes (keys stay server-side)
                                 ├── /api/test         environment validation + profile bootstrap
                                 ├── /api/state        cached state from the background poller
                                 └── background poller ──HTTPS──> VyOS API (RT-A / RT-B)
 Browser (SSH tab iframe) ──HTTP──> webssh container (port 8888) ──SSH──> firewalls/routers
```

Key design decisions:

- **VyOS API keys never reach the browser.** All router calls go through the
  backend; `GET /api/topology` and the default export redact `apiKey`.
  Re-importing a redacted export keeps the stored keys (the `__REDACTED__`
  marker is merged back).
- **Server-side polling.** One poller reconciles router reality (interface
  `disable` state and `qos interface <if> egress` policy) on the interval set
  by `settings.pollIntervalSec`; every browser session reads the cached
  `/api/state` cheaply. Changes made directly on the VyOS CLI show up in the
  UI on the next cycle.
- **Profiles as shared policies.** Each entry in `linkProfiles` becomes a
  `qos policy network-emulator` policy on every router (created by the
  environment test when missing). Applying a profile to a link is a single
  atomic `set qos interface <if> egress <profile>`.
- **Persistence with history.** The live topology lives in `/data/topology.json`
  (atomic writes); every import/restore snapshots the previous version into
  `/data/history/` (keeping `HISTORY_KEEP` files), restorable from the UI.

## Quick start

```bash
docker compose up -d --build
# UI:      http://localhost:8080
# WebSSH:  http://localhost:8888  (used by the drawer's SSH tab)
```

First run seeds a default topology. Then:

1. Open the UI, click **Importar JSON** (or drag & drop) to load your real
   topology — router API keys included. It is validated, persisted and
   backed up server-side.
2. Click **Testar ambiente**: validates each VyOS API (reachability + key),
   creates any missing link-quality profiles, and probes each Sophos WebAdmin
   and the SSH gateway.
3. Click links to switch profiles or disable circuits; click firewalls for
   WebAdmin / SSH.

## VyOS prerequisites (per router)

```
set service https api keys id ui key <YOUR-KEY>
commit; save
```

The routers are expected on an out-of-band management network reachable from
the container (e.g. RT-A `https://10.255.255.11`, RT-B `https://10.255.255.12`).
TLS verification is disabled for these calls (self-signed lab certificates) —
do not expose this service outside the lab.

## Configuration file

Everything is one JSON document (see the seeded default for a full example):

| Section | Purpose |
|---|---|
| `settings` | `pollIntervalSec`, `defaultProfile` |
| `vyos.routers` | `{ id: { name, apiUrl, apiKey } }` |
| `linkProfiles` | `{ name: { label, delayMs, lossPct, reorderingPct, corruptionPct, bandwidthMbps, jitterMs } }` |
| `isps` / `sites` | nodes: names, colors, grid positions, firewall mgmt/SSH data |
| `links` | `from`/`to`, `circuit`, `control: { router, interface, vif? }`, display `stats` |
| `sshGateway.urlTemplate` | web SSH gateway URL; `{origin-host}`, `{host}`, `{user}`, `{port}` are substituted |

Notes on profiles: VyOS network-emulator applies delay only when the policy
also defines bandwidth (handled automatically). The CLI does not expose
netem's jitter parameter, so the Jitter profile emulates it with
`delay + reordering` (reordered packets skip the delay, producing measurable
latency variation); `jitterMs` is display-only.

## HTTP API

| Method & path | Description | Auth* |
|---|---|---|
| `GET /api/topology` | Current config (keys redacted) | — |
| `PUT /api/topology` | Import/replace config (validated; redacted keys merged) | ✓ |
| `GET /api/topology/export` | Download JSON (`?include_secrets=1` needs auth) | (✓) |
| `GET /api/topology/history` | List backups | — |
| `POST /api/topology/restore/{name}` | Restore a backup | ✓ |
| `GET /api/state` | Cached per-link/router state from the poller | — |
| `POST /api/links/{id}/apply` | `{ enabled, profile, persist }` → VyOS commit | ✓ |
| `POST /api/test` | Environment validation + profile bootstrap | ✓ |
| `GET /healthz` | Liveness + last poll info | — |

\* Auth applies only when `API_TOKEN` is set (header `X-Auth-Token`; the UI
prompts for it on the first 401).

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `DATA_DIR` | `/data` | Persistence directory (mount a volume) |
| `API_TOKEN` | *(empty)* | Enables token auth on mutating endpoints |
| `HISTORY_KEEP` | `20` | Config backups retained |
| `VYOS_TIMEOUT` | `10` | Seconds per VyOS API call |
| `PROBE_TIMEOUT` | `5` | Seconds per WebAdmin/SSH-gateway probe |

## Development

```bash
pip install -r requirements.txt
DATA_DIR=./data uvicorn app.main:app --reload --port 8080
```

## Security notes

Lab tool, lab assumptions: TLS verification disabled towards the routers,
optional (not mandatory) token auth, WebAdmin iframes depend on the firewall
allowing framing. If this ever fronts anything beyond an isolated lab, put it
behind a reverse proxy with real authentication and TLS, set `API_TOKEN`, and
restrict the network path to the management VRF.
