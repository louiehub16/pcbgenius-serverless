"""engine.py — NL iteration with diffs (feature #23).

Pipeline:
    {netlist, user_request}
        -> prompt an LLM (OpenRouter chat completions, stdlib urllib only)
        -> the model returns a NEW netlist JSON
        -> validate against the contract validator (validate.py, port of
           pcbgenius-frontend/src/validate.ts)
        -> on failure: reject, append the violations to the prompt as feedback,
           and retry (max 2 retries = 3 attempts total)
        -> on success: compute a structured diff (diff_render.py) and return
           {netlist, diff, attempts, raw}

No npm/docker/git required. Pure stdlib.
"""

import json
import os
import re
import urllib.error
import urllib.request
from copy import deepcopy

from validate import validate_netlist
from diff_render import render_diff, count_changes

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "deepseek/deepseek-chat"
MAX_RETRIES = 2  # reject + retry, max 2, then raise an honest error.


class IterationError(RuntimeError):
    """Raised when the LLM cannot produce a valid netlist or the API call fails."""


# ---------------------------------------------------------------------------
# Prompt building
# ---------------------------------------------------------------------------

SCHEMA_DOC = """\
The output MUST be a single JSON object matching this netlist contract (v1.0.0):
{
  "schema_version": "1.0.0",
  "metadata": {"design_name": str, "description": str, "board_layers": int,
               "created_by": str, "target_fab": "jlcpcb"|"pcbway"|null},
  "components": [{"ref": str, "type": "resistor"|"capacitor"|"inductor"|"diode"|
                  "led"|"transistor"|"ic"|"connector"|"power"|"crystal"|"switch",
                  "value": str, "package": str, "mpn": str|null,
                  "pins": [{"number": str, "name": str, "net": str}],
                  "properties": {}}],
  "nets": [{"name": str, "pins": ["REF.PIN"], "class":
            "power"|"ground"|"signal"|"clock"|"analog"|"digital"}]
}
Contract rules that MUST hold:
  - schema_version is exactly "1.0.0"
  - component refs are unique and non-empty
  - every pin's net must exist as a net name (no hanging pins)
  - every net's pins list must reference real "REF.PIN" pairs that exist
  - the design MUST contain at least one net of class "ground" and one of class "power"
  - nets/pins must stay consistent: renaming a net requires updating every pin
    that references it and the net's own pins list
Return ONLY the JSON. No prose, no markdown fences.
"""


def build_prompt(netlist: dict, user_request: str, feedback: list[str] | None = None) -> str:
    """Assemble the LLM prompt from the current netlist + user request.

    `feedback` carries validator violations from a rejected attempt so the next
    try can fix them.
    """
    parts = [
        "You are PCBGenius, a PCB netlist editor. Apply the user's requested ",
        "change to the CURRENT netlist and return the FULL updated netlist.\n",
        "Keep everything you were not asked to change identical. If the request ",
        "is ambiguous, make the least surprising minimal edit.\n\n",
        SCHEMA_DOC,
        "\n=== CURRENT NETLIST (JSON) ===\n",
        json.dumps(netlist, indent=2),
        "\n\n=== USER REQUEST ===\n",
        user_request.strip(),
    ]
    if feedback:
        parts += [
            "\n\n=== YOUR PREVIOUS ATTEMPT WAS REJECTED ===\n",
            "The validator reported these problems (fix ALL of them):\n",
            "\n".join(f"- {v['rule']}: {v['message']}" for v in feedback),
        ]
    return "".join(parts)


# ---------------------------------------------------------------------------
# OpenRouter call (stdlib urllib)
# ---------------------------------------------------------------------------

def call_llm(prompt: str, api_key: str | None = None, model: str | None = None,
             timeout: int = 60) -> str:
    """POST the prompt to OpenRouter and return the raw assistant text."""
    key = api_key or os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise IterationError(
            "OPENROUTER_API_KEY is not set. Set the env var or pass api_key=."
        )
    mdl = model or os.environ.get("OPENROUTER_MODEL") or DEFAULT_MODEL

    body = {
        "model": mdl,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }
    req = urllib.request.Request(
        OPENROUTER_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://pcbgenius.local",
            "X-Title": "pcbgenius-iterate",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise IterationError(f"OpenRouter HTTP {e.code}: {e.read().decode('utf-8', 'replace')}") from e
    except urllib.error.URLError as e:
        raise IterationError(f"OpenRouter unreachable: {e.reason}") from e

    try:
        return payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        raise IterationError(f"Unexpected OpenRouter response shape: {e}") from e


def parse_llm_json(raw: str) -> dict:
    """Extract a JSON object from LLM output (tolerates markdown fences/whitespace)."""
    text = raw.strip()
    # Strip ```json ... ``` fences if present.
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise IterationError("LLM output contained no JSON object.")
    return json.loads(text[start:end + 1])


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def iterate_netlist(netlist: dict, user_request: str, api_key: str | None = None,
                    model: str | None = None, max_retries: int = MAX_RETRIES) -> dict:
    """Apply a natural-language edit to a netlist via an LLM.

    Returns:
      {"netlist": <new valid netlist>, "diff": <render_diff output>,
       "counts": {"added","removed","modified"}, "attempts": int, "raw": str}

    Raises IterationError on API failure or when every attempt is rejected by
    the validator (honest error, never a silently-invalid netlist).
    """
    current = deepcopy(netlist)
    feedback: list[dict] = []
    attempts = 0

    for attempt in range(max_retries + 1):  # 1 initial + max_retries retries
        attempts += 1
        prompt = build_prompt(current, user_request, feedback)
        raw = call_llm(prompt, api_key=api_key, model=model)
        try:
            candidate = parse_llm_json(raw)
        except IterationError as e:
            # Not a JSON parse shape we can retry against the validator with:
            # feed it back as feedback and retry.
            feedback = [{"rule": "LLM_JSON", "severity": "error", "source": "llm",
                         "message": str(e)}]
            continue

        violations = validate_netlist(candidate)
        if not violations:
            diff = render_diff(current, candidate)
            return {
                "netlist": candidate,
                "diff": diff,
                "counts": count_changes(diff),
                "attempts": attempts,
                "raw": raw,
            }
        feedback = violations  # feed validator violations back for the retry

    raise IterationError(
        f"LLM produced an invalid netlist after {max_retries + 1} attempts "
        f"({attempts} call(s)). Last validator findings: "
        + "; ".join(f"{v['rule']} {v['message']}" for v in feedback)
    )


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 3:
        print("usage: python engine.py <netlist.json> \"<user request>\" [model]",
              file=sys.stderr)
        sys.exit(2)
    with open(sys.argv[1], "r", encoding="utf-8") as fh:
        net = json.load(fh)
    model = sys.argv[3] if len(sys.argv) > 3 else None
    try:
        result = iterate_netlist(net, sys.argv[2], model=model)
    except IterationError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(result["diff"], indent=2))
    with open("iterated_netlist.json", "w", encoding="utf-8") as fh:
        json.dump(result["netlist"], fh, indent=2)
    print(f"\nattempts={result['attempts']} counts={result['counts']} "
          f"-> wrote iterated_netlist.json", file=sys.stderr)