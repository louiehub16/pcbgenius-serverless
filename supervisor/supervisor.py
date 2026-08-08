# PCBGenius — Always-on Supervisor Orchestrator
# Runs as a Salad vCPU container 24/7. The Kimi K3 "brain" that keeps the
# project going while the user's PC is off.
#
# Loop (every SUPERVISOR_INTERVAL_SEC):
#   1. Read project/pipeline state (R2 via boto3 or local state file)
#   2. Decide current phase/stage + what needs attention
#   3. Call Kimi K3 (OpenRouter) to get supervision verdict / fixes
#   4. POST status to the kanban board (so UI shows live progress)
#   5. Email ONLY on CRITICAL issues (per project rule: no spam)
#   6. Sleep
#
# Single long-lived foreground process — Salad requires the container to
# never exit (a `while True` loop keeps it billed-but-alive correctly).
import json
import os
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Config (from env, all optional except what the loop needs)
# ---------------------------------------------------------------------------
OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY", "")
KIMI_MODEL = os.environ.get("KIMI_MODEL", "moonshotai/kimi-k3")
KANBAN_URL = os.environ.get(
    "KANBAN_URL", "https://kanban.louie-ibunia-va.workers.dev/api/update"
)
AGENT_NAME = os.environ.get("AGENT_NAME", "kimi-supervisor")
INTERVAL = int(os.environ.get("SUPERVISOR_INTERVAL_SEC", "600"))  # 10 min
STATE_FILE = "/state/current_stage.txt"

# ---------------------------------------------------------------------------
# Small HTTP helper
# ---------------------------------------------------------------------------
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
    body = {
        "agent": agent,
        "status": status,       # ok | warning | critical
        "feature": feature,
        "message": message,
    }
    if phase:
        body["phase"] = phase
    s, out = http("POST", KANBAN_URL, body)
    return s, out


def call_kimi(prompt, max_tokens=800):
    if not OPENROUTER_KEY:
        return None
    body = {
        "model": KIMI_MODEL,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are the PCBGenius project supervisor. You monitor an "
                    "autonomous AI-PCB-build pipeline and give terse, actionable "
                    "verdicts. Reply as strict JSON with keys: phase, next_action, "
                    "critical (true/false), note."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": 0.2,
    }
    s, out = http(
        "POST",
        "https://openrouter.ai/api/v1/chat/completions",
        body,
        headers={"Authorization": f"Bearer {OPENROUTER_KEY}"},
        timeout=60,
    )
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


# ---------------------------------------------------------------------------
# Supervisor loop
# ---------------------------------------------------------------------------
def main():
    print(f"[supervisor] starting agent={AGENT_NAME} interval={INTERVAL}s", flush=True)
    post_kanban("ok", "supervisor-up", "Supervisor container online (always-on).")

    failures = 0
    while True:
        tick = datetime.now(timezone.utc).isoformat()
        try:
            stage = read_state()
            prompt = (
                f"Current pipeline stage: {stage}. Agency running under budget "
                f"{os.environ.get('COST_CAP_USD', '90')} USD. No user is present. "
                "What should the project do next? Keep it short."
            )
            verdict = call_kimi(prompt)
            crit = False
            note = "supervisor heartbeat ok"
            if verdict:
                try:
                    v = json.loads(verdict)
                    crit = bool(v.get("critical"))
                    note = v.get("note") or "no note"
                    phase = v.get("phase")
                    post_kanban(
                        "critical" if crit else "ok",
                        "supervisor-tick",
                        note,
                        phase=phase,
                    )
                except Exception:
                    post_kanban("ok", "supervisor-tick", verdict[:120])
            else:
                # No key / failed call -> still report alive so kanban shows it
                post_kanban("warning", "supervisor-tick", "Kimi call unavailable (check OPENROUTER_API_KEY).")
            failures = 0
        except Exception as e:  # never let the loop die
            failures += 1
            try:
                post_kanban("warning", "supervisor-error", f"loop error: {e}")
            except Exception:
                pass
        print(f"[{tick}] tick done, failures={failures}", flush=True)
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
