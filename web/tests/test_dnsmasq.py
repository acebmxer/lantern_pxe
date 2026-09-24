"""dnsmasq.conf generation against the host's allowlist, and the apply-status
round trip the settings page shows.
"""
import importlib.util
import json
from pathlib import Path

import pytest

import app.config as app_config
from app.services import dnsmasq
from tests.test_routes import _login

# host/lantern_host.py is stdlib-only and lives outside the web package, so it
# isn't in the web image (whose build context is web/). Loaded from the repo.
_HOST = Path(__file__).resolve().parents[2] / "host" / "lantern_host.py"


@pytest.fixture(scope="module")
def lantern_host():
    if not _HOST.exists():
        pytest.skip("host/lantern_host.py not available (running inside the image)")
    spec = importlib.util.spec_from_file_location("lantern_host", _HOST)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

_BASE = {
    "server_ip": "192.168.1.10",
    "boot_interface": "eth0",
    "dhcp_range_start": "192.168.1.100",
    "dhcp_range_end": "192.168.1.200",
    "dhcp_subnet_mask": "255.255.255.0",
    "dhcp_gateway": "192.168.1.1",
    "dhcp_dns": "192.168.1.1",
}


@pytest.mark.parametrize("mode", ["proxy", "full", "external"])
@pytest.mark.parametrize("dhcp", ["1", "0"])
@pytest.mark.parametrize("tftp", ["1", "0"])
def test_every_generated_config_passes_host_allowlist(lantern_host, mode, dhcp,
                                                   tftp):
    """If the generator emits a directive the host doesn't allow, every settings
    save would be refused on the host. Keeps the two lists in step."""
    text = dnsmasq.build_config(
        dict(_BASE, dhcp_mode=mode, svc_dhcp=dhcp, svc_tftp=tftp))
    lantern_host.validate_conf(text)


def test_generated_config_leaves_paths_to_the_host():
    text = dnsmasq.build_config(dict(_BASE, dhcp_mode="proxy", svc_dhcp="1",
                                     svc_tftp="1"))
    assert "tftp-root" not in text
    assert "log-facility" not in text


def test_apply_status_missing_and_malformed(tmp_path, monkeypatch):
    monkeypatch.setattr(app_config, "DNSMASQ_DIR", tmp_path)
    assert dnsmasq.apply_status() is None
    (tmp_path / "apply-status.json").write_text("not json")
    assert dnsmasq.apply_status() is None
    (tmp_path / "apply-status.json").write_text("[1, 2]")
    assert dnsmasq.apply_status() is None


def test_apply_status_parsed(tmp_path, monkeypatch):
    monkeypatch.setattr(app_config, "DNSMASQ_DIR", tmp_path)
    (tmp_path / "apply-status.json").write_text(json.dumps({
        "ok": True, "restarted": True, "message": "Config applied.",
        "applied_at": "2026-09-24T12:00:00+00:00"}))
    st = dnsmasq.apply_status()
    assert st == {"ok": True, "restarted": True, "message": "Config applied.",
                  "applied_at": "2026-09-24T12:00:00+00:00"}


def test_settings_page_says_when_host_service_has_not_reported(client):
    _login(client)
    (app_config.DNSMASQ_DIR / "apply-status.json").unlink(missing_ok=True)
    r = client.get("/settings")
    assert r.status_code == 200
    assert "hasn't reported in" in r.text
    assert "DHCP/TFTP service:" not in r.text


def test_settings_page_shows_ok_status(client):
    _login(client)
    (app_config.DNSMASQ_DIR / "apply-status.json").write_text(json.dumps({
        "ok": True, "restarted": True, "message": "Config applied; restarted.",
        "applied_at": "2026-09-24T12:00:00+00:00"}))
    r = client.get("/settings")
    assert 'class="alert ok">DHCP/TFTP service: Config applied; restarted.' in r.text
    assert "hasn't reported in" not in r.text


def test_settings_page_shows_rejection(client):
    _login(client)
    (app_config.DNSMASQ_DIR / "apply-status.json").write_text(json.dumps({
        "ok": False, "restarted": False,
        "message": "line 3: directive 'x' is not allowed",
        "applied_at": "2026-09-24T12:00:00+00:00"}))
    r = client.get("/settings")
    assert 'class="alert error">DHCP/TFTP service: line 3: directive &#39;x&#39; is not allowed' in r.text


def test_render_writes_config(tmp_path, monkeypatch, db):
    monkeypatch.setattr(app_config, "DNSMASQ_DIR", tmp_path)
    text = dnsmasq.render(db)
    assert (tmp_path / "dnsmasq.conf").read_text() == text
    assert not (tmp_path / ".dnsmasq.conf.tmp").exists()
