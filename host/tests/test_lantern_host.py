"""Tests for host/lantern_host.py: the allowlist and the hostile-directory file
access. Run with: python3 -m pytest host/tests
"""
import importlib.util
import json
import os
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "lantern_host", Path(__file__).resolve().parents[1] / "lantern_host.py")
lh = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lh)


@pytest.fixture
def data(tmp_path):
    (tmp_path / "dnsmasq").mkdir()
    (tmp_path / "tftp").mkdir()
    return tmp_path


# --- validate_conf -----------------------------------------------------------

def test_allowed_directives_and_comments_pass():
    text = "# comment\n\nport=0\nlog-dhcp\ninterface=eth0\nenable-tftp\n"
    assert lh.validate_conf(text) == text.splitlines()


@pytest.mark.parametrize("line", [
    "dhcp-script=/bin/sh",
    "conf-file=/etc/shadow",
    "conf-dir=/tmp",
    "log-facility=/etc/passwd",
    "tftp-root=/",
    "addn-hosts=/etc/shadow",
    "dhcp-leasefile=/etc/passwd",
    "pid-file=/etc/passwd",
    "user=root",
    "  dhcp-script = /bin/sh",
])
def test_dangerous_directives_rejected(line):
    with pytest.raises(lh.ApplyError, match="not allowed"):
        lh.validate_conf(f"port=0\n{line}\n")


def test_control_characters_rejected():
    with pytest.raises(lh.ApplyError, match="control character"):
        lh.validate_conf("port=0\x00\n")


def test_final_conf_appends_host_tftp_root():
    out = lh.build_final_conf(["enable-tftp"])
    assert out.rstrip().endswith(f"tftp-root={lh.TFTP_ROOT}")


# --- read_data_file ----------------------------------------------------------

def test_read_regular_file(data):
    (data / "dnsmasq" / "dnsmasq.conf").write_text("port=0\n")
    assert lh.read_data_file(str(data), ("dnsmasq", "dnsmasq.conf"), 100) == b"port=0\n"


def test_read_missing_returns_none(data):
    assert lh.read_data_file(str(data), ("dnsmasq", "dnsmasq.conf"), 100) is None
    assert lh.read_data_file(str(data), ("nope", "dnsmasq.conf"), 100) is None


def test_read_refuses_symlinked_file(data, tmp_path_factory):
    secret = tmp_path_factory.mktemp("outside") / "secret"
    secret.write_text("root-only")
    (data / "dnsmasq" / "dnsmasq.conf").symlink_to(secret)
    with pytest.raises(lh.ApplyError, match="cannot open"):
        lh.read_data_file(str(data), ("dnsmasq", "dnsmasq.conf"), 100)


def test_read_refuses_symlinked_directory(data, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside")
    (outside / "dnsmasq.conf").write_text("port=0\n")
    (data / "dnsmasq").rmdir()
    (data / "dnsmasq").symlink_to(outside)
    with pytest.raises(OSError):
        lh.read_data_file(str(data), ("dnsmasq", "dnsmasq.conf"), 100)


def test_read_refuses_fifo_without_blocking(data):
    os.mkfifo(data / "dnsmasq" / "dnsmasq.conf")
    with pytest.raises(lh.ApplyError, match="not a regular file"):
        lh.read_data_file(str(data), ("dnsmasq", "dnsmasq.conf"), 100)


def test_read_enforces_size_cap(data):
    (data / "dnsmasq" / "dnsmasq.conf").write_text("x" * 101)
    with pytest.raises(lh.ApplyError, match="larger than"):
        lh.read_data_file(str(data), ("dnsmasq", "dnsmasq.conf"), 100)


# --- write_data_file / open_data_file_append ---------------------------------

def test_write_replaces_planted_symlink_without_following(data, tmp_path_factory):
    target = tmp_path_factory.mktemp("outside") / "victim"
    target.write_text("original")
    status = data / "dnsmasq" / "apply-status.json"
    status.symlink_to(target)
    lh.write_data_file(str(data), ("dnsmasq", "apply-status.json"), b"{}\n")
    assert target.read_text() == "original"
    assert not status.is_symlink()
    assert status.read_text() == "{}\n"


def test_append_refuses_symlink(data, tmp_path_factory):
    target = tmp_path_factory.mktemp("outside") / "victim"
    target.write_text("original")
    (data / "dnsmasq" / "dnsmasq.log").symlink_to(target)
    with pytest.raises(OSError):
        lh.open_data_file_append(str(data), ("dnsmasq", "dnsmasq.log"))
    assert target.read_text() == "original"


def test_append_creates_and_appends(data):
    fd = lh.open_data_file_append(str(data), ("dnsmasq", "dnsmasq.log"))
    os.write(fd, b"a\n")
    os.close(fd)
    fd = lh.open_data_file_append(str(data), ("dnsmasq", "dnsmasq.log"))
    os.write(fd, b"b\n")
    os.close(fd)
    assert (data / "dnsmasq" / "dnsmasq.log").read_text() == "a\nb\n"


# --- cmd_apply error reporting -----------------------------------------------

def test_apply_rejection_is_reported_in_status_file(data, monkeypatch):
    (data / "dnsmasq" / "dnsmasq.conf").write_text("dhcp-script=/bin/sh\n")
    # Must fail before touching anything root-owned.
    monkeypatch.setattr(lh, "CONF_OUT", str(data / "must-not-exist.conf"))
    rc = lh.main(["--data-dir", str(data), "apply"])
    status = json.loads((data / "dnsmasq" / "apply-status.json").read_text())
    assert rc == 1
    assert status["ok"] is False
    assert "dhcp-script" in status["message"]
    assert not (data / "must-not-exist.conf").exists()
