import os, sys, gc, re, base64, threading, requests, magic, json, time
from urllib.parse import urlparse
from io import BytesIO
from PIL import Image, ImageFile
ImageFile.LOAD_TRUNCATED_IMAGES = True

sys.path.insert(0, "/usr/local/lib/python3.11/dist-packages")
import subprocess

# Install CUDA runtime at container startup if needed
def ensure_cuda():
    try:
        import torch
        if torch.cuda.is_available():
            return
    except: pass
    print("Installing CUDA runtime (one-time at startup)...")
    subprocess.run([sys.executable, "-m", "pip", "install", "--no-cache-dir",
        "nvidia-cuda-runtime-cu12"], capture_output=True)
    print("CUDA runtime installed.")

ensure_cuda()

import torch
if torch.cuda.is_available(): torch.cuda.init()

MODEL_DIR = "/models/qwen3-vl-32b-awq"
MODEL_HF = "QuantTrio/Qwen3-VL-32B-Instruct-AWQ"

# Download model on first boot if not cached
if not os.path.exists(os.path.join(MODEL_DIR, "config.json")):
    print(f"Downloading {MODEL_HF} to {MODEL_DIR}...")
    os.makedirs(MODEL_DIR, exist_ok=True)
    from huggingface_hub import snapshot_download
    snapshot_download(MODEL_HF, local_dir=MODEL_DIR, max_workers=8)
    print("Download complete.")

print("Loading Qwen3-VL-32B-AWQ on RTX 6000 Ada...")
from vllm import LLMEngine, EngineArgs, SamplingParams
from vllm.utils import random_uuid

start = time.time()
engine_args = EngineArgs(
    model=MODEL_DIR, quantization="awq", dtype="float16",
    enforce_eager=False, max_model_len=4096, max_num_seqs=4,
    limit_mm_per_prompt={"image": 1}, gpu_memory_utilization=0.85,
    trust_remote_code=True, kv_cache_dtype="auto"
)
engine = LLMEngine.from_engine_args(engine_args)
load_t = time.time() - start
print(f"Engine loaded in {load_t:.1f}s. Endpoint Online.")

execution_lock = threading.Lock()

def secure_process_image(input_data):
    if not input_data or not isinstance(input_data, str):
        raise ValueError("image_data must be a non-empty string URL or Base64.")
    input_data = input_data.strip()
    if input_data.startswith(("http://", "https://")):
        parsed_url = urlparse(input_data)
        hostname = parsed_url.hostname.lower() if parsed_url.hostname else ""
        if any(x in hostname for x in ["localhost","127.0.0.1","169.254","0.0.0.0"]):
            raise ValueError("SSRF blocked.")
        if hostname.startswith("10.") or hostname.startswith("192.168."):
            raise ValueError("SSRF blocked.")
        response = requests.get(input_data, timeout=4); response.raise_for_status()
        raw_bytes = response.content
    else:
        if "," in input_data: input_data = input_data.split(",")[-1]
        raw_bytes = base64.b64decode(input_data)
    if len(raw_bytes) > 20*1024*1024:
        raise ValueError("Image exceeds 20MB limit.")
    mime = magic.from_buffer(raw_bytes, mime=True)
    if mime not in ["image/jpeg","image/png","image/webp"]:
        raise ValueError(f"Unsupported type: {mime}")
    img = Image.open(BytesIO(raw_bytes))
    if max(img.width, img.height) > 1536:
        img.thumbnail((1536, 1536), Image.Resampling.LANCZOS)
    if img.mode != "RGB": img = img.convert("RGB")
    return img

def vllm_handler(prompt, image_data=None, max_tokens=256, temperature=0.7, top_p=0.9):
    with execution_lock:
        try:
            if image_data:
                img = secure_process_image(image_data)
                vlm = {"prompt": prompt, "multi_modal_data": {"image": img}}
            else:
                vlm = prompt

            params = SamplingParams(temperature=temperature, top_p=top_p,
                                    max_tokens=min(int(max_tokens), 1024))
            req_id = random_uuid()
            engine.add_request(req_id, vlm, params)

            final = ""
            while engine.has_unfinished_requests():
                outs = engine.step()
                for o in outs:
                    if o.request_id == req_id and o.finished:
                        final = o.outputs[0].text
            return {"status": "success", "response": final.strip()}
        except ValueError as e:
            return {"status": "client_error", "error": str(e)}
        except Exception as e:
            return {"status": "system_error", "error": str(e)}
        finally:
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()

# --- FastAPI web server for HF Spaces ---
from fastapi import FastAPI
from pydantic import BaseModel, Field
from typing import Optional

app = FastAPI(title="PCBGenius Qwen3-VL Server", version="1.0.0")

class InferenceRequest(BaseModel):
    prompt: str = Field(..., description="Text prompt for the model")
    image_data: Optional[str] = Field(None, description="Base64 or URL of image")
    max_tokens: int = Field(256, ge=1, le=1024)
    temperature: float = Field(0.7, ge=0.0, le=2.0)
    top_p: float = Field(0.9, ge=0.0, le=1.0)

class InferenceResponse(BaseModel):
    status: str
    response: Optional[str] = None
    error: Optional[str] = None

@app.get("/")
async def root():
    return {
        "service": "PCBGenius Qwen3-VL",
        "status": "online",
        "model": MODEL_HF
    }

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.post("/v1/chat/completions", response_model=InferenceResponse)
async def chat_completions(req: InferenceRequest):
    return vllm_handler(
        prompt=req.prompt,
        image_data=req.image_data,
        max_tokens=req.max_tokens,
        temperature=req.temperature,
        top_p=req.top_p
    )