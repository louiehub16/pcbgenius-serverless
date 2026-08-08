# PCBGenius — Always-on Supervisor Orchestrator (v2: R2-verifiable heartbeats + SSH)
# Runs as a Salad vCPU container 24/7. The Kimi K3 "brain".
#
# v2 changes vs v1:
#   - Writes a heartbeat object to R2 each tick so the operator (via API) can
#     CONFIRM the loop is alive and see its latest decision. (Salad is
#     ephemeral; R2 is the durable, externally-readable trace.)
#   - Optional SSH (openssh-server) so we can exec in and read full logs.
#
# Loop (every SUPERVIOR_INTERVAL_SEC):
#   1. read pipeline stage state
#   2. call Kimi K3 (OpenRouter) for supervision verdict
#   3. POST status to kanban
#   4. write heartbeat {ts, stage, verdict, status} to R2 supervisor/{ts}.json
#   5. sleep
import json
import os
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone

OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY", "")
KIMI_MODEL = os.environ.get("KIMI_MODEL", "moonshotai/kimi-k3")
KANBAN_URL = os.environ.get("KANBAN_URL", "https://kanban.louie-ibunia-va.workers.dev/api/update")
AGENT_NAME = os.environ.get("AGENT_NAME", "kimi-supervisor")
INTERVAL = int(os.environ.get("SUPERVISOR_INTERVAL_SEC", "900"))
STATE_FILE = "/state/current_stage.txt"

# R2 (durable heartbeat store) — optional; if creds absent we just skip R2 writes
R2 = {
    "key": os.environ.get("R2_ACCESS_KEY", ""),
    "secret": os.environ.get("R2_SECRET_KEY", ""),
    "endpoint": os.environ.get("R2_ENDPOINT", ""),
    "bucket": os.environ.get("R2_BUCKET", "pcgenius-build"),
}


def http(method, url, body=None, headers=None, timeout=30):
    data = json.dumps(body).encode() if body is not None else None
    # Browser UA is REQUIRED — Cloudflare's kanban worker returns 403/1010
    # for non-browser agents (Python-urllib). Same WAF fix as the Salad API.
    hdr = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"),
    }
    if headers:
        hdr.update(headers)
    req = urllib.request.Request(url, data=data, method=method, headers=hdr)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()
    except Exception as e:
        return None, str(e)


def call_kimi(prompt, max_tokens=600):
    if not OPENROUTER_KEY:
        return None, "no OPENROUTER_API_KEY"
    body = {
        "model": KIMI_MODEL,
        "messages": [
            {"role": "system", "content": (
                "You are the PCBGenius project supervisor. Reply with ONLY a "
                "single JSON object: {\"phase\": <str>, \"next_action\": <str>, "
                "\"critical\": <bool>, \"note\": <str>}. No markdown, no reasoning, "
                "no extra prose.")},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": max_tokens,   # Kimi K3 is a reasoning model — too-small budget
        "temperature": 0.2,          # exhausts on reasoning and returns empty content.
    }
    s, out = http("POST", "https://openrouter.ai/api/v1/chat/completions", body,
                  headers={"Authorization": f"Bearer {OPENROUTER_KEY}"}, timeout=90)
    if s != 200:
        return None, f"kimi http {s}: {str(out)[:150]}"
    try:
        d = json.loads(out)
        msg = d["choices"][0]["message"]
        content = msg.get("content")
        if not content:  # reasoning model may put text in 'reasoning'
            content = msg.get("reasoning")
        if not content:
            return None, "kimi empty response"
        # Kimi K3 often wraps the JSON in prose/reasoning despite the instruction —
        # extract the FIRST balanced JSON object from anywhere in the text.
        obj = extract_first_json_object(content)
        if obj is None:
            return content, "no JSON object found in response"
        return json.dumps(obj), None
    except Exception as e:
        return None, f"kimi parse: {e}"


def extract_first_json_object(text):
    """Return the first balanced {...} JSON object found in text, or None.
    Handles Kimi wrapping JSON in prose by scanning for a '{' and matching
    its balanced brace depth (ignoring braces inside quoted strings)."""
    start = -1
    depth = 0
    in_str = False
    esc = False
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if start < 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                candidate = text[start : i + 1]
                try:
                    return json.loads(candidate)
                except Exception:
                    # not valid JSON — keep searching forward
                    start = -1
                    depth = 0
    return None


def post_kanban(status, feature, message, phase=None, agent=AGENT_NAME):
    body = {"agent": agent, "status": status, "feature": feature, "message": message}
    if phase:
        body["phase"] = phase
    s, out = http("POST", KANBAN_URL, body)
    return (s, out)


def read_state():
    """Read pipeline stage from R2 state/pipeline_state.txt (the durable,
    ground-truth marker the pipeline writes). Falls back to a local file,
    then 'unknown'."""
    try:
        with open(STATE_FILE) as f:
            local = f.read().strip()
            if local and local != "unknown":
                return local
    except Exception:
        pass
    if not (R2["key"] and R2["secret"] and R2["endpoint"]):
        return "unknown"
    try:
        import boto3
        s3 = boto3.client("s3", endpoint_url=R2["endpoint"],
            aws_access_key_id=R2["key"], aws_secret_access_key=R2["secret"])
        o = s3.get_object(Bucket=R2["bucket"], Key="state/pipeline_state.txt")
        return o["Body"].read().decode().strip() or "unknown"
    except Exception:
        return "unknown"


def write_r2_heartbeat(rec):
    """Write a durable heartbeat to R2 so the operator can verify via API.
    Uses the AWS S3 REST `PutObject` with AWS SigV4 via boto3 (present in image)."""
    if not (R2["key"] and R2["secret"] and R2["endpoint"]):
        return False
    try:
        import boto3
        s3 = boto3.client(
            "s3", endpoint_url=R2["endpoint"],
            aws_access_key_id=R2["key"], aws_secret_access_key=R2["secret"],
        )
        key = f"supervisor/{rec['ts'].replace(':','').replace('-','')}.json"
        s3.put_object(Bucket=R2["bucket"], Key=key, Body=json.dumps(rec, default=str))
        # keep a `supervisor/latest.json` marker for easy read
        s3.put_object(Bucket=R2["bucket"], Key="supervisor/latest.json",
                      Body=json.dumps(rec, default=str))
        return True
    except Exception as e:
        print(f"[supervisor] R2 heartbeat failed: {e}", flush=True)
        return False


def main():
    print(f"[supervisor v2] starting agent={AGENT_NAME} interval={INTERVAL}s", flush=True)
    try:
        post_kanban("ok", "supervisor-up", "Supervisor container online (always-on, v2).")
    except Exception as e:
        print(f"[supervisor] startup kanban post failed: {e}", flush=True)

    while True:
        tick = datetime.now(timezone.utc)
        try:
            stage = read_state()
            prompt = (
                f"Current pipeline stage: {stage}. Budget cap "
                f"{os.environ.get('COST_CAP_USD','90')} USD. No user present. "
                "What next? Short JSON."
            )
            verdict, kimi_err = call_kimi(prompt)
            crit = False
            note = "heartbeat ok"
            phase = None
            kanban_s = None
            if verdict:
                try:
                    v = json.loads(verdict)
                    crit = bool(v.get("critical"))
                    note = v.get("note") or "ok"
                    phase = v.get("phase")
                except Exception:
                    note = verdict[:120]
                kanban_s, _ = post_kanban("critical" if crit else "ok", "supervisor-tick", note, phase=phase)
            else:
                kanban_s, _ = post_kanban("warning", "supervisor-tick",
                            "Kimi call unavailable (check OPENROUTER_API_KEY).")
                if kimi_err:
                    note = kimi_err

            write_r2_heartbeat({
                "ts": tick.isoformat(), "agent": AGENT_NAME, "stage": stage,
                "status": "critical" if crit else "ok",
                "note": note, "kanban_http": kanban_s,
            })
        except Exception as e:
            print(f"[supervisor] loop error: {e}", flush=True)
            try:
                kanban_s, _ = post_kanban("warning", "supervisor-error", f"loop error: {e}")
                write_r2_heartbeat({"ts": tick.isoformat(), "agent": AGENT_NAME,
                                    "status": "error", "note": f"loop error: {e}",
                                    "kanban_http": kanban_s})
            except Exception as e2:
                print(f"[supervisor] error-logging failed: {e2}", flush=True)
        print(f"[{tick.isoformat()}] tick done", flush=True)
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
