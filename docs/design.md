# Design scope

Working notes from the decision to build this as a separate project
rather than a Podman port of [Beacon](https://github.com/acebmxer/beacon_pxe).
This is the topology to build against. The web layer, the host-level
DHCP/TFTP service, the containerized HTTP boot root, the NFS replacement, and
SMB placement are all decided and built (below).

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
| NFS boot roots | containerized, `privileged: true`, host network | **replaced** — plain HTTP, see below |
| SMB (Windows install media) | containerized, host network | **decided** — containerized, rootless via pasta, see below |
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

Decided: **HTTP boot instead of NFS root**, option 1 of the three originally
considered (userspace NFS via nfs-ganesha, and overlay/squashfs-over-HTTP,
were not pursued once this worked). No NFS server exists anywhere in this
project's topology, containerized or host-level — kernel NFS is root-only
with no rootless path (see Constraint above), so it was never going to be an
option for a project built specifically to avoid needing root for this class
of service.

Every non-Windows, non-XCP-NG family now nets boot purely over the existing
rootless `httpboot` nginx container (`services/images.py::_netboot_plan`):

- **Fedora/Arch-family live images** already streamed their squashfs over
  HTTP; unchanged.
- **Debian** (live-boot) uses live-boot's own `fetch=URL` boot parameter,
  which downloads just `filesystem.squashfs` into a tmpfs — verified against
  live-boot(7). No NFS, no whole-ISO download.
- **Ubuntu** (casper) uses casper's own `url=`/`netboot=url` method instead,
  which downloads and loopback-mounts the *whole ISO*. Casper has no shipped
  equivalent to live-boot's squashfs-only `fetch=` — that exists only as an
  unmerged community patch (Launchpad #1660206) — so Ubuntu clients need more
  RAM for the same content than Debian clients do. If that turns out to be a
  real problem, the fix is either the LP #1660206 patch baked into a custom
  casper initrd, or nfs-ganesha for this one family specifically — not
  revisited generally, since HTTP already works everywhere else.
- Both methods copy the whole file into client RAM before mounting it, so
  low-RAM clients are the risk to watch for, the same tradeoff the old
  `url=`/`fetch=` methods were originally dropped for. Chosen anyway here
  because staying rootless was the higher priority.

### SMB

Decided: **rootless container via pasta**, the first option in the open
question this section used to pose. `smbd` binds port 445 directly (it can,
inside its own container/user namespace — see `smb/Containerfile`'s top
comment for why that's still rootless from the host's side), and pasta
forwards real connections to it correctly, the same mechanism already proven
for `httpboot`'s port 80 — confirmed both with `smbclient` against the
published port and with a real Windows PXE client reaching the share and
running Setup. Host-level Samba (the DHCP/TFTP pattern) was never needed.

The `smb` container (`smb/`) exports one guest-only, unauthenticated share,
`[install]`, matching the `net use ... /user:guest ""` WinPE runs
(`services/images.py::_lantern_setup_cmd`) — no domain, no real accounts, no
winbind. A second share, `[capture]`, is appended by `smb/entrypoint.sh` only
when `ENABLE_DIAG_CAPTURE=true` (`.env.example`), matching the opt-in,
unauthenticated-writable-share design that setting already implied before
anything actually served it.

One real bug surfaced building this, worth recording since it's a Samba trap
rather than anything rootless-specific: `force user`/`force group` looked
like the obvious way to pin every guest file operation to the uid the web app
and its volumes already use (1000). Combined with `map to guest = bad user`,
it instead left smbd's own POSIX ACL check (`check_parent_access_fsp`)
denying writes to a directory the mapped uid plainly owned 0755 on —
some interaction between force-mapping and an already-guest-mapped session
that smbd doesn't handle cleanly. `guest account = lantern` alone (no
`force user`/`force group`) achieves the same uid pinning and doesn't hit
this; that's what `smb/smb.conf` uses.

A second, unrelated bug is what actually made "Windows won't boot" look like
a VM crash: `_extract_tree_7z` (the Windows ISO unpack into `SMB_DIR`) left
every extracted file at the plain `644` 7z gives them, with no Unix execute
bit. `net use`, `xcopy`-ing drop-in drivers, and reading files over the share
all worked fine — those only need `FILE_READ_DATA`. Actually *launching*
`setup.exe` over SMB needs the `FILE_EXECUTE` right, which Samba's POSIX
permission check ties to the Unix execute bit, so it failed instantly
("Access is denied", errorlevel 5) before showing any UI. WinPE's own default
behavior when its shell (`lantern-setup.cmd`, via `winpeshl.ini`) exits is to
reboot the machine — so a script failing in under a second looked, on
screen, exactly like the VM crashing partway through boot. Found by adding a
progress log that `lantern-setup.cmd` now always writes to the (opt-in)
`[capture]` share on every step, specifically because it survives a VM reset
even when nothing else does. Fixed by `chmod -R a+rwx` on the whole extracted
tree right after unpacking — the SMB share's own `read only = yes` is what
actually enforces read-only for guests, not the Unix write bit, so this
doesn't loosen anything a guest can do over the wire.

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
