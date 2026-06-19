#!/usr/bin/env bash
# Clean RunPod ComfyUI LTX-2.3 V2 installer by Aitrepreneur
# Designed for fresh or broken RunPod installs.
# Main rule: Torch is installed once and protected from custom node requirements.

set -euo pipefail

# ============================================================
# Config
# ============================================================

WORKSPACE="${WORKSPACE:-/workspace}"
COMFY_ROOT="${COMFY_ROOT:-$WORKSPACE/ComfyUI}"
COMFY_REPO="${COMFY_REPO:-https://github.com/comfyanonymous/ComfyUI.git}"
COMFY_REF="${COMFY_REF:-v0.21.1}"

HF_BASE="${HF_BASE:-https://huggingface.co/Aitrepreneur/FLX/resolve/main}"
MODEL_VERSION="${MODEL_VERSION:-Q8_0}"

PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_DIR="${VENV_DIR:-venv}"

# Clean means clean. This recreates the Python venv.
RECREATE_VENV="${RECREATE_VENV:-true}"

# This removes and reclones only the required custom nodes used by this installer.
RESET_REQUIRED_NODES="${RESET_REQUIRED_NODES:-true}"

# Torch / CUDA
TORCH_VERSION="${TORCH_VERSION:-2.4.0}"
TORCHVISION_VERSION="${TORCHVISION_VERSION:-0.19.0}"
TORCHAUDIO_VERSION="${TORCHAUDIO_VERSION:-2.4.0}"
CUDA_TAG="${CUDA_TAG:-cu121}"
TORCH_INDEX="${TORCH_INDEX:-https://download.pytorch.org/whl/${CUDA_TAG}}"
XFORMERS_VERSION="${XFORMERS_VERSION:-0.0.27.post2}"

# LTXVideo pin from the workflow-compatible version
LTXVIDEO_REF="${LTXVIDEO_REF:-cd5d371518afb07d6b3641be8012f644f25269fc}"

# Python package pins
PIN_TRANSFORMERS_VERSION="${PIN_TRANSFORMERS_VERSION:-4.51.3}"
PIN_TOKENIZERS_SPEC="${PIN_TOKENIZERS_SPEC:-tokenizers>=0.21,<0.22}"
PIN_TIMM_VERSION="${PIN_TIMM_VERSION:-1.0.15}"
PIN_OPENCV_HEADLESS="${PIN_OPENCV_HEADLESS:-4.12.0.88}"
PIN_PILLOW_MIN="${PIN_PILLOW_MIN:-11.0.0}"
PIN_NUMPY_SPEC="${PIN_NUMPY_SPEC:-numpy>=1.26,<3}"

# Optional extras
PIN_LIBROSA_VERSION="${PIN_LIBROSA_VERSION:-}"

# Safety switches
VERIFY_LTX_IMPORT="${VERIFY_LTX_IMPORT:-true}"
INSTALL_MODELS="${INSTALL_MODELS:-true}"

export PIP_DISABLE_PIP_VERSION_CHECK=1
export PIP_ROOT_USER_ACTION=ignore
export PYTHONNOUSERSITE=1

# ============================================================
# Helpers
# ============================================================

[[ "$(id -u)" -eq 0 ]] && SUDO="" || SUDO="sudo"

log() {
  echo
  echo "============================================================"
  echo "$1"
  echo "============================================================"
}

die() {
  echo
  echo "[ERROR] $*"
  exit 1
}

need_pkg() {
  command -v "$1" &>/dev/null && return 0

  echo "[INFO] Installing system package: $1"
  $SUDO apt-get update -y
  $SUDO apt-get install -y "$1"
}

grab() {
  local out="$1"
  local url="$2"

  if [[ -f "$out" ]]; then
    echo " [SKIP] $(basename "$out") already exists"
    return 0
  fi

  echo " [DL] $(basename "$out")"
  mkdir -p "$(dirname "$out")"
  curl -L --fail --progress-bar --show-error -o "$out" "$url"
}

ensure_clean_dir_removed() {
  local dir="$1"

  if [[ -d "$dir" ]]; then
    echo " [CLEAN] Removing $dir"
    rm -rf "$dir"
  fi
}

checkout_repo() {
  local dir="$1"
  local url="$2"
  local ref="${3:-}"

  if [[ -d "$dir/.git" ]]; then
    echo " [GIT] Updating existing repo: $dir"
    git -C "$dir" fetch --all --tags || true
  elif [[ -d "$dir" ]]; then
    echo " [WARN] $dir exists but is not a git repo. Replacing it."
    rm -rf "$dir"
    git clone "$url" "$dir"
    git -C "$dir" fetch --all --tags || true
  else
    echo " [GIT] Cloning $url -> $dir"
    git clone "$url" "$dir"
    git -C "$dir" fetch --all --tags || true
  fi

  if [[ -n "$ref" ]]; then
    echo " [GIT] Checking out $ref"
    git -C "$dir" checkout "$ref"
  else
    git -C "$dir" pull --ff-only || true
  fi
}

clone_node() {
  local dir="$1"
  local url="$2"
  local ref="${3:-}"

  local target="$COMFY_ROOT/custom_nodes/$dir"

  if [[ "$RESET_REQUIRED_NODES" == "true" ]]; then
    ensure_clean_dir_removed "$target"
  fi

  checkout_repo "$target" "$url" "$ref"
}

make_constraints() {
  CONSTRAINT_FILE="/tmp/ait_ltx23_constraints.txt"

  cat > "$CONSTRAINT_FILE" <<EOF
torch==${TORCH_VERSION}
torchvision==${TORCHVISION_VERSION}
torchaudio==${TORCHAUDIO_VERSION}
xformers==${XFORMERS_VERSION}
transformers==${PIN_TRANSFORMERS_VERSION}
${PIN_TOKENIZERS_SPEC}
timm==${PIN_TIMM_VERSION}
opencv-python-headless==${PIN_OPENCV_HEADLESS}
pillow>=${PIN_PILLOW_MIN}
${PIN_NUMPY_SPEC}
EOF

  echo "$CONSTRAINT_FILE"
}

sanitize_requirements() {
  local input="$1"
  local output="$2"

  python - "$input" "$output" <<'PY'
import re
import sys
from pathlib import Path

src = Path(sys.argv[1])
dst = Path(sys.argv[2])

blocked_prefixes = [
    "torch",
    "torchvision",
    "torchaudio",
    "xformers",
    "triton",
    "transformers",
    "tokenizers",
    "timm",
    "opencv-python",
    "opencv-contrib-python",
    "opencv-python-headless",
    "sageattention",
]

blocked_contains = [
    "github.com/facebookresearch/sam2",
]

def should_block(line: str) -> bool:
    stripped = line.strip()
    lower = stripped.lower()

    if not stripped or lower.startswith("#"):
        return False

    for item in blocked_contains:
        if item in lower:
            return True

    if lower.startswith("nvidia-"):
        return True

    for prefix in blocked_prefixes:
        if re.match(rf"^(-e\s+)?{re.escape(prefix)}(\[|==|>=|<=|~=|!=|>|<|\s|$)", lower):
            return True

    return False

out = []
for raw in src.read_text(encoding="utf-8", errors="ignore").splitlines():
    if should_block(raw):
        out.append("# skipped by Aitrepreneur installer to protect Torch stack: " + raw)
    else:
        out.append(raw)

dst.write_text("\n".join(out) + "\n", encoding="utf-8")
PY
}

install_req_sanitized() {
  local req="$1"
  local label="$2"

  [[ -f "$req" ]] || {
    echo " [SKIP] No requirements file for $label"
    return 0
  }

  local sanitized="/tmp/$(basename "$(dirname "$req")")_$(basename "$req").sanitized.txt"

  sanitize_requirements "$req" "$sanitized"

  echo " [REQ] Installing sanitized requirements for $label"
  echo "       Original:  $req"
  echo "       Sanitized: $sanitized"

  python -m pip install --no-input --prefer-binary \
    --upgrade-strategy only-if-needed \
    --constraint "$CONSTRAINT_FILE" \
    -r "$sanitized"
}

install_safe_package_set() {
  log "Installing safe pinned Python packages"

  python -m pip install --no-input --prefer-binary \
    --constraint "$CONSTRAINT_FILE" \
    "${PIN_NUMPY_SPEC}" \
    "pillow>=${PIN_PILLOW_MIN}" \
    "opencv-python-headless==${PIN_OPENCV_HEADLESS}" \
    "safetensors>=0.4.3" \
    "huggingface_hub>=0.25.2,<1.0" \
    "accelerate>=0.34.0" \
    "filelock" \
    "packaging" \
    "pyyaml" \
    "regex" \
    "requests" \
    "tqdm"

  echo " [PIN] Installing Transformers without dependencies"
  python -m pip install --no-input --no-deps \
    "transformers==${PIN_TRANSFORMERS_VERSION}"

  echo " [PIN] Installing Tokenizers with constraints"
  python -m pip install --no-input --prefer-binary \
    --constraint "$CONSTRAINT_FILE" \
    "${PIN_TOKENIZERS_SPEC}"

  echo " [PIN] Installing timm without dependencies"
  python -m pip install --no-input --no-deps \
    "timm==${PIN_TIMM_VERSION}"
}

verify_torch() {
  log "Verifying Torch CUDA"

  python - <<PY
import sys
import torch

print("torch:", torch.__version__)
print("torch cuda:", torch.version.cuda)
print("cuda available:", torch.cuda.is_available())

expected_torch_prefix = "${TORCH_VERSION}"
expected_cuda = "12.1"

if not torch.__version__.startswith(expected_torch_prefix):
    raise SystemExit(f"[ERROR] Wrong torch version: {torch.__version__}, expected {expected_torch_prefix}")

if torch.version.cuda != expected_cuda:
    raise SystemExit(f"[ERROR] Wrong torch CUDA version: {torch.version.cuda}, expected {expected_cuda}")

if not torch.cuda.is_available():
    raise SystemExit("[ERROR] CUDA is not available. This usually means wrong Torch build or incompatible RunPod image.")

print("gpu:", torch.cuda.get_device_name(0))
print("[OK] Torch CUDA is good")
PY
}

verify_no_bad_cuda_packages() {
  log "Checking for bad CUDA 13 packages"

  if python -m pip freeze | grep -Ei "nvidia-.*cu13|torch==.*cu13" >/tmp/ait_bad_cuda.txt; then
    cat /tmp/ait_bad_cuda.txt
    die "Detected CUDA 13 packages. This would break older RunPod drivers."
  fi

  echo "[OK] No CUDA 13 packages detected"
}

verify_ltx_import() {
  [[ "$VERIFY_LTX_IMPORT" == "true" ]] || return 0

  log "Verifying ComfyUI-LTXVideo import"

  cd "$COMFY_ROOT"

  python - <<'PY'
import importlib.util
import sys
from pathlib import Path

root = Path.cwd()
pkg_dir = root / "custom_nodes" / "ComfyUI-LTXVideo"
init_file = pkg_dir / "__init__.py"

if not init_file.exists():
    raise SystemExit("[ERROR] ComfyUI-LTXVideo __init__.py not found")

sys.path.insert(0, str(root))
sys.path.insert(0, str(root / "custom_nodes"))

spec = importlib.util.spec_from_file_location(
    "ComfyUI_LTXVideo",
    init_file,
    submodule_search_locations=[str(pkg_dir)],
)

module = importlib.util.module_from_spec(spec)
sys.modules["ComfyUI_LTXVideo"] = module
spec.loader.exec_module(module)

print("[OK] ComfyUI-LTXVideo imports successfully")
PY
}

# ============================================================
# System packages
# ============================================================

log "Installing system packages"

need_pkg curl
need_pkg git
need_pkg git-lfs
need_pkg ffmpeg
need_pkg python3-venv

git lfs install || true

# ============================================================
# ComfyUI source
# ============================================================

log "Preparing ComfyUI"

mkdir -p "$WORKSPACE"

if [[ -d "$COMFY_ROOT/.git" ]]; then
  echo " [OK] ComfyUI repo exists: $COMFY_ROOT"
elif [[ -d "$COMFY_ROOT" ]]; then
  echo " [WARN] $COMFY_ROOT exists but is not a git repo. Replacing it."
  rm -rf "$COMFY_ROOT"
fi

checkout_repo "$COMFY_ROOT" "$COMFY_REPO" "$COMFY_REF"

cd "$COMFY_ROOT"

mkdir -p models custom_nodes

# ============================================================
# Clean venv
# ============================================================

log "Creating clean Python venv"

if [[ "$RECREATE_VENV" == "true" ]]; then
  ensure_clean_dir_removed "$COMFY_ROOT/$VENV_DIR"
fi

if [[ ! -d "$COMFY_ROOT/$VENV_DIR" ]]; then
  "$PYTHON_BIN" -m venv "$COMFY_ROOT/$VENV_DIR"
fi

# shellcheck disable=SC1091
source "$COMFY_ROOT/$VENV_DIR/bin/activate"

PYTHON="$(command -v python)"
PIP="$(command -v pip)"

echo "Python: $PYTHON"
echo "Pip:    $PIP"

python -m pip install --no-input --upgrade pip setuptools wheel

CONSTRAINT_FILE="$(make_constraints)"
echo "Constraints: $CONSTRAINT_FILE"

# ============================================================
# Torch stack
# ============================================================

log "Installing locked Torch cu121 stack"

python -m pip uninstall -y \
  torch torchvision torchaudio xformers triton \
  nvidia-cublas-cu13 \
  nvidia-cuda-cupti-cu13 \
  nvidia-cuda-nvrtc-cu13 \
  nvidia-cuda-runtime-cu13 \
  nvidia-cudnn-cu13 \
  nvidia-cufft-cu13 \
  nvidia-curand-cu13 \
  nvidia-cusolver-cu13 \
  nvidia-cusparse-cu13 \
  nvidia-cusparselt-cu13 \
  nvidia-nccl-cu13 \
  nvidia-nvjitlink-cu13 \
  nvidia-nvshmem-cu13 \
  nvidia-nvtx-cu13 || true

python -m pip install --no-input --no-cache-dir \
  "torch==${TORCH_VERSION}" \
  "torchvision==${TORCHVISION_VERSION}" \
  "torchaudio==${TORCHAUDIO_VERSION}" \
  --index-url "$TORCH_INDEX"

python -m pip install --no-input --no-cache-dir --no-deps \
  "xformers==${XFORMERS_VERSION}"

verify_torch
verify_no_bad_cuda_packages

# ============================================================
# ComfyUI requirements
# ============================================================

log "Installing ComfyUI requirements safely"

install_safe_package_set

install_req_sanitized "$COMFY_ROOT/requirements.txt" "ComfyUI core"

if [[ -f "$COMFY_ROOT/manager_requirements.txt" ]]; then
  install_req_sanitized "$COMFY_ROOT/manager_requirements.txt" "ComfyUI manager requirements"
fi

# Re-apply pins after ComfyUI requirements
install_safe_package_set
verify_torch
verify_no_bad_cuda_packages

# ============================================================
# Models
# ============================================================

if [[ "$INSTALL_MODELS" == "true" ]]; then
  log "Downloading LTX-2.3 model files"

  grab "models/text_encoders/ltx-2.3_text_projection_bf16.safetensors" \
       "$HF_BASE/ltx-2.3_text_projection_bf16.safetensors?download=true"

  grab "models/text_encoders/gemma_3_12B_it_fp4_mixed.safetensors" \
       "$HF_BASE/gemma_3_12B_it_fp4_mixed.safetensors?download=true"

  grab "models/vae/LTX23_audio_vae_bf16.safetensors" \
       "$HF_BASE/LTX23_audio_vae_bf16.safetensors?download=true"

  grab "models/vae/LTX23_video_vae_bf16.safetensors" \
       "$HF_BASE/LTX23_video_vae_bf16.safetensors?download=true"

  grab "models/unet/ltx-2.3-22b-dev-${MODEL_VERSION}.gguf" \
       "$HF_BASE/ltx-2.3-22b-dev-${MODEL_VERSION}.gguf?download=true"

  grab "models/latent_upscale_models/ltx-2.3-spatial-upscaler-x2-1.1.safetensors" \
       "$HF_BASE/ltx-2.3-spatial-upscaler-x2-1.1.safetensors?download=true"

  grab "models/loras/ltx-2.3-22b-distilled-lora-384-1.1.safetensors" \
       "$HF_BASE/ltx-2.3-22b-distilled-lora-384-1.1.safetensors?download=true"

  grab "models/loras/ltx-2-19b-ic-lora-detailer.safetensors" \
       "$HF_BASE/ltx-2-19b-ic-lora-detailer.safetensors?download=true"
else
  echo " [SKIP] INSTALL_MODELS=false"
fi

# ============================================================
# Custom nodes
# ============================================================

log "Installing custom nodes"

mkdir -p "$COMFY_ROOT/custom_nodes"

clone_node "ComfyUI-Manager"          "https://github.com/ltdrdata/ComfyUI-Manager.git"
clone_node "ComfyUI-GGUF"             "https://github.com/city96/ComfyUI-GGUF.git"
clone_node "rgthree-comfy"            "https://github.com/rgthree/rgthree-comfy.git"
clone_node "ComfyUI-Easy-Use"         "https://github.com/yolain/ComfyUI-Easy-Use.git"
clone_node "ComfyUI-KJNodes"          "https://github.com/kijai/ComfyUI-KJNodes.git"
clone_node "RES4LYF"                  "https://github.com/ClownsharkBatwing/RES4LYF.git"
clone_node "ComfyUI-LTXVideo"         "https://github.com/Lightricks/ComfyUI-LTXVideo.git" "$LTXVIDEO_REF"
clone_node "ComfyUI-Custom-Scripts"   "https://github.com/pythongosssss/ComfyUI-Custom-Scripts.git"
clone_node "ComfyUI-VideoHelperSuite" "https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite.git"
clone_node "ComfyUI-WanVideoWrapper"  "https://github.com/kijai/ComfyUI-WanVideoWrapper.git"
clone_node "ComfyUI-Impact-Pack"      "https://github.com/ltdrdata/ComfyUI-Impact-Pack.git"
clone_node "Comfyui_TTP_Toolset"      "https://github.com/TTPlanetPig/Comfyui_TTP_Toolset.git"
clone_node "ComfyMath"                "https://github.com/evanspearman/ComfyMath.git"
clone_node "WhatDreamsCost-ComfyUI"   "https://github.com/WhatDreamsCost/WhatDreamsCost-ComfyUI.git"

# ============================================================
# Custom node requirements
# ============================================================

log "Installing custom node requirements safely"

REQUIRED_NODE_DIRS=(
  "ComfyUI-Manager"
  "ComfyUI-GGUF"
  "rgthree-comfy"
  "ComfyUI-Easy-Use"
  "ComfyUI-KJNodes"
  "RES4LYF"
  "ComfyUI-LTXVideo"
  "ComfyUI-Custom-Scripts"
  "ComfyUI-VideoHelperSuite"
  "ComfyUI-WanVideoWrapper"
  "ComfyUI-Impact-Pack"
  "Comfyui_TTP_Toolset"
  "ComfyMath"
  "WhatDreamsCost-ComfyUI"
)

for node_dir in "${REQUIRED_NODE_DIRS[@]}"; do
  install_req_sanitized "$COMFY_ROOT/custom_nodes/$node_dir/requirements.txt" "$node_dir"
done

# ============================================================
# Known useful dependencies
# ============================================================

log "Installing known useful dependencies safely"

python -m pip install --no-input --prefer-binary \
  --constraint "$CONSTRAINT_FILE" \
  boto3 \
  rotary-embedding-torch \
  deepdiff \
  py-cpuinfo \
  diffusers \
  gguf \
  piexif \
  einops \
  sentencepiece \
  protobuf \
  av \
  imageio \
  imageio-ffmpeg \
  soundfile

if [[ -n "$PIN_LIBROSA_VERSION" ]]; then
  python -m pip install --no-input --prefer-binary \
    --constraint "$CONSTRAINT_FILE" \
    "librosa==${PIN_LIBROSA_VERSION}"
else
  python -m pip install --no-input --prefer-binary \
    --constraint "$CONSTRAINT_FILE" \
    librosa
fi

# Re-apply the important pins one final time
install_safe_package_set

# ============================================================
# Final verification
# ============================================================

verify_torch
verify_no_bad_cuda_packages

log "Verifying pinned package versions"

python - <<PY
import transformers
import tokenizers
import timm

print("transformers:", transformers.__version__)
print("tokenizers:", tokenizers.__version__)
print("timm:", timm.__version__)

if transformers.__version__ != "${PIN_TRANSFORMERS_VERSION}":
    raise SystemExit("[ERROR] Wrong transformers version")

print("[OK] Python package pins are good")
PY

verify_ltx_import

# ============================================================
# Done
# ============================================================

log "Install complete"

echo "✅ Clean LTX-2.3 RunPod install is ready."