"""selftest_drawing.py — run one drawing job through the sibling 2d-to-3d parser."""
from pathlib import Path
from PIL import Image, ImageDraw
from app.store import store
from app.orchestrator import _run

img = Image.new("RGB", (900, 700), (255, 255, 255))
d = ImageDraw.Draw(img)
for r in [(40, 40, 860, 660), (40, 40, 450, 360), (450, 360, 860, 660)]:
    d.rectangle(r, outline=(0, 0, 0), width=3)
d.text((120, 180), "WARD 16.88X7.26", fill=(0, 0, 0))
p = Path("selftest_plan.png"); img.save(p)

job = store.create("selftest_plan.png", {"route": "drawing", "facility": "hospital"})
store.set_state(job["id"], "input_path", str(p.resolve()))
store.set_state(job["id"], "fields", {"route": "drawing", "facility": "hospital"})
_run(job["id"])

j = store.load(job["id"])
print("status=", j["status"], " route=", j["state"].get("route"))
for s in j["stages"]:
    print(f"  {s['status']:8} {s['name']:16} {s.get('note','')}")
print("scene_path=", j["state"].get("scene_path"))
print("error=", j.get("error"))
