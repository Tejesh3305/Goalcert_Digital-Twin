"""selftest_prior.py — verify geometry-prior retrieval end-to-end."""
from pathlib import Path
from PIL import Image, ImageDraw
import trimesh

from app.store import store
from app.orchestrator import _run
from app.priors.library import library


def make_image(path, seed=0):
    img = Image.new("RGB", (768, 768), (210, 214, 222))
    d = ImageDraw.Draw(img)
    d.ellipse([180, 160, 588, 600], fill=(120, 90, 200))
    d.rectangle([300, 360, 470, 640], fill=(80, 60, 150))
    img.save(path)
    return path


def run_job(img):
    job = store.create(Path(img).name, {"object_type": "test blob", "asset_id": "T"})
    store.set_state(job["id"], "input_path", str(Path(img).resolve()))
    store.set_state(job["id"], "fields", {"object_type": "test blob"})
    _run(job["id"])
    return store.load(job["id"])


img = make_image("prior_query.png")

print("── 1) empty library ──")
j = run_job(img)
gp = next(s for s in j["stages"] if s["name"] == "geometry_prior")
rc = next(s for s in j["stages"] if s["name"] == "reconstruct")
print("  geometry_prior:", gp["note"])
print("  reconstruct   :", rc["note"])

print("\n── 2) ingest a canonical prior (image + mesh) ──")
canon = trimesh.creation.box(extents=[1, 1.4, 1]); canon.export("canon.glb")
item = library.add(img, "server_rack", "canon.glb", {"note": "seed"})
print("  added prior id:", item["id"], "asset_type=server_rack mesh=yes")

print("\n── 3) re-query with the same image → expect retrieve ──")
j = run_job("prior_query.png")
gp = next(s for s in j["stages"] if s["name"] == "geometry_prior")
rc = next(s for s in j["stages"] if s["name"] == "reconstruct")
print("  geometry_prior:", gp["note"])
print("  reconstruct   :", rc["note"])
print("  reconstruction mode:", j["state"].get("reconstruction"))
