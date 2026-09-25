# Lantern PXE

A self-hosted PXE/iPXE boot server with a web console — same goal and
user-facing behavior as [Beacon](https://github.com/acebmxer/beacon_pxe)
(web UI, PXE boot menu, image management, DHCP/TFTP/NFS boot flow), but
built around Podman's rootless model instead of Docker's root daemon.

Read [docs/design.md](docs/design.md) before doing anything — it has the
full rationale (why this is a new project, not a Podman port of Beacon)
and the target service topology. Don't ask Nick to re-explain any of that;
it's all there. [docs/design.md](docs/design.md) is the source of truth;
this file is just the entry point to it.

## Status

Stage 1 done: the FastAPI web layer (`web/`) is built and runs standalone in
a rootless container — auth, users/2FA, settings, image upload/ISO
processing, iPXE menu generation, SQLite DB + backup/restore, driver
staging. `podman compose up -d --build` (or `docker compose`) brings up just
that service; see [compose.yml](compose.yml) and
[.env.example](.env.example).

Stage 2 built: the host-level DHCP/TFTP service (`host/`). dnsmasq runs as a
root systemd unit on the host, and a systemd path unit applies the web app's
`dnsmasq.conf` whenever it changes, checking it against an allowlist first.
Installed with `sudo host/install.sh <data dir>`. The design is in
[docs/design.md](docs/design.md)'s "DHCP/TFTP" section.

Stage 3 built: the HTTP boot root (`httpboot/`), a containerized, rootless
nginx serving `BOOTROOT_DIR` (boot.ipxe, kernels/initrds, squashfs, wimboot,
the XCP-NG GRUB chainload) and reverse-proxying `/track/` to the web app.
Uses `nginxinc/nginx-unprivileged` running as uid 1000, the same uid the web
app writes as — the plain `nginx` image's usual root-then-drop-privileges
pattern needs `CAP_NET_BIND_SERVICE` to bind port 80 itself, which rootless
Podman won't grant, and a fresh named volume shared by two containers with
different uids doesn't get auto-owned to either of them (verified: the web
app's own writes into a shared `bootroot` volume failed permission checks
until both containers ran as the same uid). The bootroot volume mount is
`:z` (shared SELinux label), not `:Z` (private) — verified `:Z` locks the
second container out under SELinux enforcing. The host still has to allow
binding port 80 as non-root (`net.ipv4.ip_unprivileged_port_start`); see
[README.md](README.md).

Not built yet, per the open questions in [docs/design.md](docs/design.md):
NFS (needed for Debian/Ubuntu live images' netboot root) and SMB placement
(needed for Windows install media once WinPE starts). Extraction stages
files under `NFS_DIR`/`SMB_DIR`, but nothing serves them yet.

Beacon's Docker-socket self-update feature was deliberately not ported — it
isn't in design.md's "what carries over" list and conflicts with the
rootless model. `services.restore.can_restart()` is similarly a stub: there's
no decided mechanism yet for a rootless container to restart itself, so a
database restore swaps the file but needs a manual restart to take effect.

## When told to "start" or "go"

That means: begin implementation. Don't ask what the project is or ask
for the background again — start with this, in order:

1. **Scaffold the rootless-safe web layer first.** The FastAPI app
   (auth, users, settings, image handling/ISO processing, boot-menu/iPXE
   generation, SQLite DB, driver staging) doesn't depend on any of the
   open topology questions below and can be built and run standalone,
   in a rootless container, reusing Beacon's app shape per the "What
   carries over from Beacon" section of the design doc.
2. **Stub the parts that depend on unresolved architecture** (DHCP
   config generation/reload, the NFS/HTTP boot-root choice, SMB
   placement) behind clear interfaces rather than blocking on them —
   note them as TODOs tied to the open questions in the design doc.
3. **Surface the open questions from design.md as they become blocking**,
   not all at once up front. If reaching a real fork (e.g. actually
   wiring DHCP reload, or picking the NFS replacement) requires a
   decision, ask about that one decision when you get there — don't
   front-load the whole open-questions list as a precondition to
   starting.

If something in step 1 turns out to already need a topology decision
sooner than expected, say so in one line and ask — don't stall silently
and don't guess on anything hard to undo (on-disk layout, the DB schema,
public interfaces).
