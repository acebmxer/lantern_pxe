# Changelog

All notable changes to Lantern are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Entries describe user-facing changes. Internal refactors that change nothing
observable are omitted.

## [Unreleased]

### Added

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
