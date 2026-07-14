"""selftest.py — run one object job end-to-end without the HTTP server."""
import time
from pathlib import Path
from PIL import Image, ImageDraw
from app.store import store
from app.orchestrator import _run   # run synchronously for the test

# make a simple object-ish photo (coloured shape on a plain background)
img = Image.new("RGB", (768, 768), (210, 214, 222))
d = ImageDraw.Draw(img)
d.ellipse([180, 160, 588, 600], fill=(120, 90, 200))
d.rectangle([300, 360, 470, 640], fill=(80, 60, 150))
p = Path("selftest_input.png"); img.save(p)

job = store.create("selftest_input.png", {"object_type": "test blob", "asset_id": "TEST-1"})
store.set_state(job["id"], "input_path", str(p.resolve()))
store.set_state(job["id"], "filename", "selftest_input.png")
store.set_state(job["id"], "fields", {"object_type": "test blob", "asset_id": "TEST-1"})

t = time.time()
_run(job["id"])
j = store.load(job["id"])
print(f"\nstatus={j['status']}  route={j['state'].get('route')}  {int((time.time()-t)*1000)}ms total\n")
for s in j["stages"]:
    print(f"  {s['status']:8} {s['name']:14} {str(s['ms'])+'ms':>8}  {s.get('note','')}")
print("\nresult_glb:", j["state"].get("result_glb"))
print("exports:", j["state"].get("exports"))
print("twin:", j["state"].get("twin_path"))
