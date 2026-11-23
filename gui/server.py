#!/usr/bin/env python3
"""
Local dashboard for the MES ORB workflow (standard library only).

    python gui/server.py            # then open http://127.0.0.1:8765

Design rules:
  * Binds to 127.0.0.1 only. Every /api/* call needs the per-launch token that is
    embedded in the page, and a Host header of 127.0.0.1/localhost (blocks other
    websites and DNS-rebinding from driving it).
  * Actions run the SAME CLI scripts as a subprocess (argument lists, no shell),
    so every CLI safety lock still applies. There is deliberately NO hold-out
    button: the one-time hold-out run stays a deliberate CLI action
    (Blueprint §2.3).
  * No rule numbers live here: everything comes from toolbox/backtest_config.json
    through orb_backtest / session_tools.
  * API keys are stored only via backtest/credentials.py (git-ignored file) and are
    never sent back to the browser, only "set" + last 4 characters.
"""
from __future__ import annotations

import argparse
import json
import re
import secrets
import subprocess
import sys
import threading
import uuid
import webbrowser
from datetime import date, datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backtest"))
import credentials as cr  # noqa: E402
import gates as gt  # noqa: E402
import orb_backtest as ob  # noqa: E402
import session_tools as st  # noqa: E402

CONFIG = ROOT / "toolbox" / "backtest_config.json"
INDEX_HTML = Path(__file__).resolve().parent / "index.html"
TZ_CHOICES = ["", "America/Chicago", "America/New_York", "UTC"]   # "" = timestamps carry a UTC offset
PAPER_GATE = {"sessions": 40, "trades": 15}                       # playbook §6 item 2 (D7)
MAX_BODY = 64 * 1024
MAX_OUTPUT = 200_000
TIMEOUTS = {"status": 120, "dev_backtest": 3600, "reconcile": 600, "tests": 900}


class ApiError(Exception):
    def __init__(self, code: int, msg: str):
        super().__init__(msg)
        self.code, self.msg = code, msg


def git(*args) -> str:
    try:
        return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True,
                              timeout=10).stdout.strip()
    except Exception:
        return ""


class App:
    def __init__(self, port: int, log_path: Optional[Path] = None, data_dir: Optional[Path] = None,
                 results_dir: Optional[Path] = None):
        self.port = port
        self.results_dir = results_dir or ROOT / "results"
        self.token = secrets.token_urlsafe(24)
        self.log_path_override = log_path
        self.data_dir = data_dir or ROOT / "data"
        self.jobs: dict = {}
        self.lock = threading.Lock()
        self.connection: dict = {}          # last test result per target, this server session only

    # ------------------------------------------------------------------ state
    def log_path(self) -> Path:
        return self.log_path_override or ROOT / "logs" / f"trade_log_{date.today().year}.csv"

    def data_files(self) -> list:
        if not self.data_dir.exists():
            return []
        return sorted(p.name for p in self.data_dir.iterdir() if p.is_file() and p.suffix.lower() == ".csv")

    def status_block(self, cfg) -> dict:
        path = self.log_path()
        rel = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path
        base = {"log_file": str(rel), "pause_usd": cfg.drawdown_pause_usd, "stop_usd": cfg.drawdown_stop_usd,
                "starting_capital": cfg.starting_capital, "gate": PAPER_GATE}
        if not path.exists():
            return {**base, "verdict": "GO", "sessions": 0, "trades": 0, "equity_usd": cfg.starting_capital,
                    "drawdown_usd": 0.0, "rule_violations": 0, "log_exists": False,
                    "reasons": [f"No paper-trading log yet. Copy toolbox/trade_log_template.csv to {rel} "
                                "to start (playbook §3)."]}
        try:
            log = pd.read_csv(path, dtype=str, keep_default_na=False)
            s = st.status(log, cfg, "SIM")
            sim = log[log["mode"].str.upper() == "SIM"]
            violations = int(sim["rule_violation"].str.strip().str.upper().isin(["Y", "YES", "TRUE"]).sum())
        except Exception as e:
            return {**base, "verdict": "ERROR", "sessions": 0, "trades": 0, "equity_usd": None,
                    "drawdown_usd": None, "rule_violations": None, "log_exists": True,
                    "reasons": [f"Could not read the trade log: {type(e).__name__}: {e}"]}
        return {**base, **{k: s.get(k) for k in ("verdict", "sessions", "trades", "equity_usd", "drawdown_usd",
                                                 "current_losing_streak", "current_no_trade_streak",
                                                 "proof_gaps", "reasons")},
                "equity_usd": s.get("equity_usd", cfg.starting_capital), "drawdown_usd": s.get("drawdown_usd", 0.0),
                "trades": s.get("trades", 0), "rule_violations": violations, "log_exists": True}

    @staticmethod
    def rules_block(cfg) -> dict:
        r = st.rules(cfg)
        plain = [
            ("Opening range", f"{cfg.or_start}–{cfg.or_end} ET, from all 15 one-minute bars"),
            ("Max risk per trade", f"${cfg.max_risk_usd:g} all-in (stop distance + slippage + fees), 1 contract"),
            ("Tradable range width", f"{r['min_or_width_pts']:g} to {r['max_or_width_pts']:g} points; "
                                     f"check: {r['precheck_formula']}"),
            ("Signal", f"first completed {cfg.signal_minutes}-minute close outside the range, "
                       f"{cfg.entry_start}–{cfg.entry_end} ET; one trade per day"),
            ("Entry order", f"limit at {r['long_limit']} (long) or {r['short_limit']} (short), "
                            "stop attached at the opposite range edge"),
            ("Target", f"{cfg.target_r:g} × risk from your fill, rounded away from the fill"),
            ("Time exit", f"flat by {cfg.exit_time} ET"),
            ("Loss limits", f"${cfg.daily_loss_limit_usd:g} per day; pause at ${cfg.drawdown_pause_usd:g} "
                            f"drawdown, stop at ${cfg.drawdown_stop_usd:g}"),
        ]
        return {"plain": plain, "raw": r}

    def state(self) -> dict:
        try:
            cfg = ob.load_config(str(CONFIG))
        except SystemExit as e:
            raise ApiError(500, f"config error: {e}")
        head = git("rev-parse", "--short", "HEAD")
        tags = git("tag", "--points-at", "HEAD").split()
        return {
            "status": self.status_block(cfg),
            "rules": self.rules_block(cfg),
            "repo": {"commit": head or None, "dirty": bool(git("status", "--porcelain")),
                     "tags_at_head": tags, "frozen_tag_exists": bool(git("tag", "-l", "v1.1-frozen"))},
            "credentials": cr.masked(cr.load_credentials()),
            "connection": self.connection,
            "data_files": self.data_files(),
            "runs": gt.list_runs(self.results_dir),
            "reason_codes": st.LOGGABLE_REASONS,
            "exit_reasons": list(st.EXIT_REASONS),
            "tz_choices": TZ_CHOICES,
            "jobs": [self.public_job(j) for j in list(self.jobs.values())[-5:]],
        }

    # ------------------------------------------------------------------ jobs
    @staticmethod
    def public_job(j: dict) -> dict:
        return {k: j[k] for k in ("id", "name", "state", "returncode", "started", "finished", "output", "command")}

    def _finish(self, job: dict, state: str, output: str, rc) -> None:
        job.update(state=state, output=output[-MAX_OUTPUT:], returncode=rc,
                   finished=datetime.now(timezone.utc).isoformat(timespec="seconds"))

    def start_job(self, name: str, argv: Optional[list], note: str = "") -> dict:
        with self.lock:
            if any(j["state"] == "running" for j in self.jobs.values()):
                raise ApiError(409, "Another action is still running. Wait for it to finish.")
            jid = uuid.uuid4().hex[:8]
            job = {"id": jid, "name": name, "state": "running", "returncode": None, "output": "",
                   "started": datetime.now(timezone.utc).isoformat(timespec="seconds"), "finished": None,
                   "command": " ".join(self.display_arg(a) for a in argv) if argv else ""}
            self.jobs[jid] = job
        if argv is None:
            self._finish(job, "done", note, 0)
            return self.public_job(job)

        def run():
            try:
                p = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True, timeout=TIMEOUTS[name])
                out = (p.stdout or "") + (("\n" + p.stderr) if p.stderr else "")
                self._finish(job, "done" if p.returncode == 0 else "failed", note + out, p.returncode)
            except subprocess.TimeoutExpired:
                self._finish(job, "failed", note + f"Timed out after {TIMEOUTS[name]} s.", None)
            except Exception as e:
                self._finish(job, "failed", f"{type(e).__name__}: {e}", None)

        threading.Thread(target=run, daemon=True).start()
        return self.public_job(job)

    @staticmethod
    def display_arg(a) -> str:
        a = str(a)
        if a == sys.executable:
            return "python"
        try:
            return str(Path(a).relative_to(ROOT))
        except ValueError:
            return a

    # ------------------------------------------------------------------ session log + gates
    def log_session(self, data: dict) -> dict:
        cfg = ob.load_config(str(CONFIG))
        dry = bool(data.pop("dry_run", True))
        path = self.log_path()
        try:
            res = st.build_log_row(data, cfg, st.read_log(path))
            if not dry:
                st.append_log_row(path, res["row"])
        except st.LogError as e:
            raise ApiError(400, str(e))
        return {"saved": not dry, "log_file": str(path), "row": {k: v for k, v in res["row"].items() if v != ""},
                "violations": res["violations"], "checks": res["checks"]}

    def evaluate_gates(self, data: dict) -> dict:
        runs = {r["run_id"]: r for r in gt.list_runs(self.results_dir)}

        def pick(key, period, required):
            rid = data.get(key) or ""
            if not rid and not required:
                return None
            if rid not in runs or runs[rid]["period"] != period:
                raise ApiError(400, f"pick a {period} run from the results list")
            return runs[rid]["path"]
        try:
            return gt.evaluate(pick("dev", "dev", True), pick("holdout", "holdout", False))
        except gt.RunError as e:
            raise ApiError(400, str(e))

    # ------------------------------------------------------------------ validation
    def pick_data_file(self, name) -> Path:
        if not name or name not in self.data_files():
            raise ApiError(400, "Pick a CSV file that is inside the data/ folder.")
        return self.data_dir / name

    @staticmethod
    def tz_args(tz) -> list:
        if tz not in TZ_CHOICES:
            raise ApiError(400, f"timezone must be one of {TZ_CHOICES}")
        return ["--data-tz", tz] if tz else []

    @staticmethod
    def bar_label(v) -> str:
        if v not in ("start", "end"):
            raise ApiError(400, "bar label must be 'start' or 'end'")
        return v

    def action(self, name: str, p: dict) -> dict:
        py = sys.executable
        if name == "status":
            if not self.log_path().exists():
                return self.start_job(name, None, self.status_block(ob.load_config(str(CONFIG)))["reasons"][0])
            return self.start_job(name, [py, ROOT / "backtest" / "session_tools.py", "status",
                                         "--log", self.log_path()])
        if name == "tests":
            return self.start_job(name, [py, "-m", "pytest", "tests/", "-q"])
        if name == "dev_backtest":
            argv = [py, ROOT / "backtest" / "orb_backtest.py", "--period", "dev",
                    "--data", self.pick_data_file(p.get("data_file")), "--config", CONFIG,
                    *self.tz_args(p.get("data_tz", "")), "--bar-label", self.bar_label(p.get("bar_label", "start"))]
            note = ("NOTE: the working tree has uncommitted changes. Dev runs are allowed, but a run only counts "
                    "as proof from a committed state (playbook §4.2).\n\n") if git("status", "--porcelain") else ""
            return self.start_job(name, argv, note)
        if name == "reconcile":
            d = str(p.get("date", ""))
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d):
                raise ApiError(400, "date must be YYYY-MM-DD")
            try:
                date.fromisoformat(d)
            except ValueError:
                raise ApiError(400, "date is not a real calendar date")
            if not self.log_path().exists():
                raise ApiError(400, f"No trade log at {self.log_path()} to reconcile against.")
            argv = [py, ROOT / "backtest" / "session_tools.py", "reconcile", "--log", self.log_path(),
                    "--bars", self.pick_data_file(p.get("data_file")), "--date", d,
                    *self.tz_args(p.get("data_tz", "")), "--bar-label", self.bar_label(p.get("bar_label", "start"))]
            return self.start_job(name, argv)
        if name == "holdout":
            raise ApiError(403, "The hold-out run is one-time and CLI-only by design (Blueprint §2.3).")
        raise ApiError(404, f"unknown action '{name}'")


def make_handler(app: App):
    allowed_hosts = {f"127.0.0.1:{app.port}", f"localhost:{app.port}"}

    class Handler(BaseHTTPRequestHandler):
        server_version = "ORBDashboard/1.0"
        sys_version = ""

        def log_message(self, fmt, *args):  # request line only; never bodies
            if not getattr(self.server, "quiet", False):
                sys.stderr.write("%s %s\n" % (self.log_date_time_string(), fmt % args))

        def send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy",
                             "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
                             "connect-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'none'; "
                             "frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)

        def json(self, code: int, obj) -> None:
            self.send(code, json.dumps(obj, default=str).encode(), "application/json")

        def guard(self, api: bool) -> bool:
            if self.headers.get("Host") not in allowed_hosts:
                self.json(403, {"error": "bad Host header"})
                return False
            if api and not secrets.compare_digest(self.headers.get("X-ORB-Token", ""), app.token):
                self.json(403, {"error": "missing or wrong token (reload the page)"})
                return False
            return True

        def body(self) -> dict:
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                raise ApiError(415, "Content-Type must be application/json")
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_BODY:
                raise ApiError(413, "request too large")
            try:
                data = json.loads(self.rfile.read(n) or b"{}")
            except json.JSONDecodeError:
                raise ApiError(400, "invalid JSON")
            if not isinstance(data, dict):
                raise ApiError(400, "JSON object expected")
            return data

        def do_GET(self):
            if self.path in ("/", "/index.html"):
                if not self.guard(api=False):
                    return
                html = INDEX_HTML.read_text().replace("__ORB_TOKEN__", app.token)
                return self.send(200, html.encode(), "text/html; charset=utf-8")
            if not self.guard(api=True):
                return
            try:
                if self.path == "/api/state":
                    return self.json(200, app.state())
                m = re.fullmatch(r"/api/jobs/([0-9a-f]{8})", self.path)
                if m and m.group(1) in app.jobs:
                    return self.json(200, app.public_job(app.jobs[m.group(1)]))
                raise ApiError(404, "not found")
            except ApiError as e:
                self.json(e.code, {"error": e.msg})

        def do_POST(self):
            if not self.guard(api=True):
                return
            try:
                data = self.body()
                if self.path == "/api/credentials":
                    return self.json(200, {"credentials": cr.save_credentials(data)})
                if self.path == "/api/credentials/clear":
                    app.connection.clear()
                    return self.json(200, {"credentials": cr.clear_credentials()})
                if self.path == "/api/log":
                    return self.json(200, app.log_session(data))
                if self.path == "/api/gates":
                    return self.json(200, app.evaluate_gates(data))
                if self.path == "/api/connection/test":
                    res = cr.test_connection(str(data.get("target", "")))
                    app.connection[res["target"]] = res
                    return self.json(200, res)
                m = re.fullmatch(r"/api/actions/([a-z_]+)", self.path)
                if m:
                    return self.json(202, app.action(m.group(1), data))
                raise ApiError(404, "not found")
            except cr.CredentialError as e:
                self.json(400, {"error": str(e)})
            except ApiError as e:
                self.json(e.code, {"error": e.msg})

    return Handler


def make_server(port: int = 8765, **kw):
    srv = ThreadingHTTPServer(("127.0.0.1", port), None)   # bind first so port 0 resolves
    app = App(srv.server_address[1], **kw)
    srv.RequestHandlerClass = make_handler(app)
    srv.daemon_threads = True
    return srv, app


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Local MES ORB dashboard (127.0.0.1 only)")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--open", action="store_true", help="open the dashboard in your browser")
    a = ap.parse_args(argv)
    srv, app = make_server(a.port)
    url = f"http://127.0.0.1:{app.port}/"
    print(f"MES ORB dashboard running at {url}  (local only; Ctrl+C to stop)")
    if a.open:
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
