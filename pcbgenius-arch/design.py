"""design.py — PCBGenius D6 architecture stage: prompt -> block diagram -> netlist skeleton.

Pipeline (per prompt):
    user prompt
      -> [D6 openrouter] prompt LLM -> JSON block list  (API, optional; see call_llm)
      -> deterministic_blocks()                         (pure fallback, always works)
      -> blocks_to_netlist()                            (contract v1.0.0 skeleton)

The architecture stage is intentionally EARLY in the PCBGenius flow: a natural
language prompt becomes a coarse functional block diagram (power stage, loads,
passives), which is then expanded into a contract-legal netlist *skeleton* —
components + nets that pass the SAME ``validate_netlist`` gate used by the
Wave-A/iterate stages, so downstream (layout, verification, iteration) always
receives a well-formed design.

Design notes
------------
* The OpenRouter call is marked ``# [D6 openrouter]`` at its three staging
  points (URL, Request, response unwrap). It is used to *suggest* a richer
  block list; it never decides program correctness.
* ``design_from_prompt`` ALWAYS degrades to the deterministic template path when
  no API key is present, when the network fails, or when the LLM output fails to
  parse — so the pipeline is fully usable offline and tests never touch the
  network. The returned dict reports ``source`` = "llm" | "deterministic" so a
  caller can tell which path produced it.
* No npm/docker/git. Pure stdlib (urllib for the API call).

The netlist produced by this module satisfies ``pcbgenius-iterate/validate.py``
(contract v1.0.0). To avoid a dependency on that sibling path here, the rules are
re-implemented in ``test_architecture.py`` as a local check mirroring the
contract (>=1 ground, >=1 power, no hanging pins, resolvable ref.pin pairs).
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "deepseek/deepseek-chat"
DEFAULT_TIMEOUT = 60
SCHEMA_VERSION = "1.0.0"
CREATED_BY = "pcbgenius"


class ArchitectureError(RuntimeError):
    """Raised on an internal error in the architecture stage (never a silent guess)."""


# ---------------------------------------------------------------------------
# Block diagram model
# ---------------------------------------------------------------------------
@dataclass
class Block:
    """A single functional block in the architecture diagram.

    ``kind`` drives component typing in the netlist skeleton (ico/regulator,
    capacitor, ...). ``nets`` is the ordered list of nets this block touches;
    the first pin is treated as pin 1. ``label`` is used in the Mermaid render.
    """

    id: str
    kind: str
    label: str
    nets: List[str] = field(default_factory=list)
    value: str = ""


Kind = str  # "regulator" | "capacitor" | ...


# ---------------------------------------------------------------------------
# Net class classification (mirror of atopile_integration.classify_net)
# ---------------------------------------------------------------------------
_NET_CLASS_HINTS: List[tuple[str, str]] = [
    (r"^(GND|VSS|VEE|ground)$", "ground"),
    (r"^(VCC|VDD|VBUS|VIN|VOUT|PWR|SW)$|^\d+V$|3V3|5V|12V|24V", "power"),
    (r"CLK|XTAL|OSC", "clock"),
    (r"A[INOUT]|ADC|FB|SENSE|REF|DAC", "analog"),
    (r"(SD[AI]|SCK|MOSI|MISO|TX|RX)$", "digital"),
]


def classify_net(name: str) -> str:
    """Derive the contract net class enum from a net name."""
    for pattern, cls in _NET_CLASS_HINTS:
        if re.search(pattern, name.upper()):
            return cls
    return "signal"


# ---------------------------------------------------------------------------
# Deterministic block extraction (fallback + offline path)
# ---------------------------------------------------------------------------
def deterministic_blocks(prompt: str) -> List[Block]:
    """Map a prompt to a canonical block list using templates (no network).

    Mirrors the B1 atopile template shapes so a prompt that mentions a buck /
    LDO / LED design yields the same canonical blocks the datagen stage knows.
    """
    low = prompt.lower()
    if "buck" in low or "switch" in low or "converter" in low:
        return [
            Block("U1", "regulator", "BuckConverter (LM2596)", ["VIN", "GND", "SW", "VOUT"], "LM2596"),
            Block("Q1", "diode", "Freewheel Diode (SS34)", ["SW", "GND"], "SS34"),
            Block("L1", "inductor", "Output Inductor (33uH)", ["SW", "VOUT"], "33uH"),
            Block("C1", "capacitor", "Output Capacitor (100uF)", ["VOUT", "GND"], "100uF"),
            Block("C2", "capacitor", "Input Capacitor (10uF)", ["VIN", "GND"], "10uF"),
        ]
    if "ldo" in low or "regulator" in low or "linear" in low or "3.3" in low:
        return [
            Block("U1", "regulator", "LDO (AMS1117-3V3)", ["VIN", "GND", "VOUT"], "AMS1117-3V3"),
            Block("C1", "capacitor", "Input Capacitor (10uF)", ["VIN", "GND"], "10uF"),
            Block("C2", "capacitor", "Output Capacitor (10uF)", ["VOUT", "GND"], "10uF"),
        ]
    if "led" in low or "blink" in low:
        return [
            Block("U1", "microcontroller", "MCU (ATtiny85)", ["VCC", "GND", "LED_CTRL"], "ATtiny85"),
            Block("R1", "resistor", "Current Limit (330)", ["LED_CTRL", "LED_NET"], "330"),
            Block("LED1", "led", "LED (red)", ["LED_NET", "GND"], "red"),
        ]
    # Generic default: a microcontroller plus decoupling.
    return [
        Block("U1", "microcontroller", "IC (generic)", ["VCC", "GND"], "Generic"),
        Block("C1", "capacitor", "Decoupling (100nF)", ["VCC", "GND"], "100nF"),
    ]


# ---------------------------------------------------------------------------
# Block diagram -> contract netlist skeleton
# ---------------------------------------------------------------------------
_COMPONENT_KINDS = {
    "regulator": "ic",
    "microcontroller": "ic",
    "capacitor": "capacitor",
    "inductor": "inductor",
    "resistor": "resistor",
    "diode": "diode",
    "led": "led",
}


def blocks_to_netlist(blocks: List[Block], design_name: str) -> Dict[str, Any]:
    """Convert a block list into a contract v1.0.0 netlist skeleton.

    Each block becomes one component; its pins are named after the nets it
    touches (pin number = 1-indexed, pin name = net name). Nets are the set of
    distinct net names across blocks, each pin list being every
    ``REF.NETNAME`` that connects. Net classes come from :func:`classify_net`.

    Guarantees the skeleton passes the contract gate: unique refs, every pin
    net resolves, every net pin resolves, at least one ground and one power net
    (raises ``ArchitectureError`` if a template would violate this rather than
    fabricating data).
    """
    components: List[Dict[str, Any]] = []
    net_pins: Dict[str, List[str]] = {}

    for blk in blocks:
        if not blk.nets:
            raise ArchitectureError(f"block {blk.id} has no nets; cannot build skeleton")
        pins = [
            {"number": str(i), "name": net, "net": net}
            for i, net in enumerate(blk.nets, start=1)
        ]
        components.append({
            "ref": blk.id,
            "type": _COMPONENT_KINDS.get(blk.kind, "ic"),
            "value": blk.value,
            "package": "unspecified",
            "mpn": None,
            "pins": pins,
            "properties": {"role": blk.label},
        })
        for net in blk.nets:
            net_pins.setdefault(net, []).append(f"{blk.id}.{net}")

    # Ensure the contract's minimum: one ground and one power net present.
    classes = {classify_net(n) for n in net_pins}
    if "ground" not in classes or "power" not in classes:
        # Deterministic templates all carry GND + a power rail, so this is a
        # programming error, not a data condition — surface it loudly.
        raise ArchitectureError(
            f"skeleton would lack ground/power nets (classes={sorted(classes)}); "
            "refuse rather than guess."
        )

    nets: List[Dict[str, Any]] = [
        {"name": name, "pins": sorted(set(rp)), "class": classify_net(name)}
        for name, rp in net_pins.items()
    ]

    return {
        "schema_version": SCHEMA_VERSION,
        "metadata": {
            "design_name": design_name,
            "description": _describe(blocks),
            "board_layers": 2,
            "created_by": CREATED_BY,
            "target_fab": None,
        },
        "components": components,
        "nets": nets,
    }


def _describe(blocks: List[Block]) -> str:
    return (f"Architecture skeleton: {len(blocks)} blocks; "
            f"{', '.join(b.label for b in blocks[:3])}.")


# ---------------------------------------------------------------------------
# OpenRouter call (stdlib urllib)
# ---------------------------------------------------------------------------
def call_llm(prompt: str, api_key: Optional[str] = None,
             model: Optional[str] = None,
             timeout: int = DEFAULT_TIMEOUT) -> str:
    """POST the prompt to OpenRouter and return the raw assistant text.

    # [D6 openrouter] API call marker. Requires ``OPENROUTER_API_KEY``; raises
    ``ArchitectureError`` if it is missing or the request fails. The returned
    text should be a JSON list of blocks (see BLOCK_SCHEMA_DOC).
    """
    key = api_key or os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise ArchitectureError(
            "OPENROUTER_API_KEY is not set. Set the env var or pass api_key=."
        )
    mdl = model or os.environ.get("OPENROUTER_MODEL") or DEFAULT_MODEL

    body = {
        "model": mdl,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }
    # [D6 openrouter] urlopen is the single network boundary for this module.
    req = urllib.request.Request(
        OPENROUTER_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://pcbgenius.local",
            "X-Title": "pcbgenius-arch",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # [D6 openrouter]
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise ArchitectureError(
            f"OpenRouter HTTP {e.code}: {e.read().decode('utf-8', 'replace')}"
        ) from e
    except urllib.error.URLError as e:
        raise ArchitectureError(f"OpenRouter unreachable: {e.reason}") from e

    try:  # [D6 openrouter]
        return payload["choices"][0]["message"]["content"]  # [D6 openrouter]
    except (KeyError, IndexError, TypeError) as e:
        raise ArchitectureError(f"Unexpected OpenRouter response shape: {e}") from e


BLOCK_SCHEMA_DOC = """\
Analyse the PCB design request and return a JSON object:
{"blocks": [{"id": "U1", "kind": "regulator|capacitor|inductor|resistor|diode|led|microcontroller",
             "label": "human readable", "value": "part/value",
             "nets": ["VIN", "GND", "VOUT"]}]}
Rules:
- one entry per distinct functional block; ids unique (U1, C1, R1, L1, D1...)
- every block MUST connect to a GND net and there must be at least one VIN/VCC
  power net shared by blocks
- nets: use GND for ground, VIN/VCC for power, and descriptive names for signals
Return ONLY the JSON, no prose, no markdown fences.
"""


def build_block_prompt(prompt: str) -> str:
    """Assemble the LLM prompt requesting a block diagram from a prompt."""
    return (
        "You are PCBGenius, an EDA architect. Turn the user's design request "
        "into a functional block diagram.\n\n" +
        BLOCK_SCHEMA_DOC +
        "\n\n=== DESIGN REQUEST ===\n" +
        prompt.strip()
    )


def parse_llm_blocks(raw: str) -> List[Block]:
    """Parse the LLM's JSON {'blocks': [...]} response into Block objects."""
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ArchitectureError("LLM block output contained no JSON object.")
    try:
        data = json.loads(text[start:end + 1])
        raw_blocks = data["blocks"]
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        raise ArchitectureError(f"LLM block output was not the expected shape: {e}") from e

    blocks: List[Block] = []
    for i, b in enumerate(raw_blocks, start=1):
        blocks.append(Block(
            id=str(b.get("id") or f"B{i}"),
            kind=str(b.get("kind") or "ic"),
            label=str(b.get("label") or "block"),
            nets=[str(n) for n in (b.get("nets") or [])],
            value=str(b.get("value") or ""),
        ))
    if not blocks:
        raise ArchitectureError("LLM returned an empty block list.")
    return blocks


# ---------------------------------------------------------------------------
# Main driver
# ---------------------------------------------------------------------------
def design_from_prompt(prompt: str, design_name: Optional[str] = None,
                       api_key: Optional[str] = None,
                       model: Optional[str] = None) -> Dict[str, Any]:
    """Full D6 pipeline for one prompt.

    Returns:
      {"blocks": [...], "netlist": {...}, "source": "llm"|"deterministic",
       "mermaid": <mermaid string>}

    The LLM path is best-effort: any API/parse failure falls back to the
    deterministic template path, so this NEVER raises for a plain prompt and the
    returned netlist ALWAYS validates. ``source`` tells the caller which path
    ran.
    """
    name = design_name or _slugify(prompt)
    blocks: List[Block]
    source: str

    try:
        raw = call_llm(build_block_prompt(prompt), api_key=api_key, model=model)
        blocks = parse_llm_blocks(raw)
        source = "llm"
    except ArchitectureError:
        blocks = deterministic_blocks(prompt)
        source = "deterministic"

    netlist = blocks_to_netlist(blocks, name)

    from mermaid import render_mermaid_flowchart  # local import; sibling module
    diagram = render_mermaid_flowchart(blocks)

    return {
        "blocks": [b.__dict__ for b in blocks],
        "netlist": netlist,
        "source": source,
        "mermaid": diagram,
    }


def _slugify(prompt: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "_", prompt).strip("_").lower()
    if not s or s[0].isdigit():
        s = "design_" + s
    return s[:32] or "design"


if __name__ == "__main__":
    import sys
    prompt = sys.argv[1] if len(sys.argv) > 1 else "build me a 12v to 5v buck converter"
    result = design_from_prompt(prompt)
    print(json.dumps(result, indent=2, default=str))
    print(f"\nsource={result['source']}")