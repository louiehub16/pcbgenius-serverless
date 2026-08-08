import { useCallback, useState } from "react";
import { Netlist } from "../contractTypes";

/**
 * Diff shapes produced by the backend iteration engine (pcbgenius-iterate/diff_render.py).
 * `render_diff` returns exactly: { added: [], removed: [], modified: [{kind,ref,field,old,new}] }.
 */
export interface DiffEntry {
  kind: "component" | "net";
  ref: string;
  type?: string;
  value?: string;
  package?: string;
  class?: string;
}

export interface ModifiedEntry {
  kind: "component" | "net";
  ref: string;
  field: string;
  old: unknown;
  new: unknown;
}

export interface NetlistDiff {
  added: DiffEntry[];
  removed: DiffEntry[];
  modified: ModifiedEntry[];
}

export interface IterateResult {
  netlist: Netlist;
  diff: NetlistDiff;
  attempts?: number;
  error?: string;
}

interface IteratePanelProps {
  netlist: Netlist;
  /** Async callback wired to the backend engine (engine.py iterate_netlist, Tauri command or HTTP). */
  onIterate?: (netlist: Netlist, request: string) => Promise<IterateResult>;
  onApply?: (netlist: Netlist) => void;
}

const fmt = (v: unknown): string => {
  if (v === null || v === undefined) return "—";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
};

function DiffList({ diff }: { diff: NetlistDiff }) {
  return (
    <div className="diff-summary">
      {diff.added.length > 0 && (
        <section>
          <h4 className="diff-head add">+ Added ({diff.added.length})</h4>
          <ul className="diff-list">
            {diff.added.map((e) => (
              <li key={`a-${e.kind}-${e.ref}`} className="diff-item add">
                <span className="badge">{e.kind}</span> <strong>{e.ref}</strong>
                {e.type && <span className="muted"> · {e.type}</span>}
                {e.value && <span className="muted"> · {e.value}</span>}
                {e.package && <span className="muted"> · {e.package}</span>}
              </li>
            ))}
          </ul>
        </section>
      )}

      {diff.removed.length > 0 && (
        <section>
          <h4 className="diff-head remove">− Removed ({diff.removed.length})</h4>
          <ul className="diff-list">
            {diff.removed.map((e) => (
              <li key={`r-${e.kind}-${e.ref}`} className="diff-item remove">
                <span className="badge">{e.kind}</span> <strong>{e.ref}</strong>
                {e.value && <span className="muted"> · {e.value}</span>}
              </li>
            ))}
          </ul>
        </section>
      )}

      {diff.modified.length > 0 && (
        <section>
          <h4 className="diff-head modify">~ Modified ({diff.modified.length})</h4>
          <ul className="diff-list">
            {diff.modified.map((m, i) => (
              <li key={`m-${i}`} className="diff-item modify">
                <span className="badge">{m.kind}</span>{" "}
                <strong>{m.ref}</strong>{" "}
                <span className="muted">.{m.field}</span>
                <div className="diff-oldnew">
                  <span className="diff-old">{fmt(m.old)}</span>
                  <span className="diff-arrow">→</span>
                  <span className="diff-new">{fmt(m.new)}</span>
                </div>
              </li>
            ))}
          </ul>
        </section>
      )}

      {diff.added.length === 0 && diff.removed.length === 0 && diff.modified.length === 0 && (
        <p className="muted">No changes detected.</p>
      )}
    </div>
  );
}

/**
 * NL iteration panel: type a change request in plain English, let the backend
 * edit the netlist via an LLM, and review a colored add/remove/modify summary
 * before applying.
 */
export function IteratePanel({ netlist, onIterate, onApply }: IteratePanelProps) {
  const [request, setRequest] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<IterateResult | null>(null);

  const run = useCallback(async () => {
    const req = request.trim();
    if (!req) return;
    if (!onIterate) {
      setError("Iteration backend is not wired up yet.");
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const res = await onIterate(netlist, req);
      if (res.error) {
        setError(res.error);
        setResult(null);
      } else {
        setResult(res);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setResult(null);
    } finally {
      setLoading(false);
    }
  }, [netlist, request, onIterate]);

  return (
    <aside className="pane iterate">
      <h2>Edit with AI</h2>
      <p className="muted">
        Describe the change in plain English — e.g. “bump R1 to 10kΩ” or
        “add a 10µF decoupling cap on VCC”.
      </p>
      <textarea
        className="iterate-input"
        rows={3}
        placeholder="e.g. Change R1 from 330Ω to 1kΩ…"
        value={request}
        onChange={(e) => setRequest(e.target.value)}
        disabled={loading}
      />
      <button className="btn" onClick={run} disabled={loading || !request.trim()}>
        {loading ? "Editing…" : "Edit with AI"}
      </button>

      {error && <p className="violation error">⚠ {error}</p>}

      {result && (
        <div className="iterate-result">
          <div className="result-head">
            <h3>Proposed changes</h3>
            {result.attempts !== undefined && (
              <span className="muted">({result.attempts} attempt{result.attempts === 1 ? "" : "s"})</span>
            )}
          </div>
          <DiffList diff={result.diff} />
          {onApply && (
            <button className="btn" onClick={() => onApply(result.netlist)}>
              Apply changes
            </button>
          )}
        </div>
      )}
    </aside>
  );
}