#!/usr/bin/env python3
"""Host-side helper for Lantern's DHCP/TFTP service. Runs as root, stdlib only.

The rootless web container never gets a way to call into this. It only writes
files under its data directory; systemd notices them (lantern-apply.path) and
runs `apply`, which decides for itself what to accept. See docs/design.md's
"DHCP/TFTP" section.

  apply   Validate <data>/dnsmasq/dnsmasq.conf against a directive allowlist,
          install it as /etc/lantern/dnsmasq.conf, copy the iPXE binaries from
          <data>/tftp into the TFTP root, restart lantern-dnsmasq if the config
          changed, and write the outcome to <data>/dnsmasq/apply-status.json for
          the web UI.
  logfwd  Follow lantern-dnsmasq's journal and append it to
          <data>/dnsmasq/dnsmasq.log, which the dashboard's recent-clients view
          parses. dnsmasq logs to the journal rather than straight into the data
          dir because SELinux gives dnsmasq and containers no file type that one
          can write and the other can read.

Everything under <data> is writable by the container, so it is treated as
hostile: files there are opened one path component at a time with O_NOFOLLOW
(a symlink can't point root at /etc/shadow), only regular files are read, reads
are size-capped, and writes go to a fresh temp file renamed into place (so a
planted symlink or hard link is replaced, never written through).
"""
from __future__ import annotations

import argparse
import json
import os
import stat
import subprocess
import sys
import time
from datetime import datetime, timezone

CONF_OUT = "/etc/lantern/dnsmasq.conf"
TFTP_ROOT = "/var/lib/tftpboot/lantern"
DNSMASQ_UNIT = "lantern-dnsmasq.service"

# The iPXE binaries the web image stages into <data>/tftp (see
# web/app/services/bootstrap.py). Nothing else is copied into the TFTP root.
TFTP_FILES = ("ipxe.efi", "undionly.kpxe")

MAX_CONF_BYTES = 64 * 1024
MAX_BINARY_BYTES = 8 * 1024 * 1024
# logfwd truncates dnsmasq.log past this; the dashboard only parses the tail.
MAX_LOG_BYTES = 16 * 1024 * 1024

# Every directive web/app/services/dnsmasq.py emits, and nothing else. Anything
# that names a file or runs a program (dhcp-script, conf-file, conf-dir,
# log-facility, tftp-root, addn-hosts, dhcp-leasefile, pid-file, user, ...) is
# absent on purpose: dnsmasq runs as root, so any of those would hand the
# container root file access or code execution. The host supplies tftp-root
# itself below.
ALLOWED_DIRECTIVES = frozenset({
    "port",
    "log-dhcp",
    "interface",
    "bind-interfaces",
    "except-interface",
    "dhcp-match",
    "dhcp-userclass",
    "enable-tftp",
    "pxe-prompt",
    "pxe-service",
    "dhcp-boot",
    "dhcp-range",
    "dhcp-option",
})


class ApplyError(Exception):
    """A problem to report to the web UI; the running config is left alone."""


# ---------------------------------------------------------------------------
# Safe access to the container-writable data directory
# ---------------------------------------------------------------------------

def _open_dir(data_dir: str, parts: tuple[str, ...]) -> int:
    """Open <data_dir>/<parts...> as a directory without following symlinks.

    data_dir itself comes from the installed unit file (root-owned), so only the
    components below it are container-controlled.
    """
    fd = os.open(data_dir, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in parts:
            nfd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                          dir_fd=fd)
            os.close(fd)
            fd = nfd
    except BaseException:
        os.close(fd)
        raise
    return fd


def read_data_file(data_dir: str, parts: tuple[str, ...], limit: int) -> bytes | None:
    """Read a regular file under data_dir. None if it doesn't exist."""
    try:
        dfd = _open_dir(data_dir, parts[:-1])
    except FileNotFoundError:
        return None
    try:
        # O_NONBLOCK so a FIFO planted in place of the file can't hang us.
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                     dir_fd=dfd)
    except FileNotFoundError:
        return None
    except OSError as e:
        raise ApplyError(f"cannot open {'/'.join(parts)}: {e.strerror}") from None
    finally:
        os.close(dfd)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise ApplyError(f"{'/'.join(parts)} is not a regular file")
        if st.st_size > limit:
            raise ApplyError(f"{'/'.join(parts)} is larger than {limit} bytes")
        with os.fdopen(fd, "rb", closefd=False) as f:
            data = f.read(limit + 1)
        if len(data) > limit:
            raise ApplyError(f"{'/'.join(parts)} is larger than {limit} bytes")
        return data
    finally:
        os.close(fd)


def write_data_file(data_dir: str, parts: tuple[str, ...], data: bytes) -> None:
    """Atomically replace a file under data_dir, owned like its directory."""
    dfd = _open_dir(data_dir, parts[:-1])
    try:
        dst = os.fstat(dfd)
        tmp = f".{parts[-1]}.{os.getpid()}.tmp"
        try:
            os.unlink(tmp, dir_fd=dfd)  # leftover from a crashed run
        except FileNotFoundError:
            pass
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o644, dir_fd=dfd)
        try:
            os.write(fd, data)
            # Hand it to the directory's owner so the container can read and
            # replace it like any other file of its own.
            os.fchown(fd, dst.st_uid, dst.st_gid)
        finally:
            os.close(fd)
        os.rename(tmp, parts[-1], src_dir_fd=dfd, dst_dir_fd=dfd)
    finally:
        os.close(dfd)


def open_data_file_append(data_dir: str, parts: tuple[str, ...]) -> int:
    """Open (creating if needed) a file under data_dir for appending."""
    dfd = _open_dir(data_dir, parts[:-1])
    try:
        dst = os.fstat(dfd)
        fd = os.open(parts[-1],
                     os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW
                     | os.O_NONBLOCK, 0o644, dir_fd=dfd)
    finally:
        os.close(dfd)
    st = os.fstat(fd)
    if not stat.S_ISREG(st.st_mode):
        os.close(fd)
        raise ApplyError(f"{'/'.join(parts)} is not a regular file")
    if st.st_uid != dst.st_uid:
        # Created just now by us (or pre-existing and root-owned): give it to the
        # container so the dashboard's "reset stats" can truncate it.
        os.fchown(fd, dst.st_uid, dst.st_gid)
    return fd


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_conf(text: str) -> list[str]:
    """Return the config's lines if every directive is allowed, else raise."""
    lines = text.splitlines()
    for n, raw in enumerate(lines, 1):
        if any(ord(c) < 0x20 and c != "\t" for c in raw) or "\x7f" in raw:
            raise ApplyError(f"line {n}: contains a control character")
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key = line.split("=", 1)[0].strip()
        if key not in ALLOWED_DIRECTIVES:
            raise ApplyError(f"line {n}: directive '{key[:40]}' is not allowed")
    return lines


def build_final_conf(lines: list[str]) -> str:
    return ("# GENERATED by lantern-host apply from the web app's dnsmasq.conf.\n"
            "# Do not edit: changes are overwritten on the next apply.\n"
            + "\n".join(lines)
            + "\n\n# --- Host-owned settings (never taken from the web app) ---\n"
            + f"tftp-root={TFTP_ROOT}\n")


# ---------------------------------------------------------------------------
# apply
# ---------------------------------------------------------------------------

def _replace_root_file(path: str, data: bytes, mode: int) -> bool:
    """Write a root-owned file atomically. Returns False if already identical."""
    try:
        with open(path, "rb") as f:
            if f.read() == data:
                return False
    except FileNotFoundError:
        pass
    tmp = f"{path}.new"
    with open(tmp, "wb") as f:
        f.write(data)
    os.chmod(tmp, mode)
    os.replace(tmp, path)
    return True


def _dnsmasq_test(dnsmasq: str, path: str) -> None:
    r = subprocess.run([dnsmasq, "--test", f"--conf-file={path}"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        msg = (r.stderr or r.stdout).strip().replace(path, "dnsmasq.conf")
        raise ApplyError(f"dnsmasq rejected the config: {msg}")


def _restart_dnsmasq() -> None:
    r = subprocess.run(["systemctl", "restart", DNSMASQ_UNIT],
                       capture_output=True, text=True)
    # dnsmasq can exit straight after starting (e.g. the boot interface doesn't
    # exist), which a restart of a Type=simple unit doesn't report. Give it a
    # moment and ask whether it's still up.
    time.sleep(2)
    active = subprocess.run(["systemctl", "is-active", "--quiet", DNSMASQ_UNIT])
    if r.returncode != 0 or active.returncode != 0:
        # dnsmasq's own last words (e.g. "unknown interface eth1"), not
        # systemd's "Main process exited" lines around them.
        tail = subprocess.run(
            ["journalctl", "-u", DNSMASQ_UNIT, "_COMM=dnsmasq", "-n", "2",
             "-o", "cat", "--no-pager"],
            capture_output=True, text=True).stdout.strip().replace("\n", "; ")
        raise ApplyError("dnsmasq failed to start"
                         + (f": {tail}" if tail else "")
                         + f" (see: journalctl -u {DNSMASQ_UNIT})")


def apply(data_dir: str, dnsmasq: str) -> dict:
    src = read_data_file(data_dir, ("dnsmasq", "dnsmasq.conf"), MAX_CONF_BYTES)
    if src is None:
        raise ApplyError("the web app hasn't written dnsmasq.conf yet")
    try:
        text = src.decode("utf-8")
    except UnicodeDecodeError:
        raise ApplyError("dnsmasq.conf is not valid UTF-8") from None
    final = build_final_conf(validate_conf(text)).encode()

    os.makedirs(os.path.dirname(CONF_OUT), mode=0o755, exist_ok=True)
    candidate = f"{CONF_OUT}.candidate"
    with open(candidate, "wb") as f:
        f.write(final)
    try:
        _dnsmasq_test(dnsmasq, candidate)
    finally:
        os.unlink(candidate)

    # The TFTP binaries are served to PXE clients, never executed here, so the
    # only checks needed are the fixed names and the size cap.
    os.makedirs(TFTP_ROOT, mode=0o755, exist_ok=True)
    staged = []
    for name in TFTP_FILES:
        data = read_data_file(data_dir, ("tftp", name), MAX_BINARY_BYTES)
        if data is None:
            continue
        _replace_root_file(os.path.join(TFTP_ROOT, name), data, 0o644)
        staged.append(name)

    changed = _replace_root_file(CONF_OUT, final, 0o644)
    if changed:
        _restart_dnsmasq()

    missing = [n for n in TFTP_FILES if n not in staged]
    message = "Config applied; DHCP/TFTP service restarted." if changed \
        else "Config unchanged; DHCP/TFTP service left running."
    if missing:
        message += f" iPXE binaries not staged yet: {', '.join(missing)}."
    return {"ok": not missing, "restarted": changed, "message": message}


def cmd_apply(args) -> int:
    try:
        result = apply(args.data_dir, args.dnsmasq)
    except ApplyError as e:
        result = {"ok": False, "restarted": False, "message": str(e)}
    result["applied_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(result["message"], file=sys.stderr if not result["ok"] else sys.stdout)
    try:
        write_data_file(args.data_dir, ("dnsmasq", "apply-status.json"),
                        (json.dumps(result) + "\n").encode())
    except OSError as e:
        print(f"could not write apply-status.json: {e}", file=sys.stderr)
    return 0 if result["ok"] else 1


# ---------------------------------------------------------------------------
# logfwd
# ---------------------------------------------------------------------------

def cmd_logfwd(args) -> int:
    parts = ("dnsmasq", "dnsmasq.log")
    # -n 0: only new lines, so a restart of this service doesn't duplicate the
    # last few. -o short gives the "Mon DD HH:MM:SS dnsmasq-dhcp[pid]: ..." shape
    # web/app/services/clients.py parses (the same as dnsmasq's own log file).
    # --utc because clients.py reads the timestamps as UTC (the container's
    # clock); the host's local time would put every client hours in the past.
    proc = subprocess.Popen(
        ["journalctl", "-f", "-n", "0", "-o", "short", "--no-hostname", "--utc",
         "-u", DNSMASQ_UNIT],
        stdout=subprocess.PIPE)
    fd = -1
    try:
        for line in proc.stdout:
            # (Re)open if we have no file yet or the container replaced it.
            if fd < 0 or os.fstat(fd).st_nlink == 0:
                if fd >= 0:
                    os.close(fd)
                    fd = -1
                try:
                    fd = open_data_file_append(args.data_dir, parts)
                except (OSError, ApplyError) as e:
                    print(f"cannot open dnsmasq.log: {e}", file=sys.stderr)
                    continue
            if os.fstat(fd).st_size > MAX_LOG_BYTES:
                os.ftruncate(fd, 0)
            os.write(fd, line)
    finally:
        if fd >= 0:
            os.close(fd)
        proc.terminate()
    return proc.wait()


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="lantern-host")
    p.add_argument("--data-dir", required=True,
                   help="the web app's data directory (compose.yml's ./data)")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("apply", help="validate and apply the generated config")
    a.add_argument("--dnsmasq", default="/usr/sbin/dnsmasq")
    a.set_defaults(func=cmd_apply)
    sub.add_parser("logfwd", help="forward dnsmasq's journal to dnsmasq.log") \
        .set_defaults(func=cmd_logfwd)
    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
