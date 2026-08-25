#!/bin/bash
set -u
NAME=auro-poc-emu

echo "==== full UI list ===="
docker exec "$NAME" bash -c 'python3 /tmp/ui.py list 2>/dev/null'

echo ""
echo "==== all clickable/nonscrollable nodes with text or desc ===="
docker exec "$NAME" bash -c 'python3 - << "PYEOF"
import sys, re
sys.path.insert(0, "/tmp")
import importlib.util
spec = importlib.util.spec_from_file_location("ui", "/tmp/ui.py")
ui = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ui)
xml = ui.dump()
for m in re.finditer(r"<node[^>]*>", xml):
    s = m.group(0)
    def attr(name):
        mm = re.search(name + r"=\"([^\"]*)\"", s)
        return mm.group(1) if mm else ""
    text = attr("text")
    desc = attr("content-desc")
    if text or desc:
        cls = attr("class").split(".")[-1]
        print(cls, "| text=", text[:50], "| desc=", desc[:50])
PYEOF'

echo ""
echo "==== logcat errors (login) ===="
docker exec "$NAME" bash -c 'adb logcat -d -t 100 2>/dev/null | grep -iE "moyoung|auro|login|auth|error" | grep -viE "^---|VERBOSE" | tail -20'
