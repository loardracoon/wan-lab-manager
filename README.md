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

1. Open the UI, click **Importar JSON** ("Import JSON"; or drag & drop) to load your real
   topology — router API keys included. It is validated, persisted and
   backed up server-side.
2. Click **Testar ambiente** ("Test environment"): validates each VyOS API (reachability + key),
   creates any missing link-quality profiles, and probes each Sophos WebAdmin
   and the SSH gateway.
3. Click links to switch profiles or disable circuits; click firewalls for
   WebAdmin / SSH.

## Lab environment (reference setup)

This is the environment the tool was built for: Sophos firewalls whose WAN
links run through VyOS routers that play the role of two ISPs. The manager
degrades a circuit by applying a QoS profile to the router interface facing
the firewall, so the firewalls see real latency, loss and jitter on their
WAN links.

### Requirements

- 2 or 3 Sophos firewalls (XGS appliances or virtual firewalls)
- 1 to 3 VyOS 1.4+ routers (the reference below uses two: ISP-01 and ISP-02)
- A host running Docker with an interface on the management network
  (`10.255.255.0/24`)
- Internet access on each VyOS `eth0` (required to register the firewalls in
  Sophos Central)

With a single VyOS router, emulate both ISPs on it by using separate
interfaces (or VLANs, via `control.vif`) for each ISP. With only two
firewalls, drop Firewall-103 and its links.

### What the lab demonstrates

In Sophos Central:

- VPN Orchestrator
- Remote access to the firewalls through Sophos Central
- Firewall policy management by firewall groups
- Sophos AI Assistant

On the firewall:

- SD-WAN failover (VPN or Internet)
- Firewall Health Check

### Topology

```
                 Internet (DHCP on eth0)
              ┌──────────┐      ┌──────────┐
              │  ISP-01  │      │  ISP-02  │      VyOS routers
              └─┬───┬───┬┘      └┬───┬───┬─┘
           eth1 │   │eth2│eth3   eth1│ eth2│ eth3
                │   │    │        │   │    │
   PortB ───────┘   │    │        │   │    │
   Firewall-101     │    │        │   │    │
   PortC ───────────┼────┼────────┘   │    │
                    │    │            │    │
   PortB ───────────┘    │            │    │
   Firewall-102          │            │    │
   PortC ────────────────┼────────────┘    │
                         │                 │
   PortB ────────────────┘                 │
   Firewall-103                            │
   PortC ──────────────────────────────────┘

 Management 10.255.255.0/24: ISP-01 eth4, ISP-02 eth4, every firewall PortE
                             and the Docker host running WAN Lab Manager
```

Each firewall has one WAN link to each ISP (PortB to ISP-01, PortC to ISP-02),
on a dedicated /24 per firewall and ISP. Every one of those six links is a
circuit the manager can degrade or disable.

### Addressing plan

| Device | Interface | Role | Address | Gateway |
|---|---|---|---|---|
| ISP-01 | eth0 | Internet uplink | DHCP | DHCP |
| ISP-01 | eth1 | Link to Firewall-101 | `10.255.101.1/24` | — |
| ISP-01 | eth2 | Link to Firewall-102 | `10.255.102.1/24` | — |
| ISP-01 | eth3 | Link to Firewall-103 | `10.255.103.1/24` | — |
| ISP-01 | eth4 | Management | `10.255.255.1/24` | — |
| ISP-02 | eth0 | Internet uplink | DHCP | DHCP |
| ISP-02 | eth1 | Link to Firewall-101 | `10.255.201.1/24` | — |
| ISP-02 | eth2 | Link to Firewall-102 | `10.255.202.1/24` | — |
| ISP-02 | eth3 | Link to Firewall-103 | `10.255.203.1/24` | — |
| ISP-02 | eth4 | Management | `10.255.255.2/24` | — |
| Firewall-101 | PortA | LAN | `192.168.101.1/24` | — |
| Firewall-101 | PortB | WAN (ISP-01) | `10.255.101.101/24` | `10.255.101.1` |
| Firewall-101 | PortC | WAN (ISP-02) | `10.255.201.101/24` | `10.255.201.1` |
| Firewall-101 | PortD | DMZ | `172.16.101.1/24` | — |
| Firewall-101 | PortE | Management (LAN zone) | `10.255.255.101/24` | — |
| Firewall-102 | PortA | LAN | `192.168.102.1/24` | — |
| Firewall-102 | PortB | WAN (ISP-01) | `10.255.102.102/24` | `10.255.102.1` |
| Firewall-102 | PortC | WAN (ISP-02) | `10.255.202.102/24` | `10.255.202.1` |
| Firewall-102 | PortD | DMZ | `172.16.102.1/24` | — |
| Firewall-102 | PortE | Management (LAN zone) | `10.255.255.102/24` | — |
| Firewall-103 | PortA | LAN | `192.168.103.1/24` | — |
| Firewall-103 | PortB | WAN (ISP-01) | `10.255.103.103/24` | `10.255.103.1` |
| Firewall-103 | PortC | WAN (ISP-02) | `10.255.203.103/24` | `10.255.203.1` |
| Firewall-103 | PortD | DMZ | `172.16.103.1/24` | — |
| Firewall-103 | PortE | Management (LAN zone) | `10.255.255.103/24` | — |
| Docker host | any | WAN Lab Manager | e.g. `10.255.255.10/24` | — |

Convention: the third octet of a WAN subnet is `1xx` for ISP-01 and `2xx` for
ISP-02, where `xx` is the firewall number; the firewall's host address on its
WAN links and on the management network is `1xx`.

### VyOS configuration (ISP-01)

```
set system host-name 'ISP-01'

set interfaces ethernet eth0 address 'dhcp'
set interfaces ethernet eth0 description 'INTERNET'
set interfaces ethernet eth1 address '10.255.101.1/24'
set interfaces ethernet eth1 description 'FIREWALL-101'
set interfaces ethernet eth2 address '10.255.102.1/24'
set interfaces ethernet eth2 description 'FIREWALL-102'
set interfaces ethernet eth3 address '10.255.103.1/24'
set interfaces ethernet eth3 description 'FIREWALL-103'
set interfaces ethernet eth4 address '10.255.255.1/24'
set interfaces ethernet eth4 description 'MGMT'

# Internet access for the firewalls (Sophos Central, updates, SD-WAN probes)
set nat source rule 100 outbound-interface name 'eth0'
set nat source rule 100 source address '10.255.0.0/16'
set nat source rule 100 translation address 'masquerade'

# Optional: reach the ISP-02 WAN subnets, so a firewall WAN on ISP-01 can
# build a tunnel to a firewall WAN on ISP-02 (cross-ISP VPN)
set protocols static route 10.255.200.0/22 next-hop '10.255.255.2'

# HTTP API used by WAN Lab Manager, bound to the management network only
set service https api keys id lab-automation key '<ISP-01-API-KEY>'
set service https api rest
set service https listen-address '10.255.255.1'
set service https allow-client address '10.255.255.0/24'

set service ssh listen-address '10.255.255.1'
commit; save
```

ISP-02 is identical except for: host name `ISP-02`, addresses
`10.255.201.1/24`, `10.255.202.1/24`, `10.255.203.1/24` and `10.255.255.2/24`,
listen addresses `10.255.255.2`, its own API key, and the optional static
route `10.255.100.0/22 next-hop 10.255.255.1`.

The link-quality profiles (`qos policy network-emulator ...`) do **not** need
to be configured by hand: **Testar ambiente** creates any missing profile on
every router. A complete router configuration template is available in
[vyos.config.example](vyos.config.example).

Notes:

- The profile is applied as `qos interface <if> egress`, so it shapes the
  traffic leaving the router towards the firewall (the download direction of
  that WAN link).
- TLS verification is disabled for the API calls (self-signed lab
  certificates) — do not expose this service outside the lab.

### Sophos Firewall configuration

On each firewall:

1. **Interfaces** — configure PortA to PortE as in the addressing plan:
   PortA in the LAN zone, PortB and PortC in the WAN zone (each with its ISP
   gateway), PortD in the DMZ zone and PortE in the LAN zone for management.
2. **Device access** — allow HTTPS and SSH on the zone of PortE, so the
   manager can reach the WebAdmin (`https://10.255.255.1xx:4444`) and the SSH
   console.
3. **SD-WAN profiles** — create the following profiles, each with all WAN
   interfaces (PortB and PortC) as gateways:

   | Profile | Gateways | Routing | Selection criteria |
   |---|---|---|---|
   | `LB-INTERNET` | All WAN interfaces | Load balance | Session-based, with SLA thresholds |
   | `LL-INTERNET` | All WAN interfaces | Best path | Lowest latency |
   | `PL-INTERNET` | All WAN interfaces | Best path | Lowest packet loss |

4. **Sophos Central** — register the firewall in Sophos Central and enable
   Central management, so the VPN Orchestrator, group policies, remote access
   and the AI Assistant can be demonstrated.

Use the manager's link profiles to exercise the SD-WAN profiles: apply
`Latency` to a circuit to make `LL-INTERNET` move traffic to the other WAN,
`PacketLoss` for `PL-INTERNET`, or disable a circuit to force a failover.

### Matching topology file

Import this file (**Importar JSON**) to drive the environment above. Replace
the API keys with the ones configured on each router.

```json
{
  "settings": { "pollIntervalSec": 15, "defaultProfile": "OK" },
  "sshGateway": {
    "urlTemplate": "http://{origin-host}:8888/?hostname={host}&username={user}&port={port}"
  },
  "vyos": {
    "routers": {
      "isp-01": { "name": "ISP-01", "apiUrl": "https://10.255.255.1", "apiKey": "<ISP-01-API-KEY>" },
      "isp-02": { "name": "ISP-02", "apiUrl": "https://10.255.255.2", "apiKey": "<ISP-02-API-KEY>" }
    }
  },
  "linkProfiles": {
    "OK":         { "label": "OK",         "delayMs": 2,   "lossPct": 0, "reorderingPct": 0,  "jitterMs": 0,  "bandwidthMbps": 1000 },
    "Latency":    { "label": "Latency",    "delayMs": 250, "lossPct": 0, "reorderingPct": 0,  "jitterMs": 0,  "bandwidthMbps": 1000 },
    "PacketLoss": { "label": "PacketLoss", "delayMs": 10,  "lossPct": 8, "reorderingPct": 0,  "jitterMs": 0,  "bandwidthMbps": 1000 },
    "Jitter":     { "label": "Jitter",     "delayMs": 60,  "lossPct": 0, "reorderingPct": 30, "jitterMs": 25, "bandwidthMbps": 1000 }
  },
  "isps": [
    { "id": "isp-01", "name": "ISP-01", "asn": "AS65001", "color": "#4fc3f7", "grid": [3.0, 1.2] },
    { "id": "isp-02", "name": "ISP-02", "asn": "AS65002", "color": "#34d399", "grid": [7.8, 1.2] }
  ],
  "sites": [
    { "id": "site-101", "name": "Site 101", "grid": [1.4, 8.6],
      "firewall": { "hostname": "Firewall-101", "model": "Sophos XGS", "mgmtUrl": "https://10.255.255.101:4444",
                    "sshHost": "10.255.255.101", "sshUser": "admin", "sshPort": 22 } },
    { "id": "site-102", "name": "Site 102", "grid": [5.4, 9.3],
      "firewall": { "hostname": "Firewall-102", "model": "Sophos XGS", "mgmtUrl": "https://10.255.255.102:4444",
                    "sshHost": "10.255.255.102", "sshUser": "admin", "sshPort": 22 } },
    { "id": "site-103", "name": "Site 103", "grid": [9.4, 8.6],
      "firewall": { "hostname": "Firewall-103", "model": "Sophos XGS", "mgmtUrl": "https://10.255.255.103:4444",
                    "sshHost": "10.255.255.103", "sshUser": "admin", "sshPort": 22 } }
  ],
  "links": [
    { "id": "isp01-101", "from": "isp-01", "to": "site-101", "circuit": "ISP01-FW101",
      "control": { "router": "isp-01", "interface": "eth1" }, "stats": { "bandwidthMbps": 1000, "usagePct": 40, "status": "up" } },
    { "id": "isp01-102", "from": "isp-01", "to": "site-102", "circuit": "ISP01-FW102",
      "control": { "router": "isp-01", "interface": "eth2" }, "stats": { "bandwidthMbps": 1000, "usagePct": 40, "status": "up" } },
    { "id": "isp01-103", "from": "isp-01", "to": "site-103", "circuit": "ISP01-FW103",
      "control": { "router": "isp-01", "interface": "eth3" }, "stats": { "bandwidthMbps": 1000, "usagePct": 40, "status": "up" } },
    { "id": "isp02-101", "from": "isp-02", "to": "site-101", "circuit": "ISP02-FW101",
      "control": { "router": "isp-02", "interface": "eth1" }, "stats": { "bandwidthMbps": 1000, "usagePct": 40, "status": "up" } },
    { "id": "isp02-102", "from": "isp-02", "to": "site-102", "circuit": "ISP02-FW102",
      "control": { "router": "isp-02", "interface": "eth2" }, "stats": { "bandwidthMbps": 1000, "usagePct": 40, "status": "up" } },
    { "id": "isp02-103", "from": "isp-02", "to": "site-103", "circuit": "ISP02-FW103",
      "control": { "router": "isp-02", "interface": "eth3" }, "stats": { "bandwidthMbps": 1000, "usagePct": 40, "status": "up" } }
  ]
}
```

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
