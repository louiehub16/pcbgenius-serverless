# pcbgenius-firmware (D4)

Firmware generation for PCBGenius: given a FROZEN CONTRACT netlist the MCU is
identified (a component of `type: "ic"`, e.g. `ATtiny85`) and turned into a
pin map, then a firmware-generation prompt is built and answered by an LLM via
OpenRouter. Maintains a **deterministic C/Arduino template fallback** so
firmware always comes back even when no model/key is available.

## Files
- `pinmap.py` — `find_mcu`, `derive_pinmap`, `format_pinmap`.
  Derives, for every net an MCU pin lands on, the net class, the pin's role
  (`gpio`/`power`/`ground`) and the peripheral refs sharing that net.
- `gen.py` — `build_prompt`, `call_openrouter`, `template_fallback`,
  `generate_firmware` (top-level, returns the contract `FirmwareResult` shape).

## Model API call site
`gen.py::call_openrouter` is the single live network call, bracketed by the
sentinels `OPENROUTER_CALL_START` / `OPENROUTER_CALL_END` and isolated in its
own function so it can be mocked/redirected. It returns `None` on any failure;
callers fall back to the template.

## CLI
```bash
python -m pcbgenius_firmware.gen path/to/netlist.json \
    --mcu ATtiny85 --no-model --print-pinmap
```
`--no-model` forces the deterministic template (no network).

## Notes
- Pure Python stdlib (urllib requests), deterministic by design.
- MCU selection by `--mcu` (ref/value, case-insensitive) else first `ic`.
- No npm/docker/git required to run.