import fnmatch
import json
import os
import socket
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

import block

CFG = Path(os.environ.get("HCUI_CONFIG", "/hermes-data/config.yaml"))
HIST = Path(os.environ.get("HCUI_HISTORY", "/history"))
SOCK = os.environ.get("HCUI_HELPER_SOCK", "/run/hcui/helper.sock")
REQUIRE_AUTH = os.environ.get("HCUI_REQUIRE_AUTH", "1") == "1"
LOCK = threading.Lock()
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware("http")
async def guard(request: Request, call_next):
    if request.url.path.startswith("/api/"):
        if REQUIRE_AUTH and not request.headers.get("authorization", "").lower().startswith("basic "):
            return JSONResponse({"detail": "basic auth missing at proxy"}, 401)
        if request.method != "GET" and request.headers.get("x-hcui") != "1":
            return JSONResponse({"detail": "missing X-HCUI header"}, 403)
    resp = await call_next(request)
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Frame-Options"] = "DENY"
    return resp


def git(*args, check=True):
    r = subprocess.run(["git", "-C", str(HIST), *args], capture_output=True, text=True, timeout=20)
    if check and r.returncode:
        raise RuntimeError(r.stderr.strip())
    return r.stdout.strip()


def commit(text, msg):
    """Store text as a new revision. Return the revision id (no new commit if unchanged)."""
    (HIST / "config.yaml").write_text(text)
    git("add", "config.yaml")
    if git("status", "--porcelain", check=False):
        git("-c", "user.name=hermes-config-ui", "-c", "user.email=hcui@localhost",
            "commit", "-q", "-m", msg)
    return git("rev-parse", "--short", "HEAD")


def init_history():
    HIST.mkdir(parents=True, exist_ok=True)
    os.chmod(HIST, 0o700)
    if not (HIST / ".git").exists():
        git("init", "-q")
    commit(CFG.read_text(), "external edit (start)")


def atomic_write(text):
    st = CFG.stat()
    fd, tmp = tempfile.mkstemp(dir=CFG.parent, prefix=".config.yaml.hcui.")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, st.st_mode & 0o777)
        os.replace(tmp, CFG)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def helper(req):
    try:
        with socket.socket(socket.AF_UNIX) as s:
            s.settimeout(60)
            s.connect(SOCK)
            s.sendall(json.dumps(req).encode() + b"\n")
            return json.loads(s.makefile().readline() or "{}")
    except OSError as e:
        return {"ok": False, "error": f"helper: {e}"}


def watcher():
    while True:
        time.sleep(5)
        try:
            with LOCK:
                commit(CFG.read_text(), "external edit")
        except Exception as e:  # noqa: BLE001
            print("watcher:", e, flush=True)


def diff_summary(old, new):
    o = {d["pattern"]: d["enabled"] for d in old["deny"]}
    n = {d["pattern"]: d["enabled"] for d in new["deny"]}
    msg = [f"{p}: {'on' if n[p] else 'off'}" for p in n if p in o and o[p] != n[p]]
    msg += [f"+{p}" for p in n if p not in o] + [f"-{p}" for p in o if p not in n]
    ok = {k["key"]: k.get("value") for k in old["keys"]}
    msg += [f"{k['key']}={k.get('value')}" for k in new["keys"]
            if k["key"] != "deny" and ok.get(k["key"]) != k.get("value")]
    if not msg and [d["pattern"] for d in old["deny"]] != [d["pattern"] for d in new["deny"]]:
        msg = ["reorder deny"]
    return ", ".join(msg) or "no change"


class Save(BaseModel):
    block_sha: str
    keys: list
    deny: list
    reason: str | None = None


def save_model(model, base_sha, label):
    with LOCK:
        text = CFG.read_text()
        cur = block.parse(text)
        if cur["block_sha"] != base_sha:
            raise HTTPException(409, {"detail": "approvals block changed on disk", "current": cur})
        try:
            new = block.apply(text, model)
        except ValueError as e:
            raise HTTPException(422, str(e))
        summary = diff_summary(cur, model)
        if new == text:
            return {"rev": git("rev-parse", "--short", "HEAD"), "model": cur, "summary": summary}
        before = commit(text, "external edit")
        atomic_write(new)
        after = commit(new, f"approvals: {label}{summary}")
    helper({"op": "notify", "text": f"hermes-config-ui: approvals {label}{summary} ({after})"})
    return {"before": before, "rev": after, "model": block.parse(new), "summary": summary}


@app.get("/api/approvals")
def get_approvals():
    try:
        return block.parse(CFG.read_text()) | {"enums": block.ENUMS}
    except block.Unsupported as e:
        raise HTTPException(422, f"read-only: {e}")


@app.put("/api/approvals")
def put_approvals(s: Save):
    return save_model({"keys": s.keys, "deny": s.deny}, s.block_sha, (s.reason + ": ") if s.reason else "")


class Restore(BaseModel):
    block_sha: str
    rev: str


@app.post("/api/restore")
def restore(r: Restore):
    if not r.rev.isalnum():
        raise HTTPException(400, "bad rev")
    old = block.parse(git("show", f"{r.rev}:config.yaml"))
    return save_model({"keys": old["keys"], "deny": old["deny"]}, r.block_sha, f"restore {r.rev}: ")


@app.get("/api/history")
def history():
    out = git("log", "-n", "100", "--format=%h%x09%cI%x09%s", check=False)
    return [dict(zip(("rev", "time", "msg"), l.split("\t", 2))) for l in out.splitlines()]


@app.get("/api/history/{rev}/diff")
def rev_diff(rev: str):
    if not rev.isalnum():
        raise HTTPException(400, "bad rev")
    return {"diff": git("show", "--format=%h %s", rev, "--", "config.yaml", check=False)}


class Test(BaseModel):
    command: str


@app.post("/api/test")
def test_cmd(t: Test):
    """Approximate Hermes `approvals.deny` match (case-insensitive fnmatch, no deobfuscation)."""
    m = block.parse(CFG.read_text())
    c = t.command.lower().strip()
    on = [d["pattern"] for d in m["deny"] if d["enabled"] and fnmatch.fnmatchcase(c, d["pattern"].lower())]
    off = [d["pattern"] for d in m["deny"] if not d["enabled"] and fnmatch.fnmatchcase(c, d["pattern"].lower())]
    return {"blocked_by": on, "would_block_if_enabled": off}


class Restart(BaseModel):
    target: str


@app.post("/api/restart")
def restart(r: Restart):
    res = helper({"op": "restart", "target": r.target})
    if not res.get("ok"):
        raise HTTPException(502, res.get("error", "restart failed"))
    return res


@app.get("/api/status")
def status():
    return helper({"op": "status"})


@app.get("/")
def index():
    return FileResponse(Path(__file__).parent / "static" / "index.html")


@app.on_event("startup")
def startup():
    init_history()
    threading.Thread(target=watcher, daemon=True).start()
