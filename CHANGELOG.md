# Changelog

All notable changes to Lantern are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Entries describe user-facing changes. Internal refactors that change nothing
observable are omitted.

## [Unreleased]

### Added

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
  Two pieces are stubbed pending those open questions: `dnsmasq.conf`
  generation has no reload trigger yet (`services.dnsmasq.trigger_reload()`),
  and database restore has no self-restart mechanism yet
  (`services.restore.can_restart()`) — both need the same kind of
  narrow-privilege design docs/design.md's "DHCP/TFTP" section calls for.
  Beacon's Docker-socket self-update feature was deliberately not carried
  over, since it isn't in that "carries over" list and the socket-mounted
  sidecar pattern is exactly what moving to Podman is meant to avoid.
