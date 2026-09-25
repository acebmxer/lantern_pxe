# Design scope

Working notes from the decision to build this as a separate project
rather than a Podman port of [Beacon](https://github.com/acebmxer/beacon_pxe).
This is the topology to build against. The web layer, the host-level
DHCP/TFTP service, and the containerized HTTP boot root are built; the NFS
replacement and SMB placement are still open.

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
| dnsmasq reload sidecar | containerized, mounts Docker socket | **N/A** — a systemd path unit on the host notices the config change, validates it and restarts dnsmasq; nothing is mounted into the container |

Rootless containers handle everything that doesn't need host root or
host-NIC control. The two hard-blocked services move out of containers
entirely rather than being forced rootless.

### DHCP/TFTP

Decided and built (`host/`). dnsmasq runs as a root systemd service on the
host (`lantern-dnsmasq.service`). The rootless web container still generates
`dnsmasq.conf`, as Beacon's does. The difference is how that change reaches
dnsmasq:

- **The container's only interface is writing files.** It writes
  `data/dnsmasq/dnsmasq.conf` and stages the iPXE binaries in `data/tftp/`.
  It has no socket, no sudo rule and no command it can run on the host.
- **A systemd path unit (`lantern-apply.path`) watches those files** and
  starts `lantern-host apply` as root. We chose this over a socket-activated
  unit, which would have given faster feedback to the UI but a live channel
  from the container to a root process. We also chose it over a sudoers rule,
  which a rootless container can't reach anyway.
- **`apply` treats everything under `data/` as hostile.** It opens files one
  component at a time without following symlinks, reads only regular files,
  caps sizes, and writes through temp-file-and-rename. It accepts only the
  dnsmasq directives the web app actually emits (`ALLOWED_DIRECTIVES` in
  `host/lantern_host.py`). Anything naming a file or running a program
  (`dhcp-script`, `conf-file`, `log-facility`, `tftp-root`, ...) is refused.
  That list is the answer to "is this just root access with extra steps":
  the container can change DHCP behaviour, not host files or code. The host
  adds `tftp-root` itself, runs `dnsmasq --test` on the result, installs it
  as `/etc/lantern/dnsmasq.conf` and restarts dnsmasq. It then writes the
  outcome to `data/dnsmasq/apply-status.json`, which the Settings page shows.
- **Host-side paths are chosen for SELinux.** The TFTP root is
  `/var/lib/tftpboot/lantern` (labelled `tftpdir_rw_t`, which `dnsmasq_t`
  can read). No file type is both writable by `dnsmasq_t` and readable by
  `container_t`, so dnsmasq logs to the journal. `lantern-dnsmasq-log.service`
  then appends those lines to `data/dnsmasq/dnsmasq.log` for the dashboard's
  recent-clients view, using the same no-symlink rules.

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
- Whether SMB can stay containerized or has to move to the host like
  DHCP — needs testing against real rootless Podman network modes
  (pasta vs. slirp4netns vs. host) before deciding.

## Open questions

- Can pasta (Podman's default rootless network backend) bind low ports
  (69, 139, 445, 2049) in a way that's actually reachable from the real
  boot LAN, or does everything below 1024 force a host-level service
  the same way DHCP does?
- XCP-NG and Windows both currently lean on NFS/SMB semantics pretty
  directly — how much of their boot flow breaks if NFS goes away
  entirely in favor of HTTP+overlay?
