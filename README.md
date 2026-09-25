# Lantern PXE

[![Latest release](https://img.shields.io/github/v/release/acebmxer/lantern_pxe)](https://github.com/acebmxer/lantern_pxe/releases/latest)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A self-hosted PXE/iPXE boot server with a login-protected web console —
same goal and user-facing experience as [Beacon](https://github.com/acebmxer/beacon_pxe),
built around Podman's rootless model instead of Docker's root-daemon one.

**Status: stage 4.** The web management layer, the host-level DHCP/TFTP
service, the containerized HTTP boot root, and the containerized Windows
install SMB share are all built. PXE clients can load iPXE, fetch the boot
menu, and boot every image family the menu serves: Fedora/Arch-family and
Debian/Ubuntu live images and XCP-NG via its GRUB chainload purely over HTTP,
and Windows via WinPE (wimboot/HTTP) followed by a guest-only SMB share for
the install media itself. See [docs/design.md](docs/design.md) for the
service topology and what's being reused vs. rebuilt.

## Running it

### 1. Web layer, HTTP boot root, and SMB share (rootless containers)

Rootless containers can't bind a port below 1024 by default. PXE clients
reach the HTTP boot root on plain HTTP with no port in the URL and WinPE
reaches the SMB share on the standard port 445, so both need to be free for
the `httpboot` and `smb` containers to bind on the host side. Either allow
rootless port binding down to 80 (this one setting covers 445 too, since it
lowers the floor, not just port 80 itself):

```
sudo sysctl -w net.ipv4.ip_unprivileged_port_start=80    # or add to /etc/sysctl.d
```

or edit the `"80:8080"` line under the `httpboot` service in `compose.yml` to
a port your setup can bind, and change every `http://<server_ip>` the iPXE
menu assumes back to `http://<server_ip>:<port>` (`SERVER_IP` in `.env` stays
just the IP; there's no separate port setting yet). The `smb` service's
`"445:445"` line has no equivalent workaround — WinPE connects to port 445
with no way to override it, so that one has to stay free.

```
cp .env.example .env   # edit ADMIN_PASSWORD, SERVER_IP, etc.
podman compose up -d --build   # or: docker compose up -d --build
```

Brings up the management console at `http://localhost:8080` (auth, users,
settings, image upload/processing, driver staging), the HTTP boot root PXE
clients fetch `boot.ipxe`, kernels/initrds and squashfs images from (port 80),
and the guest-only SMB share (port 445) WinPE mounts to run Windows Setup.
If a firewall is running, also allow TCP 445 on the boot interface for the
SMB share.

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
