#!/bin/bash
set -u
NAME=auro-poc-emu

echo "==== full XML dump (structure) ===="
docker exec "$NAME" bash -c 'python3 - << "PYEOF"
import sys, re
sys.path.insert(0, "/tmp")
import importlib.util
spec = importlib.util.spec_from_file_location("ui", "/tmp/ui.py")
ui = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ui)
xml = ui.dump()
count = 0
for m in re.finditer(r"<node[^>]*>", xml):
    s = m.group(0)
    def attr(name):
        mm = re.search(name + r"=\"([^\"]*)\"", s)
        return mm.group(1) if mm else ""
    b = attr("bounds")
    bm = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", b)
    if bm:
        y = int(bm.group(2))
        if y < 1000 or y > 1300:
            continue
    count += 1
    cls = attr("class").split(".")[-1]
    print(cls, "| bounds=", attr("bounds"), "| text=", attr("text")[:30], "| desc=", attr("content-desc")[:30], "| clickable=", attr("clickable"), "| scrollable=", attr("scrollable"))
print("total:", count)
PYEOF'

echo ""
echo "==== scrollable containers ===="
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
    if "scrollable=\"true\"" in s:
        b = re.search(r"bounds=\"\[(\d+),(\d+)\]\[(\d+),(\d+)\]\"", s)
        print("scrollable:", b.group(0) if b else "?")
PYEOF'
