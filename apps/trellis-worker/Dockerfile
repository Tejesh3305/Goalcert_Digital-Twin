# TRELLIS production RunPod worker
# Build:   docker build -t <registry>/trellis-worker:v2 .
# GPU:     needs CUDA >= 12.1 at runtime; 24 GB VRAM recommended (L4/A10/4090/A5000)
FROM pytorch/pytorch:2.4.0-cuda12.1-cudnn9-devel

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    TORCH_CUDA_ARCH_LIST="8.0;8.6;8.9;9.0" \
    ATTN_BACKEND=flash-attn \
    SPCONV_ALGO=native \
    HF_HOME=/runpod-volume/hf

RUN apt-get update && apt-get install -y --no-install-recommends \
        git wget ninja-build libgl1 libglib2.0-0 build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# TRELLIS + its extension deps (mirrors the repo's setup.sh choices).
RUN git clone --recurse-submodules https://github.com/microsoft/TRELLIS.git /app/TRELLIS

RUN pip install --no-cache-dir \
        pillow imageio imageio-ffmpeg tqdm easydict opencv-python-headless \
        scipy rembg onnxruntime trimesh xatlas pyvista pymeshfix igraph \
        transformers open3d plyfile

# attention backend + sparse conv + rasterizers (the finicky part — pinned to
# the combos the TRELLIS README documents for torch 2.4 / cu121)
RUN pip install --no-cache-dir flash-attn --no-build-isolation || \
    pip install --no-cache-dir xformers==0.0.27.post2
RUN pip install --no-cache-dir spconv-cu120
RUN pip install --no-cache-dir git+https://github.com/NVlabs/nvdiffrast.git
RUN pip install --no-cache-dir git+https://github.com/JeffreyXiang/diffoctreerast.git
RUN pip install --no-cache-dir "git+https://github.com/autonomousvision/mip-splatting.git#subdirectory=submodules/diff-gaussian-rasterization"
RUN pip install --no-cache-dir kaolin -f https://nvidia-kaolin.s3.us-east-2.amazonaws.com/torch-2.4.0_cu121.html

# the serverless runtime
RUN pip install --no-cache-dir runpod

ENV PYTHONPATH=/app/TRELLIS

COPY handler.py /app/handler.py

# Optional: bake the model into the image for zero cold-start downloads
# (adds ~5 GB; otherwise it downloads to HF_HOME on first run — use a
#  RunPod network volume mounted at /runpod-volume to persist it).
# RUN python -c "from trellis.pipelines import TrellisImageTo3DPipeline as P; P.from_pretrained('microsoft/TRELLIS-image-large')"

CMD ["python", "-u", "/app/handler.py"]
