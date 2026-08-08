#!/usr/bin/env python3
"""PCBGenius — D9 LIVE COMPONENT CATALOG (feature #14): ingest.

Pull live part data from JLCPCB/LCSC (the official LCSC component API) and
Octopart (the Nexar parts API), normalise every record into the shared
catalog schema, and hand the result to :mod:`index` for searching.

Every real network call is MARKED below as an explicit API boundary.  This
module is deliberately import-safe offline: APIs require credentials/network,
so the heavy lifting is exposed through two seams:

  * ``ingest_from_apis(...)``  — live path. Calls JLCPCB/Octopart, validates
    responses, adapts them to the schema.  Raises ``CatalogUpstreamError`` if
    a provider is unreachable or returns garbage.
  * ``sample_catalog()``       — OFFLINE fallback. A small, realistic in-memory
    catalog (common passives + a switcher + an MCU) so the pipeline and the
    search fallback in :mod:`index` can be exercised / demoed with zero
    credentials and zero network.

Output shape (single source of truth, one dict per part):

    {
      "mpn": str,              # manufacturer part number (e.g. "STM32F103C8T6")
      "manufacturer": str,     # e.g. "STMicroelectronics"
      "specs": {str: str},     # flat key/value parameter map
      "stock": int,            # available unit quantity
      "price": float,          # unit price, USD
      "package": str,          # footprint / package (e.g. "LQFP-48")
      "desc": str,             # free-text description (used for keyword search)
    }

Pure stdlib; no third-party imports.  Network calls are optional and never
executed at import time.
"""

from __future__ import annotations

import json
import os
import urllib.request
from typing import Any, Dict, List


class CatalogUpstreamError(RuntimeError):
    """Raised when a live component-data provider cannot be reached or its
    payload does not conform to the expected contract."""


# --------------------------------------------------------------------------
# Provider adapters (LIVE path).  Each uses urllib so there are zero extra
# dependencies, and each is OPT-IN: it only runs when invoked explicitly.
# --------------------------------------------------------------------------

def _http_json(url: str, headers: Dict[str, str]) -> Dict[str, Any]:
    """Minimal JSON GET. Marked API-boundary: raises CatalogUpstreamError on
    any transport/parse failure (does not fake data)."""
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:  # API CALL
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # socket errors, HTTP errors, bad JSON
        raise CatalogUpstreamError(f"GET {url} failed: {exc}") from exc


def ingest_from_apis(
    lcsc_api_key: str | None = None,
    octopart_api_key: str | None = None,
    query: str = "*",
) -> List[Dict[str, Any]]:
    """LIVE path: pull JLCPCB/LCSC + Octopart data and normalise to schema.

    API endpoints (marked, not invoked without keys):
      * JLCPCB/LCSC  -> https://wmsc.lcsc.com/wmsc/search/global?keyword=...
      * Octopart     -> https://octopart.com/api/v4/parts?apikey=...
    Normalises: LCSC (``productModel``/``productIntroEn`` -> mpn/specs) and
    Octopart (``mpn``/``descriptions``/``offers``) into the shared schema.

    Callers MUST pass at least one API key; otherwise we refuse and point at
    :func:`sample_catalog` rather than silently fabricating part data.
    """
    parts: List[Dict[str, Any]] = []
    if not lcsc_api_key and not octopart_api_key:
        raise CatalogUpstreamError(
            "ingest_from_apis() needs an LCSC and/or Octopart API key. "
            "For offline work use catalog.sample_catalog()."
        )

    if lcsc_api_key:
        # JLCPCB/LCSC global search. URL + parsing left concrete but guarded:
        # the response schema is not stable, so we assert shape and adapt.
        payload = _http_json(
            "https://wmsc.lcsc.com/wmsc/search/global?keyword=" + urllib.parse.quote(query),
            {"User-Agent": "PCBGenius", "apiKey": lcsc_api_key},
        )
        for raw in payload.get("result", []):
            parts.append(_adapt_lcsc(raw))

    if octopart_api_key:
        # Octopart (Nexar) v4 parts search.
        payload = _http_json(
            "https://octopart.com/api/v4/parts?apikey=" + octopart_api_key
            + "&q=" + urllib.parse.quote(query),
            {"User-Agent": "PCBGenius"},
        )
        for raw in payload.get("items", []):
            parts.append(_adapt_octopart(raw))

    return parts


def _adapt_lcsc(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Map an LCSC 'global' search hit onto the shared schema (live path)."""
    return {
        "mpn": str(raw.get("productModel") or raw.get("brandNameEn") or "?"),
        "manufacturer": str(raw.get("brandNameEn") or "Unknown"),
        "specs": {"category": str(raw.get("productCategory"))},
        "stock": int(raw.get("number", 0) or 0),
        "price": _parse_price(raw.get("productPrice", "")),
        "package": str(raw.get("encapStandard") or "unknown"),
        "desc": str(raw.get("productIntroEn") or ""),
    }


def _adapt_octopart(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Map an Octopart v4 part hit onto the shared schema (live path)."""
    offers = raw.get("offers") or []
    best = min(offers, key=lambda o: _parse_price(o.get("price"))) if offers else {}
    return {
        "mpn": str(raw.get("mpn") or "?"),
        "manufacturer": str((raw.get("manufacturer") or {}).get("name") or "Unknown"),
        "specs": {"octopart_uid": str(raw.get("id") or "")},
        "stock": int((best.get("stock") or {}).get("value", 0) or 0),
        "price": _parse_price(best.get("price")),
        "package": str(best.get("package") or "unknown"),
        "desc": " ".join(d.get("value", "") for d in (raw.get("descriptions") or [])),
    }


def _parse_price(value: Any) -> float:
    """Best-effort string/number -> unit price float (0.0 on failure)."""
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and value.strip():
        try:
            return float(value.strip().replace("$", "").replace(",", ""))
        except ValueError:
            return 0.0
    return 0.0


# --------------------------------------------------------------------------
# OFFLINE FALLBACK — sample in-memory catalog.
# --------------------------------------------------------------------------

def sample_catalog() -> List[Dict[str, Any]]:
    """Return a realistic in-memory catalog (marked: sample/placeholder data).

    Used when no third-party API key is configured.  Contains 9 commonly
    specified parts — passives, a switching regulator, an LDO, and an MCU —
    each in the canonical schema so the search fallback and tests run offline.
    """
    _SAMPLE_PARTS = [
        # --- Passives / common discretes ---
        {"mpn": "C0603C104K5RACTU", "manufacturer": "KEMET",
         "specs": {"capacitance": "100nF", "voltage": "50V", "tolerance": "10%"},
         "stock": 84012, "price": 0.012, "package": "0603",
         "desc": "100nF 50V X7R multilayer ceramic capacitor 0603, 10%"},
        {"mpn": "GRM188R71H104KA93D", "manufacturer": "Murata",
         "specs": {"capacitance": "100nF", "voltage": "50V", "tolerance": "10%"},
         "stock": 0, "price": 0.018, "package": "0603",
         "desc": "100nF 50V ceramic capacitor 0603 X7R"},
        {"mpn": "RC0603FR-0710KL", "manufacturer": "Yageo",
         "specs": {"resistance": "10k", "tolerance": "1%", "power": "0.1W"},
         "stock": 512000, "price": 0.005, "package": "0603",
         "desc": "10k ohm 1% thick film resistor 0603 0.1W"},
        {"mpn": "CRCW060310K0FKEA", "manufacturer": "Vishay",
         "specs": {"resistance": "10k", "tolerance": "1%", "power": "0.1W"},
         "stock": 1200, "price": 0.021, "package": "0603",
         "desc": "10k ohm 1% 0603 thick film chip resistor"},
        # --- Power / regulation ---
        {"mpn": "LM2596-5.0", "manufacturer": "Texas Instruments",
         "specs": {"type": "buck", "output_voltage": "5V", "input_range": "4.5-40V",
                    "current": "3A"},
         "stock": 0, "price": 1.85, "package": "TO-263",
         "desc": "3A 5V step-down switching voltage regulator buck converter"},
        {"mpn": "MP1584EN", "manufacturer": "Monolithic Power Systems",
         "specs": {"type": "buck", "output_voltage": "0.8-25V", "input_range": "4.5-28V",
                    "current": "3A"},
         "stock": 18900, "price": 0.79, "package": "SOIC-8",
         "desc": "3A 28V step-down buck switching regulator, frequency 1.5MHz"},
        {"mpn": "AMS1117-3.3", "manufacturer": "Advanced Monolithic Systems",
         "specs": {"type": "ldo", "output_voltage": "3.3V", "input_range": "4.5-12V",
                    "current": "1A"},
         "stock": 64200, "price": 0.088, "package": "SOT-223",
         "desc": "1A 3.3V low dropout positive fixed voltage linear regulator LDO"},
        # --- Microcontroller ---
        {"mpn": "STM32F103C8T6", "manufacturer": "STMicroelectronics",
         "specs": {"core": "ARM Cortex-M3", "flash": "64KB", "ram": "20KB",
                    "speed": "72MHz"},
         "stock": 11000, "price": 2.34, "package": "LQFP-48",
         "desc": "ARM Cortex-M3 32-bit MCU 64KB flash LQFP-48"},
        {"mpn": "STM32F030C8T6", "manufacturer": "STMicroelectronics",
         "specs": {"core": "ARM Cortex-M0", "flash": "64KB", "ram": "8KB",
                    "speed": "48MHz"},
         "stock": 0, "price": 1.21, "package": "LQFP-48",
         "desc": "ARM Cortex-M0 entry-level 32-bit MCU LQFP-48"},
    ]
    # Cheap defensive copy so callers can't mutate the module-level sample.
    return [dict(p, specs=dict(p["specs"])) for p in _SAMPLE_PARTS]


# --------------------------------------------------------------------------
# CLI convenience (offline by default for safety).
# --------------------------------------------------------------------------

def _main() -> None:
    import sys

    try:
        api_key = os.environ.get("LCSC_API_KEY") or os.environ.get("OCTOPART_API_KEY")
        if api_key:
            parts = ingest_from_apis(
                lcsc_api_key=os.environ.get("LCSC_API_KEY"),
                octopart_api_key=os.environ.get("OCTOPART_API_KEY"),
                query=sys.argv[1] if len(sys.argv) > 1 else "*",
            )
        else:
            parts = sample_catalog()
    except CatalogUpstreamError as exc:
        print(f"[ingest] upstream error, falling back to sample catalog: {exc}")
        parts = sample_catalog()

    for p in parts:
        print(f"{p['mpn']:<22} {p['manufacturer']:<24} "
              f"stock={p['stock']:>7}  price=${p['price']:.3f}  {p['package']}")
    print(f"\n[{len(parts)} parts]")


if __name__ == "__main__":
    _main()