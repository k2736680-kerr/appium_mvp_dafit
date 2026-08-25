#!/bin/bash
set -u
NAME=auro-poc-emu

echo "==== swipe up to reveal checkbox ===="
docker exec "$NAME" bash -c 'adb shell input swipe 540 1400 540 700 300; sleep 2'

echo "==== UI after swipe ===="
docker exec "$NAME" bash -c 'python3 /tmp/ui.py list 2>/dev/null | head -15'

echo ""
echo "==== checkbox state ===="
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
    if "CheckBox" in s:
        def attr(name):
            mm = re.search(name + r"=\"([^\"]*)\"", s)
            return mm.group(1) if mm else ""
        b = re.search(r"bounds=\"\[(\d+),(\d+)\]\[(\d+),(\d+)\]\"", s)
        print("CheckBox checked=", attr("checked"), "bounds=", b.group(0) if b else "?")
PYEOF'
