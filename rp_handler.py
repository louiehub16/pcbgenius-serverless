import os, sys, gc, re, base64, threading, requests, magic, json, time
from urllib.parse import urlparse
from io import BytesIO
from PIL import Image, ImageFile
ImageFile.LOAD_TRUNCATED_IMAGES = True

sys.path.insert(0, "/usr/local/lib/python3.11/dist-packages")
import subprocess, sys, os

# Install CUDA runtime at startup (RunPod's 50GB container disk handles this)
def ensure_cuda():
    pass

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

def handler(job):
    with execution_lock:
        try:
            inp = job["input"]
            prompt = inp.get("prompt", "")
            image = inp.get("image_data", inp.get("image_url", "")).strip()
            if not prompt: return {"status":"client_error","error":"prompt required"}
            
            if image:
                img = secure_process_image(image)
                vlm = {"prompt": prompt, "multi_modal_data": {"image": img}}
            else:
                vlm = prompt

            params = SamplingParams(temperature=0.7, top_p=0.9, max_tokens=min(int(inp.get("max_tokens",256)),1024))
            req_id = random_uuid()
            engine.add_request(req_id, vlm, params)

            final = ""
            while engine.has_unfinished_requests():
                outs = engine.step()
                for o in outs:
                    if o.request_id == req_id and o.finished:
                        final = o.outputs[0].text
            return {"status":"success","response": final.strip()}
        except ValueError as e:
            return {"status":"client_error","error":str(e)}
        except Exception as e:
            return {"status":"system_error","error":str(e)}
        finally:
            gc.collect(); torch.cuda.empty_cache()

import runpod
runpod.serverless.start({"handler": handler})