import { Netlist, Violation } from "./contractTypes";

/**
 * A small front-end netlist validator mirroring the contract's validation_rules
 * (Section 1). It feeds the stub Violations panel so users see problems immediately
 * without needing the backend. Runs purely client-side.
 */
export function validateNetlist(netlist: Netlist): Violation[] {
  const violations: Violation[] = [];

  if (netlist.schema_version !== "1.0.0") {
    violations.push({
      rule: "SCHEMA_VERSION",
      severity: "error",
      source: "netlist",
      message: `schema_version must be "1.0.0" (got "${netlist.schema_version}").`,
    });
  }

  // Uniqueness of component.ref
  const refs = netlist.components.map((c) => c.ref);
  const dupRefs = refs.filter((r, i) => refs.indexOf(r) !== i);
  if (dupRefs.length) {
    violations.push({
      rule: "DUPLICATE_REF",
      severity: "error",
      source: "netlist",
      message: `Duplicate component references: ${[...new Set(dupRefs)].join(", ")}.`,
    });
  }

  // Build the set of known nets and known "ref.pin" strings.
  const netNames = new Set(netlist.nets.map((n) => n.name));
  const knownPins = new Set<string>();
  for (const c of netlist.components) for (const p of c.pins) knownPins.add(`${c.ref}.${p.name}`);

  for (const c of netlist.components) {
    for (const p of c.pins) {
      if (!netNames.has(p.net)) {
        violations.push({
          rule: "PIN_HANGS",
          severity: "error",
          source: `${c.ref}.${p.name}`,
          message: `Pin ${c.ref}.${p.name} references unknown net "${p.net}".`,
        });
      }
    }
  }

  // Every net's "ref.pin" strings must resolve to a real component+pin.
  for (const n of netlist.nets) {
    for (const rp of n.pins) {
      if (!knownPins.has(rp)) {
        violations.push({
          rule: "NET_PIN_UNRESOLVED",
          severity: "error",
          source: `net ${n.name}`,
          message: `Net "${n.name}" references "${rp}" which does not exist.`,
        });
      }
    }
  }

  // Must have at least one ground and one power net.
  const hasClass = (cls: string) => netlist.nets.some((n) => n.class === cls);
  if (!hasClass("ground")) {
    violations.push({
      rule: "NO_GROUND",
      severity: "error",
      source: "netlist",
      message: "Design has no net with class 'ground'. Add one ground net.",
    });
  }
  if (!hasClass("power")) {
    violations.push({
      rule: "NO_POWER",
      severity: "error",
      source: "netlist",
      message: "Design has no net with class 'power'. Add one power rail.",
    });
  }

  return violations;
}