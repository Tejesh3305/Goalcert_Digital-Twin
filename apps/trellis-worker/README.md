# TRELLIS production worker (RunPod serverless)

The upgraded GPU worker for the 2D→3D pipeline. Unlike the legacy worker
(which only accepted `{"image": b64}` and ran TRELLIS at silent defaults),
this one exposes the model's full capability:

| Capability | Legacy worker | This worker |
|---|---|---|
| Sparse-structure sampler (steps / guidance) | fixed 12 / 7.5 | `ss_sampling_steps`, `ss_guidance_strength` |
| SLAT sampler (steps / guidance) | fixed 12 / 3.0 | `slat_sampling_steps`, `slat_guidance_strength` |
| Multi-image conditioning (2–4 views) | ✗ | `images[]` + `multiimage_algo` |
| Mesh density / texture resolution | fixed 0.95 / 1024 | `mesh_simplify`, `texture_size` (up to 4096) |
| GPU-side preprocessing (rembg) | always on | `preprocess` flag (client may pre-isolate) |
| Params + timings echo | ✗ | `output.metadata` |

The orchestrator (`collins-demo/orchestrator/pipeline3d/`) already sends the
full parameter payload — the legacy worker simply ignores it, so you can
deploy this at any time with **zero client changes** and quality jumps
immediately (`standard` = 25/25 steps + 2K texture, `ultra` = 50/40 + dense mesh).

## Deploy

1. **Build & push** (needs an x86_64 Docker host with ~40 GB disk; no GPU needed to build):

   ```bash
   cd apps/trellis-worker
   docker build -t <dockerhub-user>/trellis-worker:v2 .
   docker push <dockerhub-user>/trellis-worker:v2
   ```

2. **RunPod console** → Serverless → your endpoint (`h52njo7ydmwuao`) →
   *Manage → Edit endpoint*:
   - Container image: `<dockerhub-user>/trellis-worker:v2`
   - GPU: 24 GB class (L4 / A10 / RTX 4090 / A5000). 16 GB works for
     `draft`/`standard`; `ultra` at 2K textures wants 24 GB.
   - Container disk: ≥ 30 GB.
   - **Recommended:** attach a Network Volume mounted at `/runpod-volume`
     so the ~5 GB model download (`HF_HOME=/runpod-volume/hf`) survives
     cold starts. Alternatively un-comment the model-bake line in the
     Dockerfile to ship weights inside the image.

3. **Smoke test** from the repo (uses your existing `.env` credentials):

   ```powershell
   cd collins-demo/orchestrator
   python -c "import base64, runpod_3d as r; tid, err = r.start_image_task(open('test.png','rb').read(), quality='standard'); print(tid, err)"
   # then poll r.get_task(tid) until COMPLETED — output.metadata proves the new worker
   ```

   The new worker is live when the status payload contains
   `output.metadata.params` (the legacy worker returns only `glb`).

4. Nothing else changes: `RUNPOD_API_KEY` / `RUNPOD_ENDPOINT_ID` in
   `collins-demo/orchestrator/.env` stay as they are.

## Input contract

```jsonc
{
  "input": {
    "images": ["<b64>", "..."],        // 1-4 views of ONE object (preferred)
    "image": "<b64>",                  // legacy single-image alias
    "seed": 1,
    "ss_sampling_steps": 12,           // 12 draft · 25 standard · 50 ultra
    "ss_guidance_strength": 7.5,
    "slat_sampling_steps": 12,         // 12 draft · 25 standard · 40 ultra
    "slat_guidance_strength": 3.0,
    "mesh_simplify": 0.95,             // fraction of faces removed (0.8 = denser)
    "texture_size": 1024,              // 2048 for production
    "multiimage_algo": "stochastic",   // or "multidiffusion"
    "preprocess": true                 // GPU-side rembg + recenter
  }
}
```

Output: `{"glb": "<b64>", "metadata": {params, timings_s, glb_bytes, model}}`.

## Quality guidance (what actually moves the needle)

1. **Input beats parameters.** One clean, well-lit, centred object with a
   plain background at ≥ 1024 px beats any sampler setting. The orchestrator's
   preprocess layer enforces this automatically.
2. **Multi-image is the biggest single win** for complex objects: photograph
   the object from 2–4 angles (front / side / back) and pass them together.
3. **Steps:** 12→25 is a clear improvement; 25→50 helps thin structures and
   fine texture, with diminishing returns.
4. **Out-of-scope inputs stay out of scope:** dense diagrams, whole scenes and
   maps are not single objects — those route to the plan/BIM pipelines instead.
