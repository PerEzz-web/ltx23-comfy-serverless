# LTX 2.3 ComfyUI RunPod Serverless Worker

This repository builds a RunPod Serverless worker that runs the provided LTX 2.3 ComfyUI API workflow. The Docker image contains ComfyUI and custom nodes, but it does **not** contain the large model files. Models are expected to be supplied through RunPod Cached Models from a Hugging Face repository.

## Input schema

Single request:

```json
{
  "input": {
    "image_url": "https://.../first-frame.png",
    "audio_url": "https://.../reference.wav",
    "prompt": "Animate the source image...",
    "seconds": 5,
    "width": 1080,
    "height": 1920,
    "return_base64": false,
    "base64_fallback": true
  }
}
```

Batch request:

```json
{
  "input": {
    "width": 1080,
    "height": 1920,
    "batch": [
      {"image_url": "https://.../a.png", "audio_url": "https://.../a.wav", "prompt": "...", "seconds": 5},
      {"image_url": "https://.../b.png", "audio_url": "https://.../b.wav", "prompt": "...", "seconds": 6}
    ]
  }
}
```

## Key environment variables

- `MODEL_ID`: Hugging Face repo selected in RunPod's cached Model field, for example `your-user/ltx23-comfy-models`.
- `OUTPUT_S3_BUCKET`: bucket or RunPod network volume ID for output upload.
- `OUTPUT_S3_ENDPOINT_URL`: S3 endpoint URL.
- `OUTPUT_AWS_ACCESS_KEY_ID`, `OUTPUT_AWS_SECRET_ACCESS_KEY`, `OUTPUT_AWS_REGION`: credentials/region for S3-compatible output uploads.
- `PRESIGNED_URL_TTL`: signed URL lifetime in seconds.
- `MAX_BATCH`: max items processed in one job.

## Files

- `Dockerfile`: serverless worker image.
- `handler.py`: RunPod handler that starts ComfyUI internally, patches the workflow, and returns outputs.
- `workflows/LTX_I2V_API.json`: the ComfyUI API workflow.
- `scripts/populate_hf_repo_from_pod.sh`: uploads model files from a RunPod pod to a Hugging Face repo.
- `scripts/test_request.py`: submits a RunPod endpoint request.
