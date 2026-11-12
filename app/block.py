"""Line-level editor for the top-level `approvals:` block of Hermes config.yaml.

Only the lines of the approvals block change. All other bytes of the file stay the same.
A disabled deny rule is a comment line: `  #  - <glob>`.
"""
import hashlib
import json
import re

import yaml

ENUMS = {"mode": ["manual", "smart", "off"], "cron_mode": ["deny", "approve"],
         "unattended_mode": ["deny", "approve"]}
BOOLS = {"destructive_slash_confirm"}
KEY_RE = re.compile(r"^  ([A-Za-z_][A-Za-z0-9_]*):(?:\s+(.*?))?\s*$")
ON_RE = re.compile(r"^    - (.*?)\s*$")
OFF_RE = re.compile(r"^  #\s*- (.*?)\s*$")


class Unsupported(Exception):
    pass


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def span(lines):
    """Return (start, end): approvals header line index and end (exclusive)."""
    start = next((i for i, l in enumerate(lines) if l.rstrip() == "approvals:"), None)
    if start is None:
        raise Unsupported("no top-level 'approvals:' line")
    end = start + 1
    while end < len(lines) and (lines[end].startswith((" ", "\t")) or not lines[end].strip()):
        end += 1
    while end > start + 1 and not lines[end - 1].strip():  # keep trailing blank lines outside
        end -= 1
    return start, end


def _load_scalar(raw):
    return yaml.safe_load(raw) if raw else None


def parse(text):
    lines = text.splitlines(keepends=True)
    s, e = span(lines)
    keys, deny, in_deny = [], [], False
    for raw in lines[s + 1:e]:
        l = raw.rstrip("\n")
        if not l.strip():
            continue
        m = OFF_RE.match(l)
        if m and in_deny:
            deny.append({"pattern": str(_load_scalar(m.group(1))), "enabled": False})
            continue
        m = ON_RE.match(l)
        if m and in_deny:
            deny.append({"pattern": str(_load_scalar(m.group(1))), "enabled": True})
            continue
        m = KEY_RE.match(l)
        if m:
            k, v = m.group(1), m.group(2)
            in_deny = k == "deny"
            if in_deny:
                if v not in (None, "[]"):
                    raise Unsupported("deny must be a block list")
                keys.append({"key": "deny"})
            else:
                keys.append({"key": k, "value": _load_scalar(v)})
            continue
        raise Unsupported(f"line not supported: {l!r}")
    if not any(k["key"] == "deny" for k in keys):
        keys.append({"key": "deny"})
    return {"keys": keys, "deny": deny, "block_sha": sha("".join(lines[s:e]))}


def _plain_ok(p):
    try:
        return (yaml.safe_load(p) == p and p == p.strip() and " #" not in p
                and not p.startswith("#"))
    except yaml.YAMLError:
        return False


def ser(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if v is None:
        return "null"
    if isinstance(v, (int, float)):
        return str(v)
    v = str(v)
    return v if _plain_ok(v) else json.dumps(v)


def validate(model):
    seen = set()
    for k in model["keys"]:
        key = k["key"]
        if key in seen:
            raise ValueError(f"duplicate key {key}")
        seen.add(key)
        if key in ENUMS and k.get("value") not in ENUMS[key]:
            raise ValueError(f"{key}: value must be one of {ENUMS[key]}")
        if key in BOOLS and not isinstance(k.get("value"), bool):
            raise ValueError(f"{key}: value must be true or false")
    pats = [d["pattern"] for d in model["deny"]]
    for p in pats:
        if not isinstance(p, str) or not p.strip() or "\n" in p:
            raise ValueError(f"bad deny pattern {p!r}")
    if len(set(pats)) != len(pats):
        raise ValueError("duplicate deny pattern")


def render(model):
    out = ["approvals:\n"]
    for k in model["keys"]:
        if k["key"] == "deny":
            if not model["deny"]:
                out.append("  deny: []\n")
                continue
            out.append("  deny:\n")
            for d in model["deny"]:
                p = ser(d["pattern"])
                out.append(f"    - {p}\n" if d["enabled"] else f"  #  - {p}\n")
        else:
            out.append(f"  {k['key']}: {ser(k['value'])}\n")
    return "".join(out)


def apply(text, model):
    """Return the new file text. Raise ValueError when the result is not safe."""
    validate(model)
    lines = text.splitlines(keepends=True)
    s, e = span(lines)
    new = "".join(lines[:s]) + render(model) + "".join(lines[e:])
    old_doc, new_doc = yaml.safe_load(text), yaml.safe_load(new)
    want = {k["key"]: k.get("value") for k in model["keys"] if k["key"] != "deny"}
    want["deny"] = [d["pattern"] for d in model["deny"] if d["enabled"]]
    got = dict(new_doc.get("approvals") or {})
    got["deny"] = got.get("deny") or []
    if got != want:
        raise ValueError(f"round-trip mismatch: {got} != {want}")
    old_doc.pop("approvals", None)
    new_doc.pop("approvals", None)
    if old_doc != new_doc:
        raise ValueError("write would change keys outside approvals")
    if parse(new)["deny"] != model["deny"]:
        raise ValueError("disabled-rule round-trip mismatch")
    return new
