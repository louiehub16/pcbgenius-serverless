import { Component, Net, Violation } from "../contractTypes";
import { validateNetlist } from "../validate";
import type { Netlist } from "../contractTypes";

interface InspectorProps {
  netlist: Netlist;
  selectedId: string | null;
}

/**
 * Property inspector. Clicking a node in the canvas routes the selected node id
 * here; we look it up in the netlist and show component or net details.
 * The panel header also shows a live netlist validation summary.
 */
export function Inspector({ netlist, selectedId }: InspectorProps) {
  const isNet = selectedId?.startsWith("net-");
  const name = selectedId ? selectedId.replace(/^comp-|^net-/, "") : "";

  const component: Component | undefined = isNet
    ? undefined
    : netlist.components.find((c) => c.ref === name);
  const net: Net | undefined = isNet
    ? netlist.nets.find((n) => n.name === name)
    : undefined;

  const violations = validateNetlist(netlist);
  const errorCount = violations.filter((v) => v.severity === "error").length;

  return (
    <aside className="pane inspector">
      <h2>Inspector</h2>

      {!selectedId && (
        <p className="muted">Select a component or net on the canvas to inspect it.</p>
      )}

      {component && (
        <div className="detail">
          <h3>{component.ref}</h3>
          <dl>
            <dt>Type</dt>
            <dd>{component.type}</dd>
            <dt>Value</dt>
            <dd>{component.value}</dd>
            <dt>Package</dt>
            <dd>{component.package}</dd>
            <dt>MPN</dt>
            <dd>{component.mpn ?? "generic"}</dd>
          </dl>
          <h4>Pins</h4>
          <ul className="pin-list">
            {component.pins.map((p) => (
              <li key={`${component.ref}.${p.name}`}>
                <b>{p.number}</b> {p.name} → <em>{p.net}</em>
              </li>
            ))}
          </ul>
          {Object.keys(component.properties).length > 0 && (
            <>
              <h4>Properties</h4>
              <ul className="pin-list">
                {Object.entries(component.properties).map(([k, v]) => (
                  <li key={k}>
                    <b>{k}</b>: {String(v)}
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      )}

      {net && (
        <div className="detail">
          <h3>{net.name}</h3>
          <dl>
            <dt>Class</dt>
            <dd>{net.class}</dd>
            <dt>Connections</dt>
            <dd>{net.pins.length}</dd>
          </dl>
          <h4>Pins</h4>
          <ul className="pin-list">
            {net.pins.map((rp) => (
              <li key={rp}>{rp}</li>
            ))}
          </ul>
        </div>
      )}

      <div className={`validation-summary ${errorCount ? "has-errors" : ""}`}>
        {violations.length === 0
          ? "✓ Netlist passes contract validation."
          : `${errorCount} error${errorCount === 1 ? "" : "s"} / ${violations.length - errorCount} warning(s) — see Violations panel.`}
      </div>
    </aside>
  );
}
