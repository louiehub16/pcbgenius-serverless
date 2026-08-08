#!/usr/bin/env python3
"""
PCBGenius D4 — generate_firmware
================================
Builds a firmware-generation prompt from a contract netlist + derived pin map,
calls an LLM via OpenRouter, and returns C/C++ (Arduino) source.

Flow:
    netlist + mcu + functionality
        -> derive_pinmap()          (pinmap.py)
        -> build_prompt()           (this module)
        -> call_openrouter()        (MODEL API CALL SITE — clearly marked)
        -> on failure / empty       -> deterministic template_fallback()

The API call site is marked with the sentinel constants OPENROUTER_CALL_START /
OPENROUTER_CALL_END plus a comment block, so it is trivial to grep, mock, or
redirect to a local server. When no key/response is available the system STILL
produces valid deterministic Arduino C++ derived from the actual pin map, so
firmware generation never hard-fails.
"""

from __future__ import annotations

import json
import os
import urllib.request
import urllib.error
from typing import Any, Dict, Optional

from pinmap import derive_pinmap, find_mcu, format_pinmap, PinMap

CONTRACT_VERSION = "1.0.0"

# ── OpenRouter endpoint / model ────────────────────────────────────────────
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = os.environ.get("PCBGENIUS_FIRMWARE_MODEL", "deepseek/deepseek-chat")

# Markers delimiting the single live API call site. Grep for these to find it.
OPENROUTER_CALL_START = "__PCBGENIUS_OPENROUTER_CALL_SITE_START__"
OPENROUTER_CALL_END = "__PCBGENIUS_OPENROUTER_CALL_SITE_END__"


def _is_mcu_like(value: str) -> bool:
    """Heuristic used only for prompt language selection (not for pin mapping)."""
    v = (value or "").lower()
    return any(k in v for k in ("atmega", "uno", "nano", "tiny", "arduino"))


def build_prompt(netlist: Dict[str, Any], pinmap: PinMap, functionality: str) -> str:
    """Compose a deterministic, self-contained firmware-generation prompt."""
    comps = [
        {"ref": c.get("ref"), "type": c.get("type"), "value": c.get("value"),
         "package": c.get("package"), "pins": c.get("pins", [])}
        for c in netlist.get("components", [])
    ]
    ctx = {
        "schema_version": netlist.get("schema_version", CONTRACT_VERSION),
        "metadata": netlist.get("metadata", {}),
        "components": comps,
        "nets": netlist.get("nets", []),
    }
    return f"""You are a firmware engineer. Generate a complete, compilable, single-file
Arduino/C++ sketch for the microcontroller in the netlist below.

HARD REQUIREMENTS:
- Output ONLY the C/C++ source between ``` markers. No prose.
- Include a valid `setup()` and `loop()`, and #define the MCU pins used.
- Wire I/O exactly according to the pin map (which MCU pin connects to which peripheral).
- Do not invent hardware not present in the netlist/pin map.
- Drive LEDs / read sensors / toggle outputs to implement the requested functionality.

REQUESTED FUNCTIONALITY:
{functionality}

PIN MAP:
{format_pinmap(pinmap)}

NETLIST (context):
{json.dumps(ctx, default=str)}
"""


def call_openrouter(prompt: str,
                    model: str = DEFAULT_MODEL,
                    api_key: Optional[str] = None,
                    timeout: int = 90) -> Optional[str]:
    """Call the OpenRouter chat-completions API and return the assistant text.

    This is THE model API call site for firmware generation. It is bracketed by
    OPENROUTER_CALL_START / OPENROUTER_CALL_END sentinels and isolated in its own
    function so it can be mocked or redirected without touching prompt/fallback
    logic. Returns None on any failure — callers must fall back gracefully.
    """
    key = api_key if api_key is not None else os.environ.get("OPENROUTER_API_KEY", "")
    if not key:
        return None
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 1600,
        "temperature": 0.2,
    }
    req = urllib.request.Request(
        OPENROUTER_URL,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 Chrome/126.0",
        },
    )
    # ---- MODEL API CALL SITE ----------------------------------------------
    print(OPENROUTER_CALL_START)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            content = data["choices"][0]["message"]["content"]
            return content if content and content.strip() else None
    except (urllib.error.URLError, urllib.error.HTTPError, KeyError, IndexError, json.JSONDecodeError,
            OSError, TimeoutError) as exc:  # pragma: no cover - defensive
        print(f"[firmware-gen] OpenRouter call failed: {exc}")
        return None
    finally:
        print(OPENROUTER_CALL_END)
    # ---- END MODEL API CALL SITE ------------------------------------------


def _gpio_pins(pinmap: PinMap):
    """[(mcu_pin, net, refs)] for GPIO-role assignments only."""
    return [
        (a.pin, a.net, a.peripherals)
        for a in pinmap.assignments if a.role == "gpio"
    ]


def _build_notes(pinmap: PinMap, fallback: bool) -> str:
    if fallback:
        return (
            f"Generated from deterministic template fallback (no model response). "
            f"MCU={pinmap.mcu_ref} ({pinmap.mcu_value}), "
            f"{len(_gpio_pins(pinmap))} GPIO pin(s) wired. Compile with arduino-cli for the target board."
        )
    return (
        f"Generated by model (OpenRouter). MCU={pinmap.mcu_ref} ({pinmap.mcu_value}), "
        f"{len(_gpio_pins(pinmap))} GPIO pin(s) wired. Compile with arduino-cli for the target board."
    )


def template_fallback(pinmap: PinMap, functionality: str) -> str:
    """Deterministic Arduino/C++ source derived purely from the pin map.

    Used when the model call is unavailable or returns nothing. Produces a real,
    compilable sketch that #defines every wired MCU pin, configures GPIO pins as
    outputs, and drives the peripherals (blinks LEDs, writes sensor states).
    Identical input -> identical output.
    """
    gpio = _gpio_pins(pinmap)
    lines = [
        f"// {pinmap.mcu_value} — deterministic firmware (template fallback)",
        "// Auto-generated from the netlist pin map; no model call involved.",
        f"// Functionality: {functionality.replace(chr(10), ' ')}",
        "",
        f"#define MCU_REF \"{pinmap.mcu_ref}\"",
        f"#define MCU_VALUE \"{pinmap.mcu_value}\"",
    ]
    # pin constants (avoid duplicate names defensively)
    seen = set()
    for pin, net, _refs in gpio:
        macro = f"PIN_{net.replace(' ', '_').upper()}"
        base, i = macro, 2
        while base in seen:
            base = f"{macro}_{i}"; i += 1
        seen.add(base)
        lines.append(f"#define {base} {pin}  // net {net}")
    if not gpio:
        lines.append("// No GPIO peripherals connected — MCU is a passive controller.")
    lines += [
        "",
        "void setup(void) {",
    ]
    if gpio:
        lines.append("  // configure wired GPIO pins");
        for pin, _net, _refs in gpio:
            lines.append(f"  pinMode({pin}, OUTPUT);")
    else:
        lines.append("  // nothing to drive")
    lines += [
        "}",
        "",
        "void loop(void) {",
    ]
    if gpio:
        for i, (pin, _net, refs) in enumerate(gpio):
            tag = refs[0] if refs else _net
            lines.append(f"  digitalWrite({pin}, HIGH);  // drive {tag}")
        lines.append("  delay(500);")
        for pin, _net, _refs in gpio:
            lines.append(f"  digitalWrite({pin}, LOW);   // release {_net}")
        lines.append("  delay(500);")
    else:
        lines.append("  delay(1000);  // idle")
    lines.append("}")
    return "\n".join(lines)


def _strip_markdown(content: str) -> str:
    """Remove ``` fences the model may wrap around its source."""
    content = content.strip()
    if content.startswith("```"):
        # drop the first fence + optional language tag
        end = content.find("\n")
        content = content[end + 1:] if end != -1 else ""
        if content.rstrip().endswith("```"):
            content = content.rstrip()[:-3]
    return content.strip()


def generate_firmware(netlist: Dict[str, Any],
                      mcu: str,
                      functionality: str,
                      model: str = DEFAULT_MODEL,
                      api_key: Optional[str] = None,
                      use_model: bool = True) -> Dict[str, Any]:
    """Top-level entry point. Returns the contract FirmwareResult shape:
    {language, source, build_notes}. Falls back to the deterministic template
    whenever the model path is disabled, unconfigured, or returns nothing."""
    pinmap = derive_pinmap(netlist, mcu)
    is_arduino = _is_mcu_like(pinmap.mcu_value) or _is_mcu_like(mcu)
    language = "C++ (Arduino)" if is_arduino else "C/C++ (Arduino-compatible)"

    source = None
    fallback = True
    if use_model:
        prompt = build_prompt(netlist, pinmap, functionality)
        content = call_openrouter(prompt, model=model, api_key=api_key)
        if content:
            candidate = _strip_markdown(content)
            if candidate:
                source = candidate
                fallback = False
    if source is None:
        source = template_fallback(pinmap, functionality)

    return {
        "language": language,
        "source": source,
        "build_notes": _build_notes(pinmap, fallback),
    }


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="Generate firmware from a contract netlist.")
    ap.add_argument("netlist", help="path to netlist JSON (bare or wrapped row)")
    ap.add_argument("--mcu", default="", help="MCU ref/value (e.g. ATtiny85)")
    ap.add_argument("--functionality", default="blink any connected LEDs",
                    help="requested firmware behaviour")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--api-key", default=os.environ.get("OPENROUTER_API_KEY", ""))
    ap.add_argument("--no-model", action="store_true", help="force deterministic fallback")
    ap.add_argument("--print-pinmap", action="store_true", help="print pin map and exit")
    a = ap.parse_args()

    from pinmap import load_netlist, format_pinmap, derive_pinmap
    nl = load_netlist(a.netlist)
    if a.print_pinmap:
        print(format_pinmap(derive_pinmap(nl, a.mcu)))
        return
    result = generate_firmware(
        nl, a.mcu, a.functionality,
        model=a.model, api_key=a.api_key or None, use_model=not a.no_model,
    )
    print(f"language   : {result['language']}")
    print(f"build_notes: {result['build_notes']}")
    print("-" * 60)
    print(result["source"])


if __name__ == "__main__":
    main()