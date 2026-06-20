#!/usr/bin/env python3
"""RunPod Serverless handler for LTX 2.3 ComfyUI image+audio-to-video workflow.

Input examples:
{
  "input": {
    "image_url": "https://.../first_frame.png",
    "audio_url": "https://.../speech.wav",
    "prompt": "Animate the character...",
    "seconds": 5,
    "width": 1080,
    "height": 1920,
    "seed": 12345,
    "return_base64": false
  }
}

Batch example:
{
  "input": {
    "width": 1080,
    "height": 1920,
    "return_base64": false,
    "batch": [
      {"image_url": "...", "audio_url": "...", "prompt": "...", "seconds": 5},
      {"image_url": "...", "audio_url": "...", "prompt": "...", "seconds": 6}
    ]
  }
}
"""
from __future__ import annotations

import base64
import copy
import json
import mimetypes
import os
import pathlib
import random
import re
import shutil
import subprocess
import time
import uuid
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlparse

import boto3
import requests
import runpod

COMFY_ROOT = pathlib.Path(os.environ.get("COMFY_ROOT", "/opt/ComfyUI"))
COMFY_PYTHON = pathlib.Path(os.environ.get("COMFY_PYTHON", str(COMFY_ROOT / "venv" / "bin" / "python")))
COMFY_PORT = int(os.environ.get("COMFY_PORT", "8188"))
COMFY_URL = f"http://127.0.0.1:{COMFY_PORT}"
COMFY_INPUT_DIR = pathlib.Path(os.environ.get("COMFY_INPUT_DIR", str(COMFY_ROOT / "input")))
COMFY_TEMP_DIR = pathlib.Path(os.environ.get("COMFY_TEMP_DIR", str(COMFY_ROOT / "temp")))
COMFY_OUTPUT_DIR = pathlib.Path(os.environ.get("COMFY_OUTPUT_DIR", "/tmp/comfyui-output"))
WORKFLOW_PATH = pathlib.Path(os.environ.get("WORKFLOW_PATH", "/app/workflows/LTX_I2V_API.json"))
HF_CACHE_ROOT = pathlib.Path(os.environ.get("HF_CACHE_ROOT", "/runpod-volume/huggingface-cache/hub"))
MODEL_ID = os.environ.get("MODEL_ID", "").strip()
CACHED_MODEL_PATH = os.environ.get("CACHED_MODEL_PATH", "").strip()

JOB_TIMEOUT = int(os.environ.get("JOB_TIMEOUT", "3600"))
COMFY_START_TIMEOUT = int(os.environ.get("COMFY_START_TIMEOUT", "900"))
MAX_BATCH = int(os.environ.get("MAX_BATCH", "4"))
MAX_LINGER_SECONDS = int(os.environ.get("MAX_LINGER_SECONDS", "300"))
DEFAULT_WIDTH = int(os.environ.get("DEFAULT_WIDTH", "1080"))
DEFAULT_HEIGHT = int(os.environ.get("DEFAULT_HEIGHT", "1920"))
DEFAULT_SECONDS = int(os.environ.get("DEFAULT_SECONDS", "5"))
DEFAULT_FPS = int(os.environ.get("DEFAULT_FPS", "24"))
MAX_BASE64_BYTES = int(os.environ.get("MAX_BASE64_BYTES", str(50 * 1024 * 1024)))

COMFY_PROCESS: Optional[subprocess.Popen] = None
MODELS_READY = False
CLIENT_ID = str(uuid.uuid4())


def log(msg: str) -> None:
    print(f"[ltx-worker] {msg}", flush=True)


def safe_int(value: Any, default: int, min_value: int, max_value: int) -> int:
    try:
        ivalue = int(value)
    except Exception:
        ivalue = default
    return max(min_value, min(max_value, ivalue))


def make_divisible_by_8(value: int) -> int:
    value = int(value)
    return max(8, value - (value % 8))


def load_workflow() -> Dict[str, Any]:
    with WORKFLOW_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def resolve_cached_snapshot(model_id: str) -> Path:
    cache_root = Path("/runpod-volume/huggingface-cache/hub")
    expected_name = "models--" + model_id.replace("/", "--")

    candidates = []

    exact_root = cache_root / expected_name
    if exact_root.exists():
        candidates.append(exact_root)

    # Case-insensitive fallback because some UIs normalize HF URLs/usernames.
    if cache_root.exists():
        target_lower = expected_name.lower()
        for p in cache_root.glob("models--*"):
            if p.name.lower() == target_lower:
                candidates.append(p)

    if not candidates:
        available = []
        if cache_root.exists():
            available = [p.name for p in cache_root.glob("*")][:50]

        raise FileNotFoundError(
            f"Could not find cached Hugging Face model for {model_id}. "
            f"Expected under {exact_root}. "
            f"Available cache entries: {available}. "
            f"Make sure the endpoint Model field is set to {model_id}."
        )

    model_root = candidates[0]
    snapshots_dir = model_root / "snapshots"

    if not snapshots_dir.exists():
        raise FileNotFoundError(f"Cached model found, but snapshots folder missing: {snapshots_dir}")

    snapshots = [p for p in snapshots_dir.iterdir() if p.is_dir()]
    if not snapshots:
        raise FileNotFoundError(f"No snapshots found in: {snapshots_dir}")

    snapshots.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return snapshots[0]

def locate_model_base(snapshot: pathlib.Path) -> pathlib.Path:
    required_dirs = ["unet", "vae", "text_encoders", "latent_upscale_models", "loras"]
    for candidate in [snapshot, snapshot / "models", snapshot / "comfy_models"]:
        if all((candidate / d).exists() for d in required_dirs):
            return candidate
    raise FileNotFoundError(
        "Cached model snapshot was found, but it does not contain the expected "
        "ComfyUI folders: unet, vae, text_encoders, latent_upscale_models, loras. "
        f"Snapshot: {snapshot}"
    )


def symlink_tree_files(src_dir: pathlib.Path, dst_dir: pathlib.Path) -> None:
    dst_dir.mkdir(parents=True, exist_ok=True)
    for src in src_dir.iterdir():
        dst = dst_dir / src.name
        if dst.exists() or dst.is_symlink():
            continue
        os.symlink(src, dst)


def prepare_models() -> None:
    global MODELS_READY
    if MODELS_READY:
        return

    snapshot = resolve_cached_snapshot(MODEL_ID)
    base = locate_model_base(snapshot)
    log(f"Using ComfyUI model base: {base}")

    mapping = {
        "unet": "unet",
        "vae": "vae",
        "text_encoders": "text_encoders",
        "latent_upscale_models": "latent_upscale_models",
        "loras": "loras",
    }
    for src_name, dst_name in mapping.items():
        symlink_tree_files(base / src_name, COMFY_ROOT / "models" / dst_name)

    expected_files = [
        COMFY_ROOT / "models" / "unet" / "ltx-2.3-22b-dev-Q8_0.gguf",
        COMFY_ROOT / "models" / "vae" / "LTX23_video_vae_bf16.safetensors",
        COMFY_ROOT / "models" / "vae" / "LTX23_audio_vae_bf16.safetensors",
        COMFY_ROOT / "models" / "text_encoders" / "gemma_3_12B_it_fp4_mixed.safetensors",
        COMFY_ROOT / "models" / "text_encoders" / "ltx-2.3_text_projection_bf16.safetensors",
        COMFY_ROOT / "models" / "latent_upscale_models" / "ltx-2.3-spatial-upscaler-x2-1.1.safetensors",
        COMFY_ROOT / "models" / "loras" / "ltx-2.3-22b-distilled-lora-384-1.1.safetensors",
        COMFY_ROOT / "models" / "loras" / "ltx-2-19b-ic-lora-detailer.safetensors",
    ]
    missing = [str(p) for p in expected_files if not p.exists()]
    if missing:
        raise FileNotFoundError("Missing expected model files:\n" + "\n".join(missing))

    MODELS_READY = True
    log("Model symlinks are ready")


def start_comfyui() -> None:
    global COMFY_PROCESS
    if COMFY_PROCESS is not None and COMFY_PROCESS.poll() is None:
        return

    prepare_models()

    COMFY_INPUT_DIR.mkdir(parents=True, exist_ok=True)
    COMFY_TEMP_DIR.mkdir(parents=True, exist_ok=True)
    COMFY_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    log_path = pathlib.Path("/tmp/comfyui-serverless.log")
    comfy_args = [
        str(COMFY_PYTHON),
        "-u",
        "main.py",
        "--listen",
        "127.0.0.1",
        "--port",
        str(COMFY_PORT),
        "--output-directory",
        str(COMFY_OUTPUT_DIR),
        "--disable-xformers",
        "--use-pytorch-cross-attention",
    ]

    extra_args = os.environ.get("COMFY_EXTRA_ARGS", "").strip()
    if extra_args:
        comfy_args.extend(extra_args.split())

    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

    log("Starting ComfyUI")
    log(" ".join(comfy_args))
    COMFY_PROCESS = subprocess.Popen(
        comfy_args,
        cwd=str(COMFY_ROOT),
        stdout=log_path.open("ab"),
        stderr=subprocess.STDOUT,
        env=env,
    )
    wait_for_comfyui(COMFY_START_TIMEOUT)


def wait_for_comfyui(timeout: int) -> None:
    deadline = time.time() + timeout
    last_error: Optional[Exception] = None
    while time.time() < deadline:
        if COMFY_PROCESS is not None and COMFY_PROCESS.poll() is not None:
            tail = read_tail(pathlib.Path("/tmp/comfyui-serverless.log"), 200)
            raise RuntimeError(f"ComfyUI exited during startup. Log tail:\n{tail}")
        try:
            r = requests.get(f"{COMFY_URL}/system_stats", timeout=5)
            if r.status_code == 200:
                log("ComfyUI is ready")
                return
        except Exception as exc:
            last_error = exc
        time.sleep(2)
    tail = read_tail(pathlib.Path("/tmp/comfyui-serverless.log"), 200)
    raise TimeoutError(f"ComfyUI did not become ready in {timeout}s. Last error={last_error}\n{tail}")


def read_tail(path: pathlib.Path, lines: int = 120) -> str:
    if not path.exists():
        return ""
    data = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    return "\n".join(data[-lines:])


def extension_from_content_type(content_type: str, fallback: str) -> str:
    content_type = (content_type or "").split(";")[0].strip().lower()
    mapping = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/webp": ".webp",
        "audio/wav": ".wav",
        "audio/x-wav": ".wav",
        "audio/mpeg": ".mp3",
        "audio/mp3": ".mp3",
        "audio/mp4": ".m4a",
        "audio/ogg": ".ogg",
    }
    return mapping.get(content_type) or mimetypes.guess_extension(content_type) or fallback


def extension_from_url(url: str, fallback: str) -> str:
    path = urlparse(url).path
    ext = pathlib.Path(path).suffix.lower()
    if re.fullmatch(r"\.[a-z0-9]{2,5}", ext):
        return ext
    return fallback


def decode_data_uri(data_uri: str) -> Tuple[bytes, str]:
    header, payload = data_uri.split(",", 1)
    m = re.match(r"data:([^;]+);base64", header)
    content_type = m.group(1) if m else "application/octet-stream"
    return base64.b64decode(payload), content_type


def write_input_asset(value: Any, dest_stem: str, default_ext: str) -> str:
    """Write URL/base64/data-URI input to ComfyUI input dir and return filename."""
    if value is None:
        raise ValueError(f"Missing input asset for {dest_stem}")

    if isinstance(value, dict):
        value = value.get("url") or value.get("base64") or value.get("data") or value.get("b64")

    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Invalid input asset for {dest_stem}")

    value = value.strip()
    if value.startswith("http://") or value.startswith("https://"):
        ext = extension_from_url(value, default_ext)
        out_name = f"{dest_stem}{ext}"
        out_path = COMFY_INPUT_DIR / out_name
        log(f"Downloading input asset: {value}")
        with requests.get(value, stream=True, timeout=120) as r:
            r.raise_for_status()
            # Prefer content-type extension if URL has no meaningful extension.
            if ext == default_ext:
                cext = extension_from_content_type(r.headers.get("content-type", ""), default_ext)
                if cext != ext:
                    out_name = f"{dest_stem}{cext}"
                    out_path = COMFY_INPUT_DIR / out_name
            with out_path.open("wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        f.write(chunk)
        return out_name

    if value.startswith("data:"):
        raw, content_type = decode_data_uri(value)
        ext = extension_from_content_type(content_type, default_ext)
        out_name = f"{dest_stem}{ext}"
        (COMFY_INPUT_DIR / out_name).write_bytes(raw)
        return out_name

    # Plain base64 fallback.
    try:
        raw = base64.b64decode(value, validate=True)
    except Exception as exc:
        raise ValueError(
            f"Input asset for {dest_stem} is neither URL, data URI, nor valid base64"
        ) from exc
    out_name = f"{dest_stem}{default_ext}"
    (COMFY_INPUT_DIR / out_name).write_bytes(raw)
    return out_name


def patch_workflow(base_workflow: Dict[str, Any], item: Dict[str, Any], job_id: str, index: int) -> Dict[str, Any]:
    workflow = copy.deepcopy(base_workflow)

    width = make_divisible_by_8(safe_int(item.get("width"), DEFAULT_WIDTH, 64, 4096))
    height = make_divisible_by_8(safe_int(item.get("height"), DEFAULT_HEIGHT, 64, 4096))
    seconds = safe_int(item.get("seconds") or item.get("duration"), DEFAULT_SECONDS, 1, 60)
    fps = safe_int(item.get("fps") or item.get("frame_rate"), DEFAULT_FPS, 1, 60)
    seed = item.get("seed")
    if seed is None or str(seed).lower() in {"", "random", "none"}:
        seed = random.randint(1, 2**31 - 1)
    seed = int(seed)

    prompt = item.get("prompt") or item.get("text_prompt")
    if not prompt:
        raise ValueError("prompt is required")

    input_id = f"{job_id}_{index:03d}"
    image_value = item.get("image") or item.get("image_url") or item.get("image_base64")
    audio_value = item.get("audio") or item.get("audio_url") or item.get("audio_base64") or item.get("audio_reference") or item.get("audio_reference_url")
    image_filename = write_input_asset(image_value, f"{input_id}_image", ".png")
    audio_filename = write_input_asset(audio_value, f"{input_id}_audio", ".wav")

    workflow["1077"]["inputs"]["image"] = image_filename
    workflow["1079"]["inputs"]["audio"] = audio_filename
    workflow["1079"]["inputs"].pop("audioUI", None)
    workflow["1070"]["inputs"]["text"] = prompt
    workflow["1071"]["inputs"]["value"] = width
    workflow["1069"]["inputs"]["value"] = height
    workflow["1072"]["inputs"]["value"] = fps
    workflow["1073"]["inputs"]["value"] = seconds
    workflow["1074"]["inputs"]["noise_seed"] = seed
    workflow["1087"]["inputs"]["filename_prefix"] = f"ltx_serverless/{input_id}"

    return workflow


def queue_prompt(workflow: Dict[str, Any]) -> str:
    payload = {"prompt": workflow, "client_id": CLIENT_ID}
    r = requests.post(f"{COMFY_URL}/prompt", json=payload, timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f"ComfyUI /prompt failed: {r.status_code}\n{r.text}")
    data = r.json()
    if "prompt_id" not in data:
        raise RuntimeError(f"ComfyUI /prompt did not return prompt_id: {data}")
    return data["prompt_id"]


def wait_for_history(prompt_id: str, timeout: int) -> Dict[str, Any]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = requests.get(f"{COMFY_URL}/history/{prompt_id}", timeout=30)
        r.raise_for_status()
        data = r.json()
        if prompt_id in data:
            result = data[prompt_id]
            status = result.get("status", {})
            if status.get("status_str") in {"error", "failed"}:
                raise RuntimeError(f"ComfyUI execution failed: {json.dumps(status, indent=2)}")
            return result
        time.sleep(2)
    raise TimeoutError(f"Timed out waiting for ComfyUI prompt_id={prompt_id}")


def collect_output_files(history: Dict[str, Any], started_at: float, job_prefix: str) -> List[pathlib.Path]:
    files: List[pathlib.Path] = []
    outputs = history.get("outputs", {})
    for node_output in outputs.values():
        for key in ("images", "gifs", "videos", "audio"):
            for item in node_output.get(key, []) or []:
                filename = item.get("filename")
                subfolder = item.get("subfolder", "")
                item_type = item.get("type", "output")
                if not filename:
                    continue
                base = COMFY_TEMP_DIR if item_type == "temp" else COMFY_OUTPUT_DIR
                path = base / subfolder / filename
                if path.exists() and path.is_file():
                    files.append(path)

    if files:
        return unique_paths(files)

    # Fallback for custom nodes that don't report files in history as expected.
    fallback = []
    for p in COMFY_OUTPUT_DIR.rglob("*"):
        if p.is_file() and p.stat().st_mtime >= started_at - 2:
            if job_prefix in str(p) or p.suffix.lower() in {".mp4", ".mov", ".webm", ".mkv"}:
                fallback.append(p)
    return unique_paths(sorted(fallback, key=lambda p: p.stat().st_mtime, reverse=True))


def unique_paths(paths: Iterable[pathlib.Path]) -> List[pathlib.Path]:
    seen = set()
    out = []
    for p in paths:
        rp = str(p.resolve())
        if rp not in seen:
            seen.add(rp)
            out.append(p)
    return out


def get_s3_client():
    bucket = os.environ.get("OUTPUT_S3_BUCKET", "").strip()
    if not bucket:
        return None, None

    endpoint_url = os.environ.get("OUTPUT_S3_ENDPOINT_URL") or os.environ.get("S3_ENDPOINT_URL") or None
    region_name = os.environ.get("OUTPUT_AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "auto"
    access_key = os.environ.get("OUTPUT_AWS_ACCESS_KEY_ID") or os.environ.get("AWS_ACCESS_KEY_ID")
    secret_key = os.environ.get("OUTPUT_AWS_SECRET_ACCESS_KEY") or os.environ.get("AWS_SECRET_ACCESS_KEY")

    kwargs = {"region_name": region_name, "endpoint_url": endpoint_url}
    if access_key and secret_key:
        kwargs["aws_access_key_id"] = access_key
        kwargs["aws_secret_access_key"] = secret_key
    return boto3.client("s3", **kwargs), bucket


def upload_and_sign(path: pathlib.Path, job_id: str) -> Optional[Dict[str, Any]]:
    client, bucket = get_s3_client()
    if client is None or bucket is None:
        return None

    prefix = os.environ.get("OUTPUT_S3_PREFIX", "ltx-serverless").strip("/")
    key = f"{prefix}/{job_id}/{path.name}"
    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"

    client.upload_file(str(path), bucket, key, ExtraArgs={"ContentType": content_type})
    ttl = int(os.environ.get("PRESIGNED_URL_TTL", "86400"))
    signed_url = client.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=ttl,
    )
    return {
        "bucket": bucket,
        "key": key,
        "signed_url": signed_url,
        "signed_url_expires_in": ttl,
        "content_type": content_type,
    }


def file_to_base64(path: pathlib.Path, max_bytes: int) -> Optional[str]:
    if path.stat().st_size > max_bytes:
        return None
    return base64.b64encode(path.read_bytes()).decode("utf-8")


def run_one(base_workflow: Dict[str, Any], item: Dict[str, Any], job_id: str, index: int, timeout: int) -> Dict[str, Any]:
    started_at = time.time()
    workflow = patch_workflow(base_workflow, item, job_id, index)
    prompt_id = queue_prompt(workflow)
    log(f"Queued ComfyUI prompt_id={prompt_id} for batch index={index}")
    history = wait_for_history(prompt_id, timeout=timeout)
    files = collect_output_files(history, started_at, f"{job_id}_{index:03d}")

    return_base64 = bool(item.get("return_base64", False) or item.get("base64_fallback", True))
    max_b64 = safe_int(item.get("max_base64_bytes"), MAX_BASE64_BYTES, 1_000_000, 500_000_000)

    outputs = []
    for path in files:
        record: Dict[str, Any] = {
            "filename": path.name,
            "size_bytes": path.stat().st_size,
        }
        try:
            uploaded = upload_and_sign(path, job_id)
        except Exception as exc:
            uploaded = None
            record["upload_error"] = repr(exc)

        if uploaded:
            record.update(uploaded)
        elif return_base64:
            b64 = file_to_base64(path, max_b64)
            if b64 is None:
                record["base64_skipped"] = f"file exceeds max_base64_bytes={max_b64}"
            else:
                record["base64"] = b64
                record["content_type"] = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        outputs.append(record)

    return {
        "index": index,
        "prompt_id": prompt_id,
        "outputs": outputs,
    }


def merge_common(top_level: Dict[str, Any], item: Dict[str, Any]) -> Dict[str, Any]:
    common_keys = [
        "width", "height", "seconds", "duration", "fps", "frame_rate", "prompt", "text_prompt",
        "return_base64", "base64_fallback", "max_base64_bytes", "seed",
        "image", "image_url", "image_base64", "audio", "audio_url", "audio_base64",
        "audio_reference", "audio_reference_url",
    ]
    merged = {k: top_level[k] for k in common_keys if k in top_level}
    merged.update(item)
    return merged


def handler(job: Dict[str, Any]) -> Dict[str, Any]:
    job_input = job.get("input", {}) or {}
    job_id = str(job.get("id") or uuid.uuid4())
    log(f"Received job {job_id}")

    start_comfyui()
    base_workflow = load_workflow()

    if isinstance(job_input.get("batch"), list):
        raw_items = job_input["batch"]
    else:
        raw_items = [job_input]

    if not raw_items:
        return {"error": "batch is empty"}
    if len(raw_items) > MAX_BATCH:
        return {"error": f"batch size {len(raw_items)} exceeds MAX_BATCH={MAX_BATCH}"}

    timeout = safe_int(job_input.get("timeout"), JOB_TIMEOUT, 60, 24 * 3600)
    results = []
    for index, raw_item in enumerate(raw_items):
        if not isinstance(raw_item, dict):
            return {"error": f"batch item {index} must be an object"}
        item = merge_common(job_input, raw_item)
        results.append(run_one(base_workflow, item, job_id, index, timeout))

    linger = safe_int(job_input.get("linger_seconds"), 0, 0, MAX_LINGER_SECONDS)
    if linger > 0:
        log(f"Lingering {linger}s to keep worker warm as requested")
        time.sleep(linger)

    return {
        "job_id": job_id,
        "batch_count": len(results),
        "results": results,
    }


runpod.serverless.start({"handler": handler})
