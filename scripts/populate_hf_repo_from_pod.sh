#!/usr/bin/env bash
set -euo pipefail

# Usage:
#   export HF_TOKEN=hf_...
#   export HF_REPO=your-hf-username/ltx23-comfy-models
#   bash scripts/populate_hf_repo_from_pod.sh
#
# Run this on the RunPod pod where /workspace/ComfyUI/models already contains
# the LTX files. It creates a Hugging Face repo layout that RunPod can cache as
# one cached model.

: "${HF_TOKEN:?Set HF_TOKEN to a Hugging Face write token}"
: "${HF_REPO:?Set HF_REPO to username/repo-name}"

COMFY_MODELS_DIR="${COMFY_MODELS_DIR:-/workspace/ComfyUI/models}"
STAGE_DIR="${STAGE_DIR:-/workspace/hf_ltx23_comfy_models}"

python3 -m pip install -U "huggingface_hub[hf_xet]"
hf auth login --token "$HF_TOKEN"

python3 - <<PY
from huggingface_hub import HfApi
repo_id = "${HF_REPO}"
api = HfApi(token="${HF_TOKEN}")
api.create_repo(repo_id=repo_id, repo_type="model", private=True, exist_ok=True)
print(f"Repo ready: https://huggingface.co/{repo_id}")
PY

rm -rf "$STAGE_DIR"
mkdir -p \
  "$STAGE_DIR/unet" \
  "$STAGE_DIR/vae" \
  "$STAGE_DIR/text_encoders" \
  "$STAGE_DIR/latent_upscale_models" \
  "$STAGE_DIR/loras"

# cp -al creates hardlinks on the same filesystem, so this normally does not
# duplicate the huge model files on disk. If hardlinks fail, replace cp -al with cp -a.
cp -al "$COMFY_MODELS_DIR/unet/ltx-2.3-22b-dev-Q8_0.gguf" \
  "$STAGE_DIR/unet/"
cp -al "$COMFY_MODELS_DIR/vae/LTX23_audio_vae_bf16.safetensors" \
  "$STAGE_DIR/vae/"
cp -al "$COMFY_MODELS_DIR/vae/LTX23_video_vae_bf16.safetensors" \
  "$STAGE_DIR/vae/"
cp -al "$COMFY_MODELS_DIR/text_encoders/gemma_3_12B_it_fp4_mixed.safetensors" \
  "$STAGE_DIR/text_encoders/"
cp -al "$COMFY_MODELS_DIR/text_encoders/ltx-2.3_text_projection_bf16.safetensors" \
  "$STAGE_DIR/text_encoders/"
cp -al "$COMFY_MODELS_DIR/latent_upscale_models/ltx-2.3-spatial-upscaler-x2-1.1.safetensors" \
  "$STAGE_DIR/latent_upscale_models/"
cp -al "$COMFY_MODELS_DIR/loras/ltx-2.3-22b-distilled-lora-384-1.1.safetensors" \
  "$STAGE_DIR/loras/"
cp -al "$COMFY_MODELS_DIR/loras/ltx-2-19b-ic-lora-detailer.safetensors" \
  "$STAGE_DIR/loras/"

cat > "$STAGE_DIR/README.md" <<'EOF'
# LTX 2.3 ComfyUI model bundle

This repository is structured for a RunPod cached-model Serverless worker.
It contains the exact folders expected by the ComfyUI workflow:

- unet/
- vae/
- text_encoders/
- latent_upscale_models/
- loras/

The Docker image does not include these files. RunPod caches this Hugging Face
repository and the worker symlinks these files into /opt/ComfyUI/models at startup.
EOF

export HF_XET_HIGH_PERFORMANCE=1
hf upload-large-folder "$HF_REPO" "$STAGE_DIR"

echo
printf 'Done. In RunPod Endpoint settings, set Model to: %s\n' "$HF_REPO"
