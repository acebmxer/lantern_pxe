#!/usr/bin/env bash
# Install (or remove) Lantern's host-level DHCP/TFTP service.
#
#   sudo host/install.sh /path/to/lantern_pxe/data     install / update
#   sudo host/install.sh --uninstall                    remove
#
# The data directory is the one compose.yml mounts into the web container
# (./data by default). Run this after the web container has started once, so
# the directory exists.
#
# What it installs (see docs/design.md's "DHCP/TFTP" section for why):
#   /usr/local/libexec/lantern/lantern-host   validator / log forwarder
#   /etc/systemd/system/lantern-dnsmasq.service      dnsmasq on the host
#   /etc/systemd/system/lantern-apply.{path,service} apply config on change
#   /etc/systemd/system/lantern-dnsmasq-log.service  DHCP log for the dashboard
#   /etc/lantern/                             validated dnsmasq.conf
#   /var/lib/tftpboot/lantern/                TFTP root (iPXE binaries)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIBEXEC=/usr/local/libexec/lantern
UNIT_DIR=/etc/systemd/system
UNITS=(lantern-dnsmasq.service lantern-apply.path lantern-apply.service
       lantern-dnsmasq-log.service)

die() { echo "error: $*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "run as root (sudo $0 ...)"

if [[ "${1:-}" == "--uninstall" ]]; then
    systemctl disable --now "${UNITS[@]}" 2>/dev/null || true
    for u in "${UNITS[@]}"; do rm -f "$UNIT_DIR/$u"; done
    systemctl daemon-reload
    rm -rf "$LIBEXEC" /etc/lantern /var/lib/tftpboot/lantern
    echo "Lantern host DHCP/TFTP service removed."
    exit 0
fi

[[ $# -eq 1 ]] || die "usage: $0 /path/to/lantern_pxe/data | --uninstall"
DATA_DIR="$(realpath -e "$1" 2>/dev/null)" \
    || die "$1 does not exist — start the web container once first"
[[ -d "$DATA_DIR" ]] || die "$DATA_DIR is not a directory"
# The path is written literally into systemd unit files.
[[ "$DATA_DIR" =~ ^[A-Za-z0-9._/+-]+$ ]] \
    || die "data directory path may only contain letters, digits and ._/+-"

DNSMASQ="$(command -v dnsmasq || true)"
[[ -n "$DNSMASQ" ]] || die "dnsmasq is not installed (dnf install dnsmasq / apt install dnsmasq)"
command -v python3 >/dev/null || die "python3 is not installed"

if systemctl is-active --quiet dnsmasq.service; then
    echo "warning: the distribution's own dnsmasq.service is running. If it" >&2
    echo "         serves DHCP on the boot interface it will conflict with Lantern." >&2
fi

install -d -m 0755 "$LIBEXEC" /etc/lantern /var/lib/tftpboot/lantern
install -m 0755 "$HERE/lantern_host.py" "$LIBEXEC/lantern-host"
for u in "${UNITS[@]}"; do
    sed -e "s|@DATA_DIR@|$DATA_DIR|g" -e "s|@DNSMASQ@|$DNSMASQ|g" \
        "$HERE/systemd/$u" > "$UNIT_DIR/$u"
    chmod 0644 "$UNIT_DIR/$u"
done
# SELinux: give the new files their policy labels (the TFTP root becomes
# tftpdir_rw_t, which dnsmasq is allowed to read).
if command -v restorecon >/dev/null; then
    restorecon -R "$LIBEXEC" /etc/lantern /var/lib/tftpboot "${UNITS[@]/#/$UNIT_DIR/}"
fi

systemctl daemon-reload
systemctl enable lantern-dnsmasq.service
systemctl enable --now lantern-apply.path lantern-dnsmasq-log.service
# On a re-install, the forwarder is still running the previous lantern-host;
# enable --now leaves a running service alone, so restart it explicitly.
systemctl restart lantern-dnsmasq-log.service
# Apply whatever the web app has already written; this also starts dnsmasq.
systemctl start lantern-apply.service || true

echo
echo "Installed. Last apply result:"
cat "$DATA_DIR/dnsmasq/apply-status.json" 2>/dev/null || echo "  (none yet)"
echo
echo "If a firewall is running, allow UDP 67 (DHCP), 69 (TFTP) and 4011"
echo "(proxyDHCP) on the boot interface, e.g. with firewalld:"
# firewall-cmd refuses --add-service and --add-port in the same call.
echo "  firewall-cmd --permanent --add-service=dhcp --add-service=tftp"
echo "  firewall-cmd --permanent --add-port=4011/udp"
echo "  firewall-cmd --reload"
