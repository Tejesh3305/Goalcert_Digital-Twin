"""selftest.py — run one object job end-to-end without the HTTP server.

Exercises the real stores: the job record goes to Postgres (or SQLite) and every
artifact is published to the blob store (S3/MinIO, or local files), so a clean
run here is evidence the 3-D pipeline works on a multi-task deploy — not just on
the machine that ran it.
"""
import sys
import time
from pathlib import Path
from PIL import Image, ImageDraw
from app.store import store
from app.orchestrator import _run   # run synchronously for the test

# Stage notes contain arrow characters. On a Windows console (cp1252) printing
# one raises UnicodeEncodeError and loses the whole report AFTER a 10-minute
# pipeline run has already succeeded. Degrade the character, never the result.
def out(line: str) -> None:
    enc = sys.stdout.encoding or "utf-8"
    print(line.encode(enc, "replace").decode(enc, "replace"))

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
out(f"\nstatus={j['status']}  route={j['state'].get('route')}  {int((time.time()-t)*1000)}ms total\n")
for s in j["stages"]:
    out(f"  {s['status']:8} {s['name']:14} {str(s['ms'])+'ms':>8}  {s.get('note','') or ''}")
out(f"\nresult_glb: {j['state'].get('result_glb')}")
out(f"exports: {j['state'].get('exports')}")
out(f"twin: {j['state'].get('twin_path')}")

# Where the artifacts actually ended up — the point of the store split.
import storage  # noqa: E402  (after the app package has fixed sys.path)
glb = j["state"].get("result_glb")
if glb:
    data = store.read_artifact(j["id"], glb)
    out(f"\nblob backend: {storage.backend()}")
    out(f"GLB readable from the store: {'yes' if data else 'NO'}"
        f"{f' ({len(data)} bytes)' if data else ''}")
