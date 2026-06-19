#!/usr/bin/env python3
"""Submit a test request to your RunPod endpoint.

Usage:
  export RUNPOD_API_KEY=...
  export RUNPOD_ENDPOINT_ID=...
  python scripts/test_request.py --image-url https://...png --audio-url https://...wav --prompt "..."
"""
import argparse
import json
import os
import time
import requests

parser = argparse.ArgumentParser()
parser.add_argument("--image-url", required=True)
parser.add_argument("--audio-url", required=True)
parser.add_argument("--prompt", required=True)
parser.add_argument("--seconds", type=int, default=5)
parser.add_argument("--width", type=int, default=1080)
parser.add_argument("--height", type=int, default=1920)
parser.add_argument("--sync", action="store_true", help="Use /runsync instead of async /run")
args = parser.parse_args()

api_key = os.environ["RUNPOD_API_KEY"]
endpoint_id = os.environ["RUNPOD_ENDPOINT_ID"]
base = f"https://api.runpod.ai/v2/{endpoint_id}"
headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
body = {
    "input": {
        "image_url": args.image_url,
        "audio_url": args.audio_url,
        "prompt": args.prompt,
        "seconds": args.seconds,
        "width": args.width,
        "height": args.height,
        "return_base64": False,
        "base64_fallback": True,
    }
}

if args.sync:
    r = requests.post(f"{base}/runsync", headers=headers, json=body, timeout=3600)
    print(r.status_code)
    print(json.dumps(r.json(), indent=2)[:10000])
else:
    r = requests.post(f"{base}/run", headers=headers, json=body, timeout=120)
    r.raise_for_status()
    data = r.json()
    print("queued:", json.dumps(data, indent=2))
    job_id = data["id"]
    while True:
        s = requests.get(f"{base}/status/{job_id}", headers=headers, timeout=120)
        s.raise_for_status()
        status = s.json()
        print(status.get("status"))
        if status.get("status") in {"COMPLETED", "FAILED", "CANCELLED", "TIMED_OUT"}:
            print(json.dumps(status, indent=2)[:20000])
            break
        time.sleep(10)
