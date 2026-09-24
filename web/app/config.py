"""Environment-driven paths and defaults.

All mutable runtime settings (DHCP mode, theme, service toggles, ...) live in the
database (see models.Setting). This module only holds process-level config read
from the environment at startup.
"""
import os
import secrets
from pathlib import Path


def _path(env: str, default: str) -> Path:
    p = Path(os.environ.get(env, default))
    p.mkdir(parents=True, exist_ok=True)
    return p


# Mounted volumes (see compose.yml).
DATA_DIR = _path("DATA_DIR", "./data")
BOOTROOT_DIR = _path("BOOTROOT_DIR", "./bootroot")
TFTP_DIR = _path("TFTP_DIR", "./tftp")
IMAGE_DIR = _path("IMAGE_DIR", "./data/images")
DNSMASQ_DIR = _path("DNSMASQ_DIR", "./dnsmasq")
# Live filesystems extracted from ISOs. Whether these end up served over NFS,
# nfs-ganesha, or HTTP+overlay is still open (see docs/design.md's "NFS
# replacement" section) — extraction just stages the files locally either way.
NFS_DIR = _path("NFS_DIR", "./data/nfs")
# Unpacked Windows install media. Served over SMB so WinPE can run setup.exe
# from it (iPXE can't sanhook the ISO under UEFI); whether SMB stays
# containerized or moves to the host like DHCP is also open (docs/design.md).
SMB_DIR = _path("SMB_DIR", "./data/smb")
# Drop-in Windows storage drivers (Intel VMD/RST, AMD RAID). Sits inside SMB_DIR
# so a share exporting it re-exposes it as install\drivers, but it is a separate
# host bind (./data/drivers in compose.yml) rather than part of the smbroot
# volume — so re-extracting an image never clears it. See services.drivers.
DRIVERS_DIR = _path("DRIVERS_DIR", str(SMB_DIR / "drivers"))
# Windows Setup logs dropped by WinPE after a failed install (see
# services.images._capture_lines). A host bind under DATA_DIR, so no extra
# compose mount is needed for the web service. Only populated when
# ENABLE_DIAG_CAPTURE=true.
CAPTURE_DIR = _path("CAPTURE_DIR", str(DATA_DIR / "capture"))
# Boot-critical NIC drivers, BAKED INTO each Windows image's boot.wim rather
# than served over SMB — a driver WinPE needs to reach the network in the first
# place can't come over the network. Lives under DATA_DIR (already a host bind),
# so no compose change is needed. Changing its contents re-patches every ready
# Windows image's boot.wim (services.images.rebuild_windows_setup_all).
NICDRIVERS_DIR = _path("NICDRIVERS_DIR", str(DATA_DIR / "nicdrivers"))

DB_PATH = DATA_DIR / "lantern.db"
DB_URL = f"sqlite:///{DB_PATH}"

# Initial admin (consumed once, on first start, by services.bootstrap).
ADMIN_USER = os.environ.get("ADMIN_USER", "admin").strip() or "admin"
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "").strip()

# Session cookie signing key. Generated if not supplied (sessions then reset on
# restart, which is acceptable for a single-node admin tool).
SECRET_KEY = os.environ.get("SECRET_KEY", "").strip() or secrets.token_hex(32)

# Mark the session cookie Secure (browser only sends it over HTTPS). Off by
# default so the trusted-LAN plain-HTTP flow keeps working; set SESSION_SECURE=
# true when Lantern is served through a TLS-terminating reverse proxy.
SESSION_SECURE = os.environ.get("SESSION_SECURE", "").strip().lower() in (
    "1", "true", "yes", "on")


def _parse_trusted_proxies(raw: str) -> list[str]:
    """Split a comma/space-separated list of proxy IPs or CIDRs; ignore junk."""
    import ipaddress
    out: list[str] = []
    for tok in raw.replace(",", " ").split():
        try:
            ipaddress.ip_network(tok, strict=False)
        except ValueError:
            continue
        out.append(tok)
    return out


# Reverse-proxy hops whose X-Forwarded-For we trust to name the real client.
# Empty (default) means "no proxy": the socket peer is the client. Set this to
# the proxy's IP/CIDR when Lantern sits behind one, so the login throttle keys on
# the actual client instead of the proxy (otherwise one address — the proxy —
# owns every request and a handful of failures locks out everyone).
TRUSTED_PROXIES = _parse_trusted_proxies(os.environ.get("TRUSTED_PROXIES", ""))

WEB_PORT = int(os.environ.get("WEB_PORT", "8080"))

# Build provenance, surfaced in the UI so an admin can tell what is actually
# deployed. "dev" means a local build rather than a published image.
LANTERN_VERSION = os.environ.get("LANTERN_VERSION", "dev").strip() or "dev"
LANTERN_COMMIT = os.environ.get("LANTERN_COMMIT", "").strip()

# Defaults for first-run / settings, sourced from env so .env can seed them.
DEFAULTS = {
    "server_ip": os.environ.get("SERVER_IP", "").strip(),
    "boot_interface": os.environ.get("BOOT_INTERFACE", "eth0").strip(),
    "dhcp_mode": os.environ.get("DHCP_MODE", "proxy").strip(),
    # Full-DHCP-only fields: left empty by default so they only carry values the
    # admin explicitly sets (in Full DHCP mode). .env can still seed them.
    "dhcp_range_start": os.environ.get("DHCP_RANGE_START", "").strip(),
    "dhcp_range_end": os.environ.get("DHCP_RANGE_END", "").strip(),
    "dhcp_subnet_mask": os.environ.get("DHCP_SUBNET_MASK", "").strip(),
    "dhcp_gateway": os.environ.get("DHCP_GATEWAY", "").strip(),
    "dhcp_dns": os.environ.get("DHCP_DNS", "").strip(),
    # Service toggles.
    "svc_dhcp": "1",
    "svc_tftp": "1",
    "svc_http": "1",
    # UI.
    "theme": "dark",
    "menu_title": "Lantern",
    # Boot menu: timeout in seconds applied only when a default image is set.
    # 0 means wait indefinitely (no timeout).
    "boot_timeout": "30",
    # First-run wizard completion flag.
    "setup_complete": "0",
}
