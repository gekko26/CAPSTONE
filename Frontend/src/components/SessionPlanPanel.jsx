import { useState, useEffect, useCallback } from "react";
import { RefreshCw, AlertTriangle, ChevronDown, ChevronUp } from "lucide-react";

import { API_BASE as BASE } from "../api";

/**
 * Per-person collection plan, driven entirely by GET /training/session-plan.
 *
 * Shows what is still left to capture for each enrolled person and whether the
 * clear-air baseline has settled. The baseline banner is advisory: it reports, it
 * never blocks. Read-only — this panel issues no writes.
 */
export function SessionPlanPanel({ refreshKey = 0 }) {
  const [plan, setPlan] = useState(null);
  const [error, setError] = useState(null);
  const [open, setOpen] = useState(true);

  const load = useCallback(async () => {
    try {
      const r = await fetch(`${BASE}/training/session-plan`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setPlan(await r.json());
      setError(null);
    } catch {
      setError("session plan unavailable");
    }
  }, []);

  // Refetch only when the page asks (after a capture or a tag). No polling.
  // Refresh on demand (a capture or tag happened) rather than polling.
  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { load(); }, [load, refreshKey]);

  const baseline = plan?.baseline;
  const showBanner = baseline?.status === "unstable";

  const cell = (done, quota) => {
    if (!quota) return null;
    const complete = done >= quota;
    const partial = done > 0;
    const color = complete ? "var(--pass)" : partial ? "var(--near)" : "var(--text-muted)";
    return (
      <span className="font-mono text-[10px]" style={{ color }}>
        {done}/{quota}
      </span>
    );
  };

  return (
    <div
      className="p-2 rounded-lg border text-xs space-y-1.5"
      style={{ background: "var(--bg-active)", borderColor: "var(--border-subtle)" }}
    >
      <div className="flex items-center gap-1.5 w-full">
        <button
          onClick={() => setOpen(o => !o)}
          className="flex items-center gap-1.5 flex-1 text-left"
        >
          <span className="text-[11px] uppercase font-medium" style={{ color: "var(--text-muted)" }}>
            Session plan
          </span>
        </button>
        <span className="ml-auto flex items-center gap-1" style={{ color: "var(--text-muted)" }}>
          <button
            onClick={() => load()}
            title="Refresh session plan"
            aria-label="Refresh session plan"
            className="data-interactive"
          >
            <RefreshCw size={11} />
          </button>
          <button
            onClick={() => setOpen(o => !o)}
            aria-label={open ? "Collapse session plan" : "Expand session plan"}
            className="data-interactive"
          >
            {open ? <ChevronUp size={11} /> : <ChevronDown size={11} />}
          </button>
        </span>
      </div>

      {showBanner && (
        <div
          className="flex items-start gap-1.5 p-1.5 rounded"
          style={{ background: "var(--near-bg)", color: "var(--near)" }}
        >
          <AlertTriangle size={12} className="shrink-0 mt-px" />
          <span className="text-[10px] leading-snug">{baseline.message}</span>
        </div>
      )}

      {error && (
        <div className="text-[10px]" style={{ color: "var(--text-muted)" }}>{error}</div>
      )}

      {!plan && !error && (
        <div className="font-mono text-[10px]" style={{ color: "var(--text-muted)" }}>—</div>
      )}

      {open && plan && (
        <>
          <table className="w-full text-[10px]">
            <thead>
              <tr>
                <th className="text-left font-normal pb-1" style={{ color: "var(--text-muted)" }}>
                  person
                </th>
                {(plan.event_keys || []).map(k => (
                  <th key={k} className="text-right font-normal pb-1" style={{ color: "var(--text-muted)" }}>
                    {k.replace("_", " ")}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {(plan.people || []).map(p => (
                <tr key={p.subject_id}>
                  <td className="truncate pr-1" style={{ color: "var(--text-primary)" }}>
                    {p.name}
                    {p.next_suggested && (
                      <span
                        className="ml-1 px-1 rounded"
                        style={{ background: "var(--pass-bg)", color: "var(--pass)" }}
                      >
                        next: {p.next_suggested.replace("_", " ")}
                      </span>
                    )}
                  </td>
                  {(plan.event_keys || []).map(k => (
                    <td key={k} className="text-right">
                      {cell(p.counts?.[k] ?? 0, plan.quotas?.[k] ?? 0)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>

          {(plan.people || []).length === 0 && (
            <div className="text-[10px]" style={{ color: "var(--text-muted)" }}>
              No subjects enrolled yet.
            </div>
          )}

          <div className="text-[9px] leading-snug pt-1" style={{ color: "var(--text-muted)" }}>
            done/quota per person
            {" · "}untagged: {plan.overall?.untagged_rows ?? 0}
            {" · "}bands: {" "}
            {Object.entries(plan.overall?.alcohol_bands || {})
              .map(([k, v]) => `${k} ${v}`).join(", ")}
          </div>
        </>
      )}
    </div>
  );
}

export default SessionPlanPanel;