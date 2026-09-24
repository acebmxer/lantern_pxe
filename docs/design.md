# Design scope

Working notes from the decision to build this as a separate project
rather than a Podman port of [Beacon](https://github.com/acebmxer/beacon_pxe).
Nothing here is implemented yet — this is the topology to build against.

## Constraint

Two of Beacon's services need real host root, independent of runtime:

- **NFS** — an in-kernel NFS export requires loading the host's `nfsd`
  kernel module. Root-only, no rootless path, on any container runtime.
- **DHCP/TFTP on the physical boot LAN** — PXE clients need a DHCP
  server reachable on the real boot-LAN NIC before they have an IP.
  `NET_ADMIN`/`NET_RAW` inside a rootless user namespace only reach the
  container's own namespace, not the host's physical interface, even
  with `--network=host`.

Rootless Podman can't grant either. Running them rootful defeats the
reason for choosing Podman, so the fix is a different topology, not a
Podman flag.

## Target topology

| Concern | Beacon (Docker) | Lantern (Podman) |
|---|---|---|
| Web UI / API | containerized (FastAPI) | containerized, rootless |
| Image management, DB | containerized (FastAPI) | containerized, rootless |
| Boot menu (iPXE) generation | containerized | containerized, rootless |
| HTTP boot root (nginx) | containerized | containerized, rootless |
| DHCP/TFTP (dnsmasq) | containerized, host network, `NET_ADMIN` | **host-level systemd service**, not containerized |
| NFS boot roots | containerized, `privileged: true`, host network | **replaced** — see options below |
| SMB (Windows install media) | containerized, host network | evaluate: may work rootless via pasta/slirp4netns if ports 139/445 don't require true host-NIC binding; otherwise host-level like DHCP |
| dnsmasq reload sidecar | containerized, mounts Docker socket | **N/A** — host service reloaded via systemd/sudo-scoped helper, not a socket-mounted sidecar |

Rootless containers handle everything that doesn't need host root or
host-NIC control. The two hard-blocked services move out of containers
entirely rather than being forced rootless.

### DHCP/TFTP

Runs as a normal root systemd service on the host (e.g. `dnsmasq.service`,
config-managed the same way Beacon's web app currently regenerates
`dnsmasq.conf` — just writing to a host path and triggering a systemd
reload instead of restarting a container). The rootless web container
needs a narrow, explicit way to trigger that reload without holding host
root itself — likely a small privileged helper invoked via a tightly
scoped sudoers rule or a systemd socket-activated unit, not a general
Docker/Podman-socket mount. This needs its own write-up before
implementation.

### NFS replacement

Options to evaluate, not yet decided:

1. **HTTP boot instead of NFS root** — many distros support HTTP as a
   live-root/boot source; would drop the NFS dependency entirely for
   those cases.
2. **Userspace NFS (nfs-ganesha)** — can run without the kernel module,
   potentially rootless or with far fewer capabilities than kernel NFS.
3. **Overlay/squashfs served over HTTP** — reframes "live filesystem
   over NFS" as "boot image over HTTP + overlay", avoiding NFS as a
   protocol altogether.

Whichever is chosen has to work for every OS family Beacon currently
supports (Linux live boot, Windows via WinPE/SMB, XCP-NG) — this is
probably the single biggest open question for the project and likely
determines how much of the client-boot experience can stay identical.

## What carries over from Beacon

The parts of Beacon's `web/app` that don't require root:

- Image handling / ISO processing (`services/images.py`)
- Boot menu / iPXE generation (`services/ipxe.py`)
- Auth, users, settings, 2FA, API tokens (`routers/*`, `services/totp.py`)
- SQLite DB + backup/restore
- Dashboard/metrics reads (adapted — Beacon reads host `/proc` via a
  bind mount for whole-machine metrics; revisit under rootless)
- Driver staging for Windows (Intel VMD/RST, NIC drivers)

These can plausibly carry over close to as-is in shape and behavior.
What has to change is anything that currently assumes it's talking to a
sibling container over the Docker socket, or to `dnsmasq.conf` inside a
shared container volume — those become calls to a host-level service
instead.

## Explicitly out of scope for this doc

- Actual implementation of any service.
- Final choice between the NFS-replacement options above.
- Design of the privileged-reload helper for DHCP config changes.
- Whether SMB can stay containerized or has to move to the host like
  DHCP — needs testing against real rootless Podman network modes
  (pasta vs. slirp4netns vs. host) before deciding.

## Open questions

- Can pasta (Podman's default rootless network backend) bind low ports
  (69, 139, 445, 2049) in a way that's actually reachable from the real
  boot LAN, or does everything below 1024 force a host-level service
  the same way DHCP does?
- Does the reload-helper approach (sudoers-scoped or socket-activated)
  hold up security-wise, or is it just root access with extra steps?
- XCP-NG and Windows both currently lean on NFS/SMB semantics pretty
  directly — how much of their boot flow breaks if NFS goes away
  entirely in favor of HTTP+overlay?
