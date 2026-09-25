# Lantern PXE

[![Latest release](https://img.shields.io/github/v/release/acebmxer/lantern_pxe)](https://github.com/acebmxer/lantern_pxe/releases/latest)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A self-hosted PXE/iPXE boot server with a login-protected web console —
same goal and user-facing experience as [Beacon](https://github.com/acebmxer/beacon_pxe),
built around Podman's rootless model instead of Docker's root-daemon one.

**Status: stage 3.** The web management layer, the host-level DHCP/TFTP
service, and the containerized HTTP boot root are built, so PXE clients can
load iPXE, fetch the boot menu, and boot anything the menu serves purely over
HTTP (Fedora/Arch-family live images, XCP-NG via its GRUB chainload, and
WinPE for Windows). The NFS and SMB boot paths still aren't built: Debian/
Ubuntu live images (which netboot over NFS) won't find a root filesystem, and
a Windows install can't reach `install.wim` (served over SMB) once WinPE
starts. See [docs/design.md](docs/design.md) for the service topology and
what's being reused vs. rebuilt.

## Running it

### 1. Web layer + HTTP boot root (rootless containers)

Rootless containers can't bind a port below 1024 by default, and PXE clients
reach the boot root on plain HTTP with no port in the URL, so port 80 has to
be free for the `httpboot` container to bind on the host side. Either allow
rootless port binding down to 80:

```
sudo sysctl -w net.ipv4.ip_unprivileged_port_start=80    # or add to /etc/sysctl.d
```

or edit the `"80:8080"` line under the `httpboot` service in `compose.yml` to
a port your setup can bind, and change every `http://<server_ip>` the iPXE
menu assumes back to `http://<server_ip>:<port>` (`SERVER_IP` in `.env` stays
just the IP; there's no separate port setting yet).

```
cp .env.example .env   # edit ADMIN_PASSWORD, SERVER_IP, etc.
podman compose up -d --build   # or: docker compose up -d --build
```

Brings up the management console at `http://localhost:8080` (auth, users,
settings, image upload/processing, driver staging) and the HTTP boot root
PXE clients fetch `boot.ipxe`, kernels/initrds and squashfs images from, on
port 80.

### 2. DHCP/TFTP (host service)

DHCP has to answer on the physical boot LAN, which a rootless container
can't reach, so dnsmasq runs on the host as a systemd service. Install
dnsmasq with your package manager (`dnf install dnsmasq` /
`apt install dnsmasq`). Then, once the web container has started once:

```
sudo host/install.sh "$PWD/data"
```

After that, saving Server Settings in the web UI is enough. The host
checks the generated config against an allowlist, applies it and restarts
dnsmasq, and the result appears under **Services** on the Settings page.
The web container gets nothing on the host that it can call. The host only
reads the files the container writes. To remove the service:
`sudo host/install.sh --uninstall`.

If a firewall is running, allow UDP 67 (DHCP), 69 (TFTP) and 4011
(proxyDHCP) on the boot interface. If the distribution's own
`dnsmasq.service` serves DHCP on that interface, disable it.

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
