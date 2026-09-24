# Lantern PXE

[![Latest release](https://img.shields.io/github/v/release/acebmxer/lantern_pxe)](https://github.com/acebmxer/lantern_pxe/releases/latest)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A self-hosted PXE/iPXE boot server with a login-protected web console —
same goal and user-facing experience as [Beacon](https://github.com/acebmxer/beacon_pxe),
built around Podman's rootless model instead of Docker's root-daemon one.

**Status: stage 1 — the web management layer is built and runs standalone.**
The DHCP/TFTP, boot-root, and driver-serving pieces of the topology below
aren't built yet. See [docs/design.md](docs/design.md) for the service
topology and what's being reused vs. rebuilt.

## Running the web layer

```
cp .env.example .env   # edit ADMIN_PASSWORD, SERVER_IP, etc.
podman compose up -d --build   # or: docker compose up -d --build
```

Brings up the management console at `http://localhost:8080` (auth, users,
settings, image upload/processing, driver staging). Nothing serves DHCP,
TFTP, or boot files to real PXE clients yet — see Status above.

## Why a separate project instead of a Podman port

Beacon can't run rootless as-is: two of its services need real host root
regardless of container runtime —

- **NFS** boot roots require loading the host's in-kernel `nfsd` module
  (root-only, no rootless equivalent).
- **DHCP/TFTP** on the physical boot LAN needs real control of the host's
  NIC; `NET_ADMIN` inside a rootless user namespace doesn't reach the
  actual host network hardware.

Running those rootful would just be Docker's model with a different
daemon — not what choosing Podman is for. So this isn't a port: it's a
different service topology (host-level DHCP, a non-kernel-NFS or
non-NFS boot path) reusing Beacon's UI/workflow and whatever app logic
doesn't require root.

## Goal

Indistinguishable from Beacon to the end user — same web UI, same PXE
boot menu, same image management, same DHCP/TFTP/boot behavior from the
client's side. Free choice of implementation underneath to fit Podman's
rootless model.
