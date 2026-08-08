"""Python port of the Wave-A contract validator (pcbgenius-frontend/src/validate.ts).

The TS frontend runs `validateNetlist` client-side; this module mirrors the same
rules (frozen interface contract v1.0.0, Section 1) so the Python iteration
engine can reject LLM output before it is ever shown to the user.

Returns: list of violation dicts shaped like the frontend Violation type:
  {"rule": str, "severity": "error"|"warning"|"info", "source": str, "message": str}
An empty list means the netlist is valid.
"""

SCHEMA_VERSION = "1.0.0"


def validate_netlist(netlist: dict) -> list[dict]:
    """Validate a netlist dict. Returns [] when valid, else violation dicts."""
    violations: list[dict] = []

    if netlist.get("schema_version") != SCHEMA_VERSION:
        violations.append({
            "rule": "SCHEMA_VERSION",
            "severity": "error",
            "source": "netlist",
            "message": f'schema_version must be "{SCHEMA_VERSION}" '
                       f'(got "{netlist.get("schema_version")}").',
        })

    components = netlist.get("components", [])
    nets = netlist.get("nets", [])

    # Uniqueness of component.ref
    refs = [c.get("ref") for c in components]
    dup_refs = [r for i, r in enumerate(refs) if r in refs[:i]]
    if dup_refs:
        violations.append({
            "rule": "DUPLICATE_REF",
            "severity": "error",
            "source": "netlist",
            "message": f"Duplicate component references: {sorted(set(dup_refs))}.",
        })

    # Known nets and known "ref.pin" strings.
    net_names = {n.get("name") for n in nets}
    known_pins = {
        f"{c.get('ref')}.{p.get('name')}"
        for c in components
        for p in c.get("pins", [])
    }

    for c in components:
        ref = c.get("ref")
        for p in c.get("pins", []):
            pin_name = p.get("name")
            if p.get("net") not in net_names:
                violations.append({
                    "rule": "PIN_HANGS",
                    "severity": "error",
                    "source": f"{ref}.{pin_name}",
                    "message": f'Pin {ref}.{pin_name} references unknown net "{p.get("net")}".',
                })

    # Every net's "ref.pin" strings must resolve to a real component+pin.
    for n in nets:
        for rp in n.get("pins", []):
            if rp not in known_pins:
                violations.append({
                    "rule": "NET_PIN_UNRESOLVED",
                    "severity": "error",
                    "source": f"net {n.get('name')}",
                    "message": f'Net "{n.get("name")}" references "{rp}" which does not exist.',
                })

    # Must have at least one ground and one power net.
    classes = {n.get("class") for n in nets}
    if "ground" not in classes:
        violations.append({
            "rule": "NO_GROUND",
            "severity": "error",
            "source": "netlist",
            "message": "Design has no net with class 'ground'. Add one ground net.",
        })
    if "power" not in classes:
        violations.append({
            "rule": "NO_POWER",
            "severity": "error",
            "source": "netlist",
            "message": "Design has no net with class 'power'. Add one power rail.",
        })

    return violations


def is_valid(netlist: dict) -> bool:
    """Convenience: True when the netlist passes the contract validator."""
    return not validate_netlist(netlist)