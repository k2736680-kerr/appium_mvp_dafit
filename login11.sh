#!/bin/bash
set -u
NAME=auro-poc-emu

echo "==== tap checkbox at (99, 1320) ===="
docker exec "$NAME" bash -c 'adb shell input tap 99 1320; sleep 1'

echo "==== verify checkbox ===="
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
        mm = re.search(r"checked=\"([^\"]*)\"", s)
        print("checked =", mm.group(1) if mm else "?")
PYEOF'

echo ""
echo "==== tap Sign In ===="
docker exec "$NAME" bash -c 'adb shell input tap 540 1128; sleep 10'

echo ""
echo "==== UI after login ===="
docker exec "$NAME" bash -c 'python3 /tmp/ui.py list 2>/dev/null | head -12'

echo ""
echo "==== focus ===="
docker exec "$NAME" bash -c 'adb shell dumpsys window 2>/dev/null | grep mCurrentFocus'
