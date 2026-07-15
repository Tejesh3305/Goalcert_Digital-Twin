"""ingest_priors.py — bulk-load the geometry-prior library from a folder.

Layout: one subfolder per asset type; images inside; optional same-named mesh.

    priors_src/
        server_rack/  rack1.jpg  rack1.glb   # mesh optional, matched by stem
        mri_scanner/  mri_a.png  mri_b.png
        crac_unit/    crac.jpg

Usage:
    python ingest_priors.py path/to/priors_src

High-confidence matches to an entry that has a canonical mesh are returned
verbatim by the pipeline (deterministic consistency); entries without a mesh
still act as conditioning hints to TRELLIS.
"""
import sys
from pathlib import Path

from app.priors.library import library

IMG_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
MESH_EXT = {".glb", ".gltf", ".obj", ".ply", ".stl"}


def main(src: str):
    root = Path(src)
    if not root.is_dir():
        print(f"not a folder: {root}")
        return
    added = 0
    for sub in sorted(p for p in root.iterdir() if p.is_dir()):
        asset_type = sub.name
        meshes = {p.stem: p for p in sub.iterdir() if p.suffix.lower() in MESH_EXT}
        for img in sorted(p for p in sub.iterdir() if p.suffix.lower() in IMG_EXT):
            mesh = meshes.get(img.stem)
            item = library.add(img, asset_type, mesh, {"source": str(img)})
            added += 1
            print(f"  + {asset_type:18} {img.name:24} mesh={'yes' if mesh else '-'}  id={item['id']}")
    info = library.list()
    print(f"\nDone. added={added}  library now has {info['count']} priors "
          f"(method={info['method']}).")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python ingest_priors.py <folder-of-asset-subfolders>")
    else:
        main(sys.argv[1])
