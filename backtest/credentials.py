#!/usr/bin/env python3
"""
Local API-credential storage + an honest connection test. Shared by the GUI
(gui/server.py) and any CLI script (`from credentials import load_credentials`).

WHERE SECRETS LIVE
  config/secrets.local.json (git-ignored, file mode 0600). Override the path
  with the ORB_SECRETS_FILE environment variable. Writing is REFUSED if the
  target is inside a tracked project folder (toolbox/, logs/, results/, ...)
  or is not git-ignored, so a key can never be committed by accident.
  Environment variables win over the file:
    ORB_DATA_API_KEY, ORB_BROKER_API_KEY, ORB_BROKER_API_SECRET

CONNECTION TEST (generic, no provider-specific connector is implemented yet)
  You give a test URL for the provider (an endpoint that requires auth).
  Two GET requests are made:
    1. without credentials -> must be refused (401/403), otherwise the URL
       can't prove anything about your key  -> NOT_VERIFIED
    2. with credentials    -> 2xx = CONNECTED, 401/403 = AUTH_FAILED
  Auth scheme: data key -> "Authorization: Bearer <key>";
               broker    -> HTTP Basic <key>:<secret> if a secret is set, else Bearer.
  Only CONNECTED is green. Nothing else is ever reported as connected.

CLI:   python backtest/credentials.py show      (masked summary, never prints keys)
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SECRETS_FILE = ROOT / "config" / "secrets.local.json"
PROTECTED_DIRS = ("toolbox", "logs", "results", "playbook", "backtest", "tests", "gui", "data")
ENV_OVERRIDES = {("data", "api_key"): "ORB_DATA_API_KEY",
                 ("broker", "api_key"): "ORB_BROKER_API_KEY",
                 ("broker", "api_secret"): "ORB_BROKER_API_SECRET"}
SECRET_FIELDS = {("data", "api_key"), ("broker", "api_key"), ("broker", "api_secret")}
ALLOWED_MODES = ("paper",)          # live is locked until playbook §6 is met
LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")


class CredentialError(ValueError):
    pass


def empty() -> dict:
    return {"mode": "paper",
            "data": {"api_key": "", "test_url": ""},
            "broker": {"api_key": "", "api_secret": "", "test_url": ""},
            "updated_utc": None}


def secrets_path() -> Path:
    return Path(os.environ.get("ORB_SECRETS_FILE") or DEFAULT_SECRETS_FILE).resolve()


def check_safe_path(path: Path) -> None:
    """Refuse to write secrets anywhere they could be committed."""
    path = path.resolve()
    try:
        rel = path.relative_to(ROOT)
    except ValueError:
        return  # outside the repository: cannot be committed from here
    if rel.parts and rel.parts[0] in PROTECTED_DIRS:
        raise CredentialError(f"refusing to store secrets in tracked folder '{rel.parts[0]}/'")
    try:
        r = subprocess.run(["git", "-C", str(ROOT), "check-ignore", "-q", str(rel)],
                           capture_output=True, timeout=10)
        ignored = r.returncode == 0
    except Exception:
        ignored = False
    if not ignored:
        raise CredentialError(f"refusing to store secrets in '{rel}': it is not git-ignored")


def _read_file(path: Path) -> dict:
    data = empty()
    if path.exists():
        raw = json.loads(path.read_text() or "{}")
        for section in ("data", "broker"):
            data[section].update({k: str(v) for k, v in raw.get(section, {}).items() if k in data[section]})
        data["mode"] = raw.get("mode", "paper")
        data["updated_utc"] = raw.get("updated_utc")
    return data


def load_credentials() -> dict:
    """File values, overridden by environment variables. For CLI scripts and the GUI."""
    data = _read_file(secrets_path())
    for (section, key), env in ENV_OVERRIDES.items():
        if os.environ.get(env):
            data[section][key] = os.environ[env]
    return data


def save_credentials(update: dict) -> dict:
    """Merge `update` into the stored file. A blank secret field means 'keep the
    current value' (the GUI never receives stored secrets, so it can't resend them).
    Use clear_credentials() to wipe."""
    path = secrets_path()
    check_safe_path(path)
    data = _read_file(path)
    mode = update.get("mode", data["mode"])
    if mode not in ALLOWED_MODES:
        raise CredentialError("Live mode is locked until playbook §6 (paper -> live criteria) is met.")
    data["mode"] = mode
    for section in ("data", "broker"):
        for key, value in (update.get(section) or {}).items():
            if key not in data[section]:
                raise CredentialError(f"unknown field {section}.{key}")
            value = str(value).strip()
            if (section, key) in SECRET_FIELDS and value == "":
                continue
            if key == "test_url" and value:
                validate_test_url(value)
            data[section][key] = value
    data["updated_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _write(path, data)
    return masked(data)


def clear_credentials() -> dict:
    path = secrets_path()
    check_safe_path(path)
    data = empty()
    data["updated_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _write(path, data)
    return masked(data)


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def mask(value: str) -> str:
    if not value:
        return ""
    return "••••" + value[-4:] if len(value) > 8 else "••••"


def masked(data: dict) -> dict:
    """Safe to display/return: secrets reduced to a set flag + last 4 characters."""
    out = {"mode": data["mode"], "updated_utc": data.get("updated_utc"), "file": str(secrets_path())}
    for section in ("data", "broker"):
        out[section] = {}
        for key, value in data[section].items():
            if (section, key) in SECRET_FIELDS:
                out[section][key] = {"set": bool(value), "hint": mask(value),
                                     "from_env": bool(os.environ.get(ENV_OVERRIDES[(section, key)]))}
            else:
                out[section][key] = value
    return out


# --------------------------------------------------------------------------- connection test
def validate_test_url(url: str) -> None:
    p = urllib.parse.urlparse(url)
    if p.scheme not in ("https", "http") or not p.hostname:
        raise CredentialError("test URL must look like https://api.example.com/v1/endpoint")
    if p.scheme == "http" and p.hostname not in LOCAL_HOSTS:
        raise CredentialError("test URL must use https:// (keys are never sent over plain http)")


def _get(url: str, headers: dict, timeout: float) -> int:
    host = urllib.parse.urlparse(url).hostname
    handlers = [urllib.request.ProxyHandler({})] if host in LOCAL_HOSTS else []
    opener = urllib.request.build_opener(*handlers)
    req = urllib.request.Request(url, headers={"User-Agent": "orb-connection-test", **headers})
    try:
        with opener.open(req, timeout=timeout) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


def test_connection(target: str, creds: dict | None = None, timeout: float = 10.0) -> dict:
    creds = creds or load_credentials()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if target not in ("data", "broker"):
        raise CredentialError("target must be 'data' or 'broker'")
    sec = creds[target]

    def result(state, detail):
        return {"target": target, "state": state, "connected": state == "CONNECTED",
                "detail": detail, "tested_utc": now}

    if not sec.get("api_key"):
        return result("NOT_CONFIGURED", "No API key saved.")
    if not sec.get("test_url"):
        return result("NOT_CONFIGURED", "No test URL saved: there is no provider connector built in, so a "
                                        "URL that requires authentication is needed to verify the key.")
    try:
        validate_test_url(sec["test_url"])
    except CredentialError as e:
        return result("ERROR", str(e))
    if target == "broker" and sec.get("api_secret"):
        token = base64.b64encode(f"{sec['api_key']}:{sec['api_secret']}".encode()).decode()
        auth, scheme = {"Authorization": f"Basic {token}"}, "Basic key:secret"
    else:
        auth, scheme = {"Authorization": f"Bearer {sec['api_key']}"}, "Bearer key"
    try:
        anon = _get(sec["test_url"], {}, timeout)
        if anon < 400:
            return result("NOT_VERIFIED", f"The URL answered {anon} without any credentials, so it can't prove "
                                          "your key works. Use an endpoint that requires authentication.")
        authed = _get(sec["test_url"], auth, timeout)
    except Exception as e:  # DNS, refused, timeout, TLS, proxy refusal
        return result("UNREACHABLE", f"Could not reach {urllib.parse.urlparse(sec['test_url']).hostname}: "
                                     f"{type(e).__name__}: {e}")
    if 200 <= authed < 300:
        return result("CONNECTED", f"Refused without credentials ({anon}), accepted with them ({authed}). "
                                   f"Auth scheme: {scheme}.")
    if authed in (401, 403):
        return result("AUTH_FAILED", f"The endpoint rejected the saved credentials ({authed}). "
                                     f"Auth scheme tried: {scheme}.")
    return result("ERROR", f"Unexpected HTTP {authed} with credentials (without: {anon}).")


if __name__ == "__main__":
    if sys.argv[1:] == ["show"]:
        print(json.dumps(masked(load_credentials()), indent=2))
    else:
        print(__doc__)
