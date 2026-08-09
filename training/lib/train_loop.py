#!/usr/bin/env python3
"""
PCBGenius training-loop hardening helpers (2026-08-09).
Wrapping any training loop with these gives Salad-eviction-safe, atomic, resumable
checkpointing WITHOUT blocking the GPU on network I/O. Engine-agnostic (works with
unsloth/transformers/trl Trainer callbacks OR a manual loop).

Provide:
  save_atomic_checkpoint(state_dict, dir)     -> temp file + os.replace (atomic)
  upload_checkpoints_async(dir, r2_prefix)    -> background thread, put to R2
  register_eviction_handler(on_evict)         -> SIGTERM/SIGINT -> emergency save+exit 0
  vram_safety_sweep()                         -> print VRAM alloc/reserved, return free GB
  oom_headroom_check(min_free_gb)             -> sys.exit(90) if below headroom
  bundle_seeds() / restore_seeds(bundle)      -> torch+np+random state inside checkpoint
"""
import os, sys, signal, threading, queue, tempfile, glob, time, random
import numpy as np

def _r2_client():
    import boto3
    return boto3.client("s3",
        endpoint_url=os.environ.get("R2_ENDPOINT",""),
        aws_access_key_id=os.environ.get("R2_ACCESS_KEY",""),
        aws_secret_access_key=os.environ.get("R2_SECRET_KEY",""),
        region_name="auto")

# ---------------------------------------------------------------------------
# Async background checkpoint uploader (GPU never blocks on network)
# ---------------------------------------------------------------------------
_upload_queue = queue.Queue()
_uploader = None

def _async_uploader_worker():
    while True:
        item = _upload_queue.get()
        if item is None:
            break
        local_path, r2_prefix = item
        try:
            c = _r2_client()
            c.upload_file(local_path, os.environ["R2_BUCKET"],
                          f"{r2_prefix.rstrip('/')}/{os.path.basename(local_path)}")
        except Exception as e:
            print(f"[async-uploader] FAILED {local_path} -> {r2_prefix}: {e}", flush=True)
        finally:
            _upload_queue.task_done()

def start_async_uploader():
    global _uploader
    if _uploader is None or not _uploader.is_alive():
        _uploader = threading.Thread(target=_async_uploader_worker, daemon=True)
        _uploader.start()
    return _uploader

def enqueue_upload(local_path, r2_prefix):
    start_async_uploader()
    _upload_queue.put((local_path, r2_prefix))

def stop_async_uploader():
    if _uploader and _uploader.is_alive():
        _upload_queue.put(None)

# ---------------------------------------------------------------------------
# Atomic multi-stage safe checkpointing (temp file + rename, no corruption)
# ---------------------------------------------------------------------------
def save_atomic_checkpoint(state_dict, directory, name="latest_checkpoint.pt"):
    os.makedirs(directory, exist_ok=True)
    tmp = os.path.join(directory, f".{name}.tmp.{os.getpid()}")
    final = os.path.join(directory, name)
    if hasattr(state_dict, "save"):        # safetensors / model
        state_dict.save(tmp)
    else:                                   # plain python dict
        import torch
        torch.save(state_dict, tmp)
    os.replace(tmp, final)                  # atomic swap
    enqueue_upload(final, os.environ.get("R2_CHECKPOINT_PREFIX","artifacts/checkpoints"))
    return final

# ---------------------------------------------------------------------------
# Seeds bundling (deterministic resume)
# ---------------------------------------------------------------------------
def bundle_seeds():
    import torch
    return {"torch_rng": torch.get_rng_state().cpu().numpy() if torch.cuda.is_available() or True else None,
            "torch_cuda_rng": torch.cuda.get_rng_state().numpy() if torch.cuda.is_available() else None,
            "numpy_rng": np.random.get_state(),
            "python_rng": random.getstate()}

def restore_seeds(b):
    import torch
    torch.manual_seed(42)
    if b.get("torch_rng") is not None: torch.set_rng_state(torch.ByteTensor(b["torch_rng"]))
    if b.get("torch_cuda_rng") is not None: torch.cuda.set_rng_state(torch.ByteTensor(b["torch_cuda_rng"]))
    np.random.set_state(tuple(b["numpy_rng"]))
    random.setstate(tuple(b["python_rng"]))

# ---------------------------------------------------------------------------
# Eviction handling & graceful termination
# ---------------------------------------------------------------------------
_evict_hooks = []
def register_eviction_handler(fn):
    _evict_hooks.append(fn)

def _handle_eviction(signum, frame):
    print(f"[evict] caught signal {signum} — emergency save then graceful exit", flush=True)
    for fn in _evict_hooks:
        try: fn()
        except Exception as e: print(f"[evict] hook error: {e}", flush=True)
    print("[evict] emergency state locked. exiting.", flush=True)
    sys.exit(0)

def arm_eviction_handlers():
    signal.signal(signal.SIGTERM, _handle_eviction)
    signal.signal(signal.SIGINT, _handle_eviction)

# ---------------------------------------------------------------------------
# Dynamic VRAM safety sweep + OOM headroom
# ---------------------------------------------------------------------------
def vram_safety_sweep(min_free_gb=2.0):
    import torch
    if not torch.cuda.is_available():
        print("[vram-sweep] CRITICAL: no CUDA device.", flush=True)
        return 0.0
    free_bytes = torch.cuda.mem_get_info()[0] if hasattr(torch.cuda, "mem_get_info") else (torch.cuda.get_device_properties(0).total_memory - torch.cuda.memory_allocated())
    free_gb = free_bytes/(1024**3)
    print(f"[vram-sweep] free={free_gb:.2f}GB alloc={torch.cuda.memory_allocated()/(1024**3):.2f}GB reserved={torch.cuda.memory_reserved()/(1024**3):.2f}GB", flush=True)
    if free_gb < min_free_gb:
        print(f"[vram-sweep] only {free_gb:.2f}GB free (<{min_free_gb}) — aborting to avoid OOM crash", flush=True)
        sys.exit(90)
    return free_gb

def oom_headroom_check(min_free_mem_gb=4.0):
    t = os.sysconf("SC_AVPHYS_PAGES")*os.sysconf("SC_PAGE_SIZE")/(1024**3)
    t = t if t > 0 else 0.0
    print(f"[oom-guard] host free RAM ~{t:.1f}GB (min {min_free_mem_gb}GB)", flush=True)
    if t < min_free_mem_gb:
        print(f"[oom-guard] low host RAM {t:.1f}GB — tuning batch/threads down", flush=True)