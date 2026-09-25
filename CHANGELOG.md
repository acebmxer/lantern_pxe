# Changelog

All notable changes to Lantern are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Entries describe user-facing changes. Internal refactors that change nothing
observable are omitted.

## [Unreleased]

### Fixed

- Windows Setup wouldn't launch over the SMB install share, and it looked
  exactly like the VM itself crashing rather than a script failure.
  `_extract_tree_7z` (the ISO unpack into `SMB_DIR`) leaves every extracted
  file at the plain `644` 7z gives them, with no Unix execute bit. `net use`,
  `xcopy`-ing drop-in drivers, and plain reads over the share all worked fine
  — those only need `FILE_READ_DATA`. Actually launching `setup.exe` needs
  the SMB `FILE_EXECUTE` right, which Samba's POSIX permission check ties to
  the Unix execute bit, so it returned instantly ("Access is denied",
  errorlevel 5) before showing any UI. WinPE's own default behaviour when its
  shell (`lantern-setup.cmd`, launched via `winpeshl.ini`) exits is to reboot
  the machine, so a script failing in under a second looked, from the
  console, exactly like the VM resetting mid-boot. Found only after
  `lantern-setup.cmd` was given a progress log that writes to the (opt-in)
  `[capture]` share on every step, specifically because it survives a VM
  reset when nothing else does — the log showed Setup launching and
  returning in the same millisecond, with "Access is denied" as its own
  captured console output. Fixed with `chmod -R a+rwx` on the whole extracted
  tree right after unpacking; the SMB share's own `read only = yes` is what
  actually enforces read-only for guests, not the Unix write bit, so this
  doesn't loosen anything a guest can do over the wire.
- Debian and Ubuntu live images now actually boot over PXE. `_netboot_plan()`
  previously pointed them at `netboot=nfs nfsroot=...`, but no NFS server was
  ever built anywhere in this project — nothing wrote to or served the
  `nfsroot` volume — so every Debian/Ubuntu client hung with `connect:
  Connection refused` trying to mount a share that didn't exist. Both
  families now boot over the existing rootless `httpboot` HTTP root instead,
  with no NFS anywhere in the picture: kernel NFS needs root and was never
  going to be an option here (docs/design.md's Constraint section). Debian
  (live-boot) uses `fetch=URL` (verified against live-boot(7)), which
  downloads just `filesystem.squashfs` into a tmpfs. Ubuntu (casper) uses
  `url=`/`netboot=url` instead: casper has no shipped equivalent to
  live-boot's squashfs-only fetch — that exists only as an unmerged
  community patch (Launchpad #1660206) — so it downloads and loopback-mounts
  the whole ISO, needing more client RAM for the same content than Debian
  does. The now fully unused `NFS_DIR`/`nfsroot` volume, its `needs_nfs`
  extraction branch, and the iPXE `server-ip` variable it was the only
  consumer of have all been removed.
  While building this, found and fixed a second, pre-existing bug it would
  otherwise have inherited: `_netboot_plan()`'s `iso_url` (used by both
  Ubuntu's new `url=` and Fedora/RHEL's existing `inst.repo=`) points at
  `${boot-url}/images/<filename>`, but the `httpboot` container never had the
  images volume mounted, so that URL 404'd for every image that used it —
  Fedora/RHEL's `inst.repo=` install path was silently broken from the day it
  shipped. `httpboot` now mounts the images volume read-only and serves it at
  `/images/`.

### Added

- SMB share (`smb/`) for Windows install media: a containerized, rootless
  Samba server exporting a guest-only, unauthenticated `[install]` share that
  matches the `net use ... /user:guest ""` WinPE already runs
  (`services/images.py::_lantern_setup_cmd`) — no domain, no real accounts,
  no winbind. `smbd` runs as root *inside its own container/user namespace*
  (not the host's — see `smb/Containerfile`'s top comment for why that's
  still rootless from the host's side) so it can bind port 445 directly and
  `setuid()`/`setgroups()` to the guest account per connection; pasta forwards
  real connections to it the same way it already does for `httpboot`'s port
  80. A second share, `[capture]`, is appended by `smb/entrypoint.sh` only
  when `ENABLE_DIAG_CAPTURE=true` (`.env.example`) — previously a documented
  but entirely unenforced setting, since nothing served SMB at all to make
  "opt-in" mean anything.
  While building this, found and fixed a real bug in the approach, not the
  destination: `force user`/`force group` looked like the direct way to pin
  every guest file operation to the uid the web app and its volumes already
  use (1000), but combined with `map to guest = bad user` it left smbd's own
  POSIX ACL check (`check_parent_access_fsp`) denying writes to a directory
  the mapped uid plainly owned 0755 on — some interaction between the two
  mapping layers smbd doesn't handle cleanly. Verified with `log level = 10`
  that the denial was internal to smbd, not a kernel/SELinux permission
  problem (a plain shell as the same uid could write the same path fine).
  `guest account = lantern` alone pins the same uid without hitting this, and
  is what `smb/smb.conf` uses.
  A second bug, found from a real report rather than testing: the new `smb`
  container's `./data/drivers` and `./data/capture` mounts used `:Z`
  (private SELinux label), the same path the `web` container already mounts
  at `/smb/drivers` — two containers privately labelling the same host path
  is exactly the lockout `compose.yml`'s own `bootroot` comment already warns
  about, and it broke the Drivers page (`PermissionError: [Errno 13]
  Permission denied: '/smb/drivers'`) the moment `smb` started. Both
  containers' mounts of these two paths are now `:z` (shared), matching
  `bootroot`/`smbroot`/the images volume.
  The same lockout recurred a second way from a path that wasn't obvious
  until it happened twice: `web`'s own top-level `./data:/data:Z` mount
  recursively relabels the *whole* `./data` tree as `web`'s private category
  on every `web` restart, silently re-breaking `smb`'s separately-`:z`-labelled
  access to `data/drivers`/`data/capture` underneath it — verified by watching
  a diagnostic log `smb` should have been able to write suddenly go missing
  again right after an unrelated `web` rebuild. `web`'s `./data` mount is now
  `:z` too.
  Verified end to end with `smbclient` against the published port (guest
  read on `[install]`, including `setup.exe` and the drivers folder; guest
  write on `[capture]` once enabled; `[capture]` absent when the setting is
  off), and with a real Windows PXE client reaching the share and launching
  Setup.
- HTTP boot root (`httpboot/`): a containerized, rootless nginx that serves
  `BOOTROOT_DIR` (boot.ipxe, extracted kernels/initrds, squashfs images,
  wimboot, the XCP-NG GRUB chainload) to PXE clients and reverse-proxies
  `/track/` to the web app, so the iPXE menu only needs the one `${boot-url}`
  it already generated. This unblocks the boot flow for every image family
  that only needs HTTP: Fedora/Arch-family live images, XCP-NG via its GRUB
  chainload, and WinPE for Windows (the Windows install itself still needs
  the not-yet-built SMB share once WinPE starts). Uses
  `nginxinc/nginx-unprivileged` running as the same uid the web app writes
  as (1000), not the ordinary `nginx` image:
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
  a `PermissionError` from a misconfigured `SMB_DIR` mount) propagated out of
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
