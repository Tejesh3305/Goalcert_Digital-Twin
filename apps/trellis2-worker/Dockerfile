# TRELLIS.2 production RunPod serverless worker
#
# Build:  docker build -t <registry>/trellis2-worker:v1 .
# GPU:    CUDA >= 12.4 at runtime. Upstream requires >= 24 GB VRAM.
#         Verified upstream on A100/H100; L40S/A6000 (48 GB) also fit.
#         A 24 GB card (L4/A10/4090) is the documented floor and is tight at
#         1536³ — use 1024 there.
#
# The environment is built by UPSTREAM'S OWN setup.sh with the exact flag set
# from their README, rather than a hand-written pip list. Their extensions
# (o_voxel, cumesh, flexgemm, nvdiffrec) are custom CUDA/Triton builds pinned to
# specific commits; transcribing them by hand is how a "replication" silently
# drifts from the thing it replicates.
FROM pytorch/pytorch:2.6.0-cuda12.4-cudnn9-devel

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    TORCH_CUDA_ARCH_LIST="8.0;8.6;8.9;9.0" \
    OPENCV_IO_ENABLE_OPENEXR=1 \
    PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True \
    ATTN_BACKEND=flash-attn \
    SPCONV_ALGO=native \
    HF_HOME=/runpod-volume/hf

# build-essential/ninja: the CUDA extensions compile from source.
# libjpeg-dev: required by pillow-simd, which setup.sh --basic installs.
RUN apt-get update && apt-get install -y --no-install-recommends \
        git wget ninja-build build-essential libjpeg-dev \
        libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN git clone --recursive -b main https://github.com/microsoft/TRELLIS.2.git /app/TRELLIS.2

WORKDIR /app/TRELLIS.2

# --new-env is deliberately omitted: it would create a fresh conda env with its
# own torch, discarding this image's CUDA-matched build. Every other flag is
# upstream's documented set. Capping MAX_JOBS keeps the flash-attn compile from
# exhausting builder RAM (it is the memory-hungriest step by a wide margin).
RUN MAX_JOBS=4 bash -lc ". ./setup.sh --basic --flash-attn --nvdiffrast --nvdiffrec --cumesh --o-voxel --flexgemm"

# the serverless runtime
RUN pip install --no-cache-dir runpod

ENV PYTHONPATH=/app/TRELLIS.2

COPY handler.py /app/handler.py

# Import-check the pieces the handler needs, so a broken extension build fails
# HERE rather than on the first paid GPU invocation.
RUN python -c "import o_voxel, trellis2; from trellis2.pipelines import Trellis2ImageTo3DPipeline; print('trellis2 imports OK')"

# Optional: bake the 4B checkpoint into the image for zero cold-start download
# (adds ~8-16 GB). Otherwise it downloads to HF_HOME on first run — mount a
# RunPod network volume at /runpod-volume to persist it across workers.
# RUN python -c "from trellis2.pipelines import Trellis2ImageTo3DPipeline as P; P.from_pretrained('microsoft/TRELLIS.2-4B')"

CMD ["python", "-u", "/app/handler.py"]
