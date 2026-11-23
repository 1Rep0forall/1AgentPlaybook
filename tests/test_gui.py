"""Tests for backtest/credentials.py (local secrets + honest connection test) and gui/server.py.

Everything runs on 127.0.0.1; no internet access is needed.
"""
import base64
import http.client
import json
import os
import stat
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backtest"))
sys.path.insert(0, str(ROOT / "gui"))
import credentials as cr  # noqa: E402
import server as gui  # noqa: E402

SECRET = "sk_test_ABCDEFGHIJ1234"


@pytest.fixture
def secrets_file(tmp_path, monkeypatch):
    p = tmp_path / "secrets.local.json"
    monkeypatch.setenv("ORB_SECRETS_FILE", str(p))
    for env in cr.ENV_OVERRIDES.values():
        monkeypatch.delenv(env, raising=False)
    return p


def git_status():
    return subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain"], capture_output=True, text=True).stdout


# ------------------------------------------------------------------ credential storage
def test_save_load_roundtrip_masks_and_keeps_blank(secrets_file):
    out = cr.save_credentials({"data": {"api_key": SECRET, "test_url": "https://api.example.com/v1/me"}})
    assert SECRET not in json.dumps(out)
    assert out["data"]["api_key"] == {"set": True, "hint": "••••1234", "from_env": False}
    assert cr.load_credentials()["data"]["api_key"] == SECRET
    assert stat.S_IMODE(os.stat(secrets_file).st_mode) == 0o600
    cr.save_credentials({"data": {"api_key": "", "test_url": "https://api.example.com/v2/me"}})
    loaded = cr.load_credentials()
    assert loaded["data"]["api_key"] == SECRET and loaded["data"]["test_url"].endswith("/v2/me")
    cr.clear_credentials()
    assert cr.load_credentials()["data"]["api_key"] == ""


def test_live_mode_and_plain_http_are_refused(secrets_file):
    with pytest.raises(cr.CredentialError, match="§6"):
        cr.save_credentials({"mode": "live"})
    with pytest.raises(cr.CredentialError, match="https"):
        cr.save_credentials({"data": {"test_url": "http://api.example.com/v1"}})


def test_environment_variable_overrides_file(secrets_file, monkeypatch):
    cr.save_credentials({"broker": {"api_key": "file-key-0000"}})
    monkeypatch.setenv("ORB_BROKER_API_KEY", "env-key-9999")
    creds = cr.load_credentials()
    assert creds["broker"]["api_key"] == "env-key-9999"
    assert cr.masked(creds)["broker"]["api_key"]["from_env"] is True


@pytest.mark.parametrize("rel", ["toolbox/secrets.local.json", "logs/k.local.json", "results/k.json",
                                 "playbook/k.json", "not_ignored_secrets.json"])
def test_refuses_tracked_or_unignored_locations(monkeypatch, rel):
    monkeypatch.setenv("ORB_SECRETS_FILE", str(ROOT / rel))
    before = git_status()
    with pytest.raises(cr.CredentialError):
        cr.save_credentials({"data": {"api_key": SECRET}})
    assert not (ROOT / rel).exists() and git_status() == before


def test_default_secrets_location_is_git_ignored():
    for rel in ("config/secrets.local.json", "config/secrets.local.json.tmp", ".env"):
        r = subprocess.run(["git", "-C", str(ROOT), "check-ignore", "-q", rel])
        assert r.returncode == 0, f"{rel} is not git-ignored"


def test_saving_inside_repo_never_changes_tracked_files(monkeypatch):
    p = ROOT / "config" / "pytest_tmp_secrets.local.json"
    monkeypatch.setenv("ORB_SECRETS_FILE", str(p))
    before = git_status()
    try:
        cr.save_credentials({"data": {"api_key": SECRET}, "broker": {"api_key": SECRET, "api_secret": SECRET}})
        assert git_status() == before                       # ignored: invisible to git
        for d in ("toolbox", "logs", "results"):
            for f in (ROOT / d).rglob("*") if (ROOT / d).exists() else []:
                if f.is_file() and f.suffix in (".json", ".csv", ".md"):
                    assert SECRET not in f.read_text(errors="ignore"), f
    finally:
        p.unlink(missing_ok=True)


# ------------------------------------------------------------------ connection test
class FakeApi(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        auth = self.headers.get("Authorization", "")
        good = {f"Bearer {SECRET}", "Basic " + base64.b64encode(f"{SECRET}:s3cret".encode()).decode()}
        if self.path == "/public":
            code = 200
        else:
            code = 200 if auth in good else 401
        self.send_response(code)
        self.end_headers()


@pytest.fixture(scope="module")
def fake_api():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeApi)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def creds(section="data", **kw):
    c = cr.empty()
    c[section].update(kw)
    return c


def test_connection_states_are_honest(fake_api):
    assert cr.test_connection("data", creds())["state"] == "NOT_CONFIGURED"
    assert cr.test_connection("data", creds(api_key=SECRET))["state"] == "NOT_CONFIGURED"
    ok = cr.test_connection("data", creds(api_key=SECRET, test_url=fake_api + "/me"))
    assert ok["state"] == "CONNECTED" and ok["connected"] is True
    bad = cr.test_connection("data", creds(api_key="wrong-key-xxxx", test_url=fake_api + "/me"))
    assert bad["state"] == "AUTH_FAILED" and bad["connected"] is False
    public = cr.test_connection("data", creds(api_key=SECRET, test_url=fake_api + "/public"))
    assert public["state"] == "NOT_VERIFIED" and public["connected"] is False
    dead = cr.test_connection("data", creds(api_key=SECRET, test_url="http://127.0.0.1:9/x"), timeout=2)
    assert dead["state"] == "UNREACHABLE" and dead["connected"] is False
    broker = cr.test_connection("broker", creds("broker", api_key=SECRET, api_secret="s3cret", test_url=fake_api + "/a"))
    assert broker["state"] == "CONNECTED" and "Basic" in broker["detail"]
    for r in (ok, bad, public, dead, broker):
        assert SECRET not in json.dumps(r)


# ------------------------------------------------------------------ GUI server
@pytest.fixture
def dash(tmp_path, secrets_file):
    srv, app = gui.make_server(0, log_path=tmp_path / "trade_log.csv", data_dir=tmp_path / "data")
    srv.quiet = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield app, tmp_path
    srv.shutdown()


def call(app, method, path, body=None, token=True, host=None, ctype="application/json"):
    c = http.client.HTTPConnection("127.0.0.1", app.port, timeout=30)
    headers = {"Host": host or f"127.0.0.1:{app.port}"}
    if token:
        headers["X-ORB-Token"] = app.token
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = ctype
    c.request(method, path, body=data, headers=headers)
    r = c.getresponse()
    raw = r.read().decode()
    return r.status, raw


def test_page_and_api_guards(dash):
    app, _ = dash
    code, html = call(app, "GET", "/", token=False)
    assert code == 200 and app.token in html and "API settings" in html
    assert call(app, "GET", "/api/state", token=False)[0] == 403
    assert call(app, "GET", "/", token=False, host="evil.example:80")[0] == 403
    assert call(app, "POST", "/api/credentials", {"mode": "paper"}, ctype="text/plain")[0] == 415


def test_page_defaults_to_dark_with_a_theme_switch(dash):
    app, _ = dash
    html = call(app, "GET", "/", token=False)[1]
    assert '<html lang="en" data-theme="dark">' in html and 'id="theme-toggle"' in html


def test_state_shows_config_derived_rules_and_no_log_yet(dash):
    app, _ = dash
    code, raw = call(app, "GET", "/api/state")
    s = json.loads(raw)
    assert code == 200 and s["status"]["verdict"] == "GO" and s["status"]["log_exists"] is False
    assert s["status"]["pause_usd"] == 100 and s["status"]["stop_usd"] == 150
    assert s["rules"]["raw"]["max_or_width_pts"] == 3.75 and s["rules"]["raw"]["limit_offset_pts"] == 4.25
    assert s["status"]["gate"] == {"sessions": 40, "trades": 15}


def test_gui_saves_secrets_without_ever_returning_them(dash, secrets_file):
    app, _ = dash
    code, raw = call(app, "POST", "/api/credentials",
                     {"mode": "paper", "data": {"api_key": SECRET, "test_url": "https://api.example.com/x"}})
    assert code == 200 and SECRET not in raw
    assert cr.load_credentials()["data"]["api_key"] == SECRET and secrets_file.exists()
    assert SECRET not in call(app, "GET", "/api/state")[1]
    code, raw = call(app, "POST", "/api/credentials", {"mode": "live"})
    assert code == 400 and "§6" in json.loads(raw)["error"]
    code, raw = call(app, "POST", "/api/connection/test", {"target": "broker"})
    assert code == 200 and json.loads(raw)["state"] == "NOT_CONFIGURED"


def test_actions_validate_inputs_and_block_holdout(dash):
    app, tmp = dash
    (tmp / "data").mkdir()
    (tmp / "data" / "bars.csv").write_text("timestamp,open,high,low,close\n")
    assert call(app, "POST", "/api/actions/holdout", {})[0] == 403
    assert call(app, "POST", "/api/actions/dev_backtest",
                {"data_file": "../../toolbox/backtest_config.json", "data_tz": "", "bar_label": "start"})[0] == 400
    assert call(app, "POST", "/api/actions/dev_backtest",
                {"data_file": "bars.csv", "data_tz": "Mars/Base", "bar_label": "start"})[0] == 400
    assert call(app, "POST", "/api/actions/reconcile",
                {"data_file": "bars.csv", "date": "2026-02-30", "data_tz": "", "bar_label": "start"})[0] == 400
    assert call(app, "POST", "/api/actions/rm_rf", {})[0] == 404


def wait_job(app, job):
    for _ in range(120):
        if job["state"] != "running":
            return job
        time.sleep(0.25)
        job = json.loads(call(app, "GET", f"/api/jobs/{job['id']}")[1])
    raise AssertionError("job did not finish")


def test_status_action_runs_the_cli(dash):
    app, tmp = dash
    code, raw = call(app, "POST", "/api/actions/status", {})
    job = wait_job(app, json.loads(raw))
    assert code == 202 and job["state"] == "done" and "No paper-trading log yet" in job["output"]
    cols = (ROOT / "toolbox" / "trade_log_template.csv").read_text().splitlines()[0]
    row = {c: "" for c in cols.split(",")}
    row.update(date="2026-01-05", mode="SIM", traded="False", no_trade_reason="no_breakout", rule_violation="N")
    (tmp / "trade_log.csv").write_text(cols + "\n" + ",".join(row[c] for c in cols.split(",")) + "\n")
    job = wait_job(app, json.loads(call(app, "POST", "/api/actions/status", {})[1]))
    assert job["state"] == "done" and "verdict" in job["output"] and "GO" in job["output"]
    assert "session_tools.py status" in job["command"]
    s = json.loads(call(app, "GET", "/api/state")[1])["status"]
    assert s["sessions"] == 1 and s["rule_violations"] == 0


def test_paper_gate_matches_playbook():
    text = (ROOT / "playbook" / "MES_ORB_Playbook_v1.md").read_text()
    assert (f"≥ {gui.PAPER_GATE['sessions']} paper sessions and ≥ {gui.PAPER_GATE['trades']} paper trades"
            in text)
