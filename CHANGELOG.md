# Changelog

All notable changes to Lantern are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Entries describe user-facing changes. Internal refactors that change nothing
observable are omitted.

## [Unreleased]

### Added

- HTTP boot root (`httpboot/`): a containerized, rootless nginx that serves
  `BOOTROOT_DIR` (boot.ipxe, extracted kernels/initrds, squashfs images,
  wimboot, the XCP-NG GRUB chainload) to PXE clients and reverse-proxies
  `/track/` to the web app, so the iPXE menu only needs the one `${boot-url}`
  it already generated. This unblocks the boot flow for every image family
  that only needs HTTP: Fedora/Arch-family live images, XCP-NG via its GRUB
  chainload, and WinPE for Windows (the Windows install itself still needs
  the not-yet-built SMB share once WinPE starts, and Debian/Ubuntu live
  images still need NFS). Uses `nginxinc/nginx-unprivileged` running as the
  same uid the web app writes as (1000), not the ordinary `nginx` image:
  nginx's usual root-binds-80-then-drops-to-a-user pattern needs
  `CAP_NET_BIND_SERVICE`, which rootless Podman won't grant, and testing
  found that a fresh named volume shared between two containers running as
  different uids isn't auto-owned to either of them — the web app's writes
  into the shared `bootroot` volume got a permission error until both
  containers ran as uid 1000. The volume is mounted `:z` (shared SELinux
  label) rather than `:Z` (private); testing under SELinux enforcing showed
  `:Z` gives each container its own MCS category and locks the other one out
  of files the other wrote. The host still needs to allow a rootless
  container to bind port 80 (`net.ipv4.ip_unprivileged_port_start`), since
  PXE clients reach the boot root on plain HTTP with no port in the URL; see
  the new step in [README.md](README.md).
  While testing a real PXE boot end to end, found and fixed a real bug in
  `httpboot/nginx.conf`'s `/track/` proxy: `proxy_pass http://web:8080/...`
  uses a literal hostname, which nginx resolves once at worker startup and
  caches for the worker's lifetime. Any time the `web` container is
  recreated with a new IP (a rebuild, a restart), every `/track/` request
  failed with `502`/`Host is unreachable` until `httpboot` itself was
  restarted too. `nginx.conf` now proxies through a variable
  (`set $web_upstream ...; proxy_pass $web_upstream/track/;`), which forces
  nginx to re-resolve on every request, using a `resolver` directive pointed
  at the container's own DNS. Since that resolver address differs per
  Docker/Podman network, a new `docker-entrypoint.d/40-resolver.sh` (mounted
  in alongside `nginx.conf`) reads it from `/etc/resolv.conf` at container
  start and writes it to `/tmp/resolver.conf`, which `nginx.conf` includes,
  instead of hardcoding one network's address.
- Host-level DHCP/TFTP service (`host/`), installed with
  `sudo host/install.sh <data dir>` (re-run it to update an existing install)
  and removed with `--uninstall`; the
  installer prints the firewalld commands for the ports it needs. dnsmasq
  runs as a root systemd service on the host, because a rootless container
  can't answer DHCP on the physical boot LAN. The web container's only
  interface to it is the `dnsmasq.conf` it already generated. A systemd path
  unit notices each change and runs `lantern-host apply`. That accepts only
  the dnsmasq directives the web app emits, refusing anything that names a
  file or runs a program, and installs the config after `dnsmasq --test`
  passes. It then restarts dnsmasq and reports the result under **Services**
  on the Settings page. The container has no socket, sudo rule or command on
  the host. The web image now builds the iPXE binaries, using Beacon's pinned,
  USB-keyboard-enabled build, and stages them for the host's TFTP root
  (`/var/lib/tftpboot/lantern`). dnsmasq logs to the journal, and a forwarder
  service copies the log, with UTC timestamps as the dashboard expects, into
  `data/dnsmasq/dnsmasq.log` for the dashboard's recent-clients view. In `compose.yml`, the dnsmasq config and TFTP staging
  moved from the `dnsmasqconf` named volume into `./data` so the host can
  read them. The design is in [docs/design.md](docs/design.md)'s
  "DHCP/TFTP" section.
- Web management layer (`web/`): a FastAPI app providing authentication with
  optional TOTP two-factor, user accounts, server settings, OS image upload
  and ISO processing (Linux kernel/initrd extraction, Windows WinPE/wimboot
  staging, XCP-NG Xen multiboot via a generated UEFI GRUB chainloader), iPXE
  boot-menu generation, a live dashboard (host metrics, recent PXE clients,
  deployment stats), Windows driver staging (including fetch-on-demand from
  a curated, checksum-verified catalog), and SQLite-backed backup/restore.
  Built to run standalone in a rootless Podman container
  (`web/Containerfile`, `compose.yml`) — reuses Beacon's app shape per the
  "What carries over from Beacon" section of [docs/design.md](docs/design.md),
  since none of it depends on the open DHCP/NFS/SMB topology questions.
  Database restore has no self-restart mechanism yet
  (`services.restore.can_restart()`); it needs the same kind of
  narrow-privilege design as the DHCP/TFTP service above.
  Beacon's Docker-socket self-update feature was deliberately not carried
  over, since it isn't in that "carries over" list and the socket-mounted
  sidecar pattern is exactly what moving to Podman is meant to avoid.
  While testing the HTTP boot root above end to end, found and fixed a real
  bug in `process_image()`: only `subprocess.CalledProcessError` from the
  extraction steps was caught, so an unrelated failure (reproduced here with
  a `PermissionError` from a misconfigured `NFS_DIR` mount) propagated out of
  the background task uncaught, leaving the image stuck on `processing`
  forever with no message and, unlike `error`/`pending`/`needs_reprocess`, no
  Retry button in the UI (`templates/images.html`) to get it out of that
  state without deleting and re-uploading. `process_image()` now wraps the
  whole extraction in a catch-all that marks the image `error` with a message
  on any unexpected exception, not just the specific ones already handled.
  Also, while testing a real PXE boot of a Fedora KDE live image on a 4 GB
  XCP-ng VM, the console sat on a solid black screen for several minutes
  with no way to tell a slow boot from a hung one. That turned out to be the
  VM simply not having enough RAM (a 6 GB VM booted the same image to the
  desktop fine, using 4.7 GB once Plasma was up) — this class of image (the
  whole live root pulled into tmpfs over HTTP, like Archiso's `airootfs.sfs`
  above) needs meaningfully more client RAM than a from-media boot of the
  same ISO would. `plymouth.enable=0` and `nomodeset` were both tried on the
  kernel line as a way to see boot progress on the black screen instead of
  Plymouth's splash; neither turned out to be necessary once the VM had
  enough RAM, so `_netboot_plan()`'s kernel line for this image family is
  unchanged from Beacon's.
