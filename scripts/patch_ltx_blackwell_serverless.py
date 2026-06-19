#!/usr/bin/env python3
"""Patch the Aitrepreneur LTX installer for RunPod Blackwell/serverless builds."""
from pathlib import Path
import re
import sys

path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("LTX-2-3-AUTO_INSTALL-RUNPOD-V2.sh")
s = path.read_text(encoding="utf-8")

replacements = {
    'TORCH_VERSION="${TORCH_VERSION:-2.4.0}"': 'TORCH_VERSION="${TORCH_VERSION:-2.8.0}"',
    'TORCHVISION_VERSION="${TORCHVISION_VERSION:-0.19.0}"': 'TORCHVISION_VERSION="${TORCHVISION_VERSION:-0.23.0}"',
    'TORCHAUDIO_VERSION="${TORCHAUDIO_VERSION:-2.4.0}"': 'TORCHAUDIO_VERSION="${TORCHAUDIO_VERSION:-2.8.0}"',
    'CUDA_TAG="${CUDA_TAG:-cu121}"': 'CUDA_TAG="${CUDA_TAG:-cu128}"',
    'XFORMERS_VERSION="${XFORMERS_VERSION:-0.0.27.post2}"': 'XFORMERS_VERSION="${XFORMERS_VERSION:-0.0.32.post2}"',
    'expected_cuda = "12.1"': 'expected_cuda = "12.8"',
}
for old, new in replacements.items():
    if old not in s:
        print(f"[WARN] exact text not found, maybe already patched: {old}")
    s = s.replace(old, new)

s = s.replace(
    'python -m pip install --no-input --no-cache-dir --no-deps \\\n  "xformers==${XFORMERS_VERSION}"',
    'python -m pip install --no-input --no-cache-dir --no-deps \\\n  "xformers==${XFORMERS_VERSION}" \\\n  --index-url "$TORCH_INDEX"'
)

pattern = re.compile(r"verify_torch\(\) \{.*?^\}", flags=re.S | re.M)
match = pattern.search(s)
if not match:
    raise SystemExit("Could not find verify_torch() function to patch")

new_verify = r'''verify_torch() {
  log "Verifying Torch CUDA"

  python - <<PY
import os
import torch

print("torch:", torch.__version__)
print("torch cuda:", torch.version.cuda)
print("cuda available:", torch.cuda.is_available())

expected_torch_prefix = "${TORCH_VERSION}"
expected_cuda = "12.8"

if not torch.__version__.startswith(expected_torch_prefix):
    raise SystemExit(f"[ERROR] Wrong torch version: {torch.__version__}, expected {expected_torch_prefix}")

if torch.version.cuda != expected_cuda:
    raise SystemExit(f"[ERROR] Wrong torch CUDA version: {torch.version.cuda}, expected {expected_cuda}")

skip_runtime_cuda = os.environ.get("VERIFY_TORCH_CUDA", "true").lower() in {"0", "false", "no", "skip"}
if skip_runtime_cuda:
    print("[OK] Torch package versions are good; runtime CUDA check skipped")
else:
    if not torch.cuda.is_available():
        raise SystemExit("[ERROR] CUDA is not available. This usually means wrong Torch build or incompatible RunPod image.")
    print("gpu:", torch.cuda.get_device_name(0))
    print("[OK] Torch CUDA is good")
PY
}'''
s = s[:match.start()] + new_verify + s[match.end():]

path.write_text(s, encoding="utf-8")
print(f"[OK] patched {path}")
