import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "app"))
import block

SRC = pathlib.Path(sys.argv[1]).read_text() if len(sys.argv) > 1 else None

def check(text):
    m = block.parse(text)
    assert block.apply(text, m) == text, "identity round-trip changed bytes"
    m["deny"][0]["enabled"] = not m["deny"][0]["enabled"]
    m["deny"].append({"pattern": "*weird: x #y", "enabled": True})
    m["deny"].append({"pattern": "aws * tag*z", "enabled": False})
    for k in m["keys"]:
        if k["key"] == "mode": k["value"] = "manual"
    new = block.apply(text, m)
    m2 = block.parse(new)
    assert m2["deny"] == m["deny"]
    s, e = block.span(text.splitlines(True)); s2, e2 = block.span(new.splitlines(True))
    assert text.splitlines(True)[:s] == new.splitlines(True)[:s2]
    assert text.splitlines(True)[e:] == new.splitlines(True)[e2:]
    for bad in ({"key": "mode", "value": "yolo"},):
        mm = block.parse(text); mm["keys"] = [bad if k["key"] == "mode" else k for k in mm["keys"]]
        try: block.apply(text, mm); raise AssertionError("bad mode accepted")
        except ValueError: pass
    print("ok", len(m2["deny"]), "rules")

check(SRC)
