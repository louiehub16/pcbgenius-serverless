import { Violation } from "../contractTypes";

interface ViolationsProps {
  violations: Violation[];
}

/**
 * Stub violations panel. Currently runs the client-side contract validator only.
 * In later waves this same list will be fed by the real backend ERC/DRC endpoints
 * (run_erc / run_drc) which return violations in the frozen contract shape.
 */
export function ViolationsPanel({ violations }: ViolationsProps) {
  if (violations.length === 0) {
    return (
      <aside className="pane violations">
        <h2>Violations</h2>
        <p className="muted">No violations. 🎉</p>
      </aside>
    );
  }

  return (
    <aside className="pane violations">
      <h2>Violations ({violations.length})</h2>
      <ul className="violation-list">
        {violations.map((v, i) => (
          <li key={i} className={`violation ${v.severity}`}>
            <div className="violation-rule">
              <span className="badge">{v.severity}</span> {v.rule}
            </div>
            <div className="violation-source">at {v.source}</div>
            <div className="violation-msg">{v.message}</div>
          </li>
        ))}
      </ul>
    </aside>
  );
}
