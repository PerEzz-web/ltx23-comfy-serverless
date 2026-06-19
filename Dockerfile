FROM runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    WORKSPACE=/opt \
    COMFY_ROOT=/opt/ComfyUI \
    COMFY_PORT=8188 \
    COMFY_OUTPUT_DIR=/tmp/comfyui-output \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore \
    PYTHONNOUSERSITE=1 \
    TORCH_VERSION=2.8.0 \
    TORCHVISION_VERSION=0.23.0 \
    TORCHAUDIO_VERSION=2.8.0 \
    CUDA_TAG=cu128 \
    TORCH_INDEX=https://download.pytorch.org/whl/cu128 \
    XFORMERS_VERSION=0.0.32.post2

WORKDIR /opt

RUN apt-get update -y && \
    apt-get install -y --no-install-recommends \
      git git-lfs curl wget ca-certificates ffmpeg \
      python3 python3-venv python3-dev python3-pip \
      build-essential cmake ninja-build pkg-config \
      libgl1 libglib2.0-0 libsm6 libxext6 libxrender1 libgomp1 \
      psmisc lsof && \
    git lfs install || true

COPY LTX-2-3-AUTO_INSTALL-RUNPOD-V2.sh /tmp/LTX-2-3-AUTO_INSTALL-RUNPOD-V2.sh
COPY scripts/patch_ltx_blackwell_serverless.py /tmp/patch_ltx_blackwell_serverless.py

# Install ComfyUI, custom nodes, and Python dependencies, but NOT model files.
RUN set -eux; \
    chmod +x /tmp/LTX-2-3-AUTO_INSTALL-RUNPOD-V2.sh; \
    python3 /tmp/patch_ltx_blackwell_serverless.py /tmp/LTX-2-3-AUTO_INSTALL-RUNPOD-V2.sh; \
    WORKSPACE=/opt \
    COMFY_ROOT=/opt/ComfyUI \
    PYTHON_BIN=python3 \
    RECREATE_VENV=true \
    RESET_REQUIRED_NODES=true \
    INSTALL_MODELS=false \
    VERIFY_TORCH_CUDA=false \
    VERIFY_LTX_IMPORT=false \
    bash /tmp/LTX-2-3-AUTO_INSTALL-RUNPOD-V2.sh \
      2>&1 | tee /tmp/ltx_install.log \
    || (echo "==== LAST 300 LTX INSTALL LOG LINES ===="; tail -300 /tmp/ltx_install.log; exit 1)

# Extra runtime deps: RunPod SDK, S3 upload/signing, and ComfyUI DB deps.
RUN /opt/ComfyUI/venv/bin/python -m pip install --no-cache-dir --no-input --prefer-binary \
      runpod boto3 requests \
      "SQLAlchemy>=2.0" alembic aiosqlite && \
    mkdir -p /opt/ComfyUI/models/unet \
             /opt/ComfyUI/models/vae \
             /opt/ComfyUI/models/text_encoders \
             /opt/ComfyUI/models/latent_upscale_models \
             /opt/ComfyUI/models/loras \
             /opt/ComfyUI/input \
             /tmp/comfyui-output \
             /app/workflows && \
    mkdir -p /opt/ComfyUI/custom_nodes_disabled && \
    mv /opt/ComfyUI/custom_nodes/ComfyUI-Manager /opt/ComfyUI/custom_nodes_disabled/ 2>/dev/null || true && \
    mv /opt/ComfyUI/custom_nodes/ComfyUI-WanVideoWrapper /opt/ComfyUI/custom_nodes_disabled/ 2>/dev/null || true && \
    mv /opt/ComfyUI/custom_nodes/ComfyUI-Impact-Pack /opt/ComfyUI/custom_nodes_disabled/ 2>/dev/null || true && \
    rm -rf /root/.cache/pip /var/lib/apt/lists/* /tmp/LTX-2-3-AUTO_INSTALL-RUNPOD-V2.sh

COPY workflows/LTX_I2V_API.json /app/workflows/LTX_I2V_API.json
COPY handler.py /app/handler.py

ENTRYPOINT []
CMD ["/opt/ComfyUI/venv/bin/python", "-u", "/app/handler.py"]
