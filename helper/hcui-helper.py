#!/usr/bin/env python3
"""Host helper for hermes-config-ui. Runs as a systemd --user service on the WSL host.

Listens on a unix socket. Accepts only these ops:
  restart <target>  (target from UNITS, nothing else)
  status
  notify <text>     (Telegram message to TELEGRAM_HOME_CHANNEL)
It never returns or logs the Telegram token.
"""
import json
import os
import socketserver
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path

SOCK = Path(os.environ.get("HCUI_HELPER_SOCK", Path.home() / ".local/share/hermes-config-ui/run/helper.sock"))
ENV = Path(os.environ.get("HCUI_HERMES_ENV", Path.home() / "hermes/data/.env"))
UNITS = {
    "gateway": os.environ.get("HCUI_UNIT_GATEWAY", "hermes-gateway.service"),
    "webui": os.environ.get("HCUI_UNIT_WEBUI", "hermes-webui.service"),
    "dashboard": os.environ.get("HCUI_UNIT_DASHBOARD", "hermes-dashboard.service"),
}


def env(key):
    for line in ENV.read_text().splitlines():
        if line.startswith(key + "="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def systemctl(*a):
    return subprocess.run(["systemctl", "--user", *a], capture_output=True, text=True, timeout=90)


def notify(text):
    tok, chat = env("TELEGRAM_BOT_TOKEN"), env("TELEGRAM_HOME_CHANNEL")
    if not tok or not chat:
        return {"ok": False, "error": "telegram not configured"}
    data = urllib.parse.urlencode({"chat_id": chat, "text": text[:3500]}).encode()
    try:
        with urllib.request.urlopen(f"https://api.telegram.org/bot{tok}/sendMessage", data, timeout=15) as r:
            return {"ok": r.status == 200}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": type(e).__name__}  # no URL: it holds the token


def handle(req):
    op = req.get("op")
    if op == "status":
        return {"ok": True, "units": {k: systemctl("is-active", u).stdout.strip() for k, u in UNITS.items()}}
    if op == "restart":
        unit = UNITS.get(req.get("target"))
        if not unit:
            return {"ok": False, "error": "target not allowed"}
        r = systemctl("restart", unit)
        state = systemctl("is-active", unit).stdout.strip()
        print(f"restart {unit}: rc={r.returncode} state={state}", flush=True)
        notify(f"hermes-config-ui: restart {unit}: {state}")
        return {"ok": r.returncode == 0, "state": state, "error": r.stderr.strip()[-300:]}
    if op == "notify":
        return notify(str(req.get("text", "")))
    return {"ok": False, "error": "op not allowed"}


class H(socketserver.StreamRequestHandler):
    def handle(self):
        try:
            res = handle(json.loads(self.rfile.readline(65536)))
        except Exception as e:  # noqa: BLE001
            res = {"ok": False, "error": type(e).__name__}
        self.wfile.write(json.dumps(res).encode() + b"\n")


if __name__ == "__main__":
    SOCK.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(SOCK.parent, 0o700)
    SOCK.unlink(missing_ok=True)
    srv = socketserver.ThreadingUnixStreamServer(str(SOCK), H)
    os.chmod(SOCK, 0o600)
    print(f"listening on {SOCK}", flush=True)
    srv.serve_forever()
