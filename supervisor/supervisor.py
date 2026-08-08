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
    hdr = {"Content-Type": "application/json", "Accept": "application/json"}
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


def post_kanban(status, feature, message, phase=None, agent=AGENT_NAME):
    body = {"agent": agent, "status": status, "feature": feature, "message": message}
    if phase:
        body["phase"] = phase
    return http("POST", KANBAN_URL, body)


def call_kimi(prompt, max_tokens=400):
    if not OPENROUTER_KEY:
        return None
    body = {
        "model": KIMI_MODEL,
        "messages": [
            {"role": "system", "content": (
                "You are the PCBGenius project supervisor. Give terse, actionable "
                "verdicts as strict JSON with keys: phase, next_action, critical, note.")},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": 0.2,
    }
    s, out = http("POST", "https://openrouter.ai/api/v1/chat/completions", body,
                  headers={"Authorization": f"Bearer {OPENROUTER_KEY}"}, timeout=60)
    if s != 200:
        return None
    try:
        return json.loads(out)["choices"][0]["message"]["content"]
    except Exception:
        return None


def read_state():
    try:
        with open(STATE_FILE) as f:
            return f.read().strip() or "unknown"
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
            verdict = call_kimi(prompt)
            crit = False
            note = "heartbeat ok"
            phase = None
            if verdict:
                try:
                    v = json.loads(verdict)
                    crit = bool(v.get("critical"))
                    note = v.get("note") or "ok"
                    phase = v.get("phase")
                except Exception:
                    note = verdict[:120]
                post_kanban("critical" if crit else "ok", "supervisor-tick", note, phase=phase)
            else:
                post_kanban("warning", "supervisor-tick",
                            "Kimi call unavailable (check OPENROUTER_API_KEY).")

            write_r2_heartbeat({
                "ts": tick.isoformat(), "agent": AGENT_NAME, "stage": stage,
                "status": "critical" if crit else "ok", "note": note,
            })
        except Exception as e:
            print(f"[supervisor] loop error: {e}", flush=True)
            try:
                post_kanban("warning", "supervisor-error", f"loop error: {e}")
            except Exception:
                pass
        print(f"[{tick.isoformat()}] tick done", flush=True)
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
