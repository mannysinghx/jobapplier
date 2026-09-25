import { useMemo, useState, type FormEvent } from "react";
import {
  api, CREATABLE_FACT_KINDS, FACT_FIELDS, type Conflict, type CreatableFactKind, type Fact, type FactDecisionResult,
  type FactKind, type FactStatus,
} from "../api";
import { useApp } from "../components/context";
import { useAction, useAsync } from "../components/useAsync";
import { ActionFeedback, Badge, Empty, ErrorBox, FactStatusBadge, Field, Loading, Notice, ReadOnlyNote, Section, fmtDate } from "../components/ui";

const GROUP_ORDER: { kind: FactKind; title: string }[] = [
  { kind: "summary", title: "Summary" },
  { kind: "contact", title: "Contact details" },
  { kind: "role", title: "Roles & achievements" },
  { kind: "education", title: "Education" },
  { kind: "skill", title: "Skills" },
  { kind: "certification", title: "Certifications" },
  { kind: "project", title: "Projects" },
  { kind: "achievement", title: "Other achievements" },
];

const str = (v: unknown): string => (v === null || v === undefined ? "" : String(v));

export function factText(f: Fact): string {
  const d = f.data;
  switch (f.kind) {
    case "role": {
      const period = d.start || d.end ? ` (${str(d.start) || "?"} – ${d.end === "present" ? "Present" : str(d.end) || "?"})` : "";
      return `${str(d.title) || "(no title)"}${d.employer ? ` — ${str(d.employer)}` : ""}${period}${d.location ? ` · ${str(d.location)}` : ""}`;
    }
    case "education":
      return [d.degree, d.institution, d.year].map(str).filter(Boolean).join(" — ") || "(empty)";
    case "skill":
      return str(d.name) || "(empty)";
    case "certification":
      return `${str(d.name) || "(empty)"}${d.authority ? ` — ${str(d.authority)}` : ""}`;
    case "contact":
      return `${str(d.field)}: ${str(d.value)}`;
    default:
      return str(d.text) || "(empty)";
  }
}

export function FactsPage() {
  const { canWrite } = useApp();
  const facts = useAsync(() => api.facts(), []);
  const conflicts = useAsync(() => api.conflicts(), []);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [statusFilter, setStatusFilter] = useState<"ALL" | FactStatus>("ALL");
  const [lastDecision, setLastDecision] = useState<FactDecisionResult | null>(null);
  const decide = useAction();

  const byId = useMemo(() => new Map((facts.data ?? []).map((f) => [f.id, f])), [facts.data]);
  const visible = useMemo(
    () => (facts.data ?? []).filter((f) => statusFilter === "ALL" || f.status === statusFilter),
    [facts.data, statusFilter],
  );
  const visibleRoleIds = useMemo(() => new Set(visible.filter((f) => f.kind === "role").map((f) => f.id)), [visible]);
  const counts = useMemo(() => {
    const c = { PENDING: 0, APPROVED: 0, REJECTED: 0 };
    for (const f of facts.data ?? []) c[f.status] += 1;
    return c;
  }, [facts.data]);

  function reloadAll() {
    facts.reload();
    conflicts.reload();
  }

  function toggle(id: number, on: boolean) {
    setSelected((s) => {
      const n = new Set(s);
      if (on) n.add(id);
      else n.delete(id);
      return n;
    });
  }

  async function applyDecision(decision: FactStatus) {
    const ids = [...selected];
    if (!ids.length) return;
    const r = await decide.run(() => api.decideFacts(ids, decision),
      (x) => `${x.updated.length} fact(s) set to ${decision}${x.skipped.length ? `, ${x.skipped.length} skipped (see below)` : ""}.`);
    if (r) {
      setLastDecision(r);
      setSelected(new Set(r.skipped.map((s) => s.id)));
      facts.reload();
    }
  }

  const openConflicts = (conflicts.data ?? []).filter((c) => c.status === "OPEN");
  const resolvedConflicts = (conflicts.data ?? []).filter((c) => c.status !== "OPEN");

  return (
    <div className="page">
      <h1 className="page-title">Facts review</h1>
      <Notice tone="info">
        Facts are extracted from your resume, your LinkedIn export, or entered by you. <strong>Only APPROVED facts are ever used</strong> for
        matching, resumes, cover letters and answers. Pending and rejected facts are ignored. Every fact keeps its provenance
        (source document and the snippet it came from).
      </Notice>
      {!canWrite && <ReadOnlyNote />}

      <Section
        title={<>Conflicts {openConflicts.length > 0 && <Badge tone="bad">{openConflicts.length} open</Badge>}</>}
        actions={<button className="btn btn-small" onClick={reloadAll}>Refresh</button>}
      >
        <ErrorBox error={conflicts.error} />
        {conflicts.loading && !conflicts.data ? <Loading /> : openConflicts.length === 0 ? (
          <Empty>No open conflicts. Facts involved in a conflict cannot be approved until it is resolved.</Empty>
        ) : (
          <div className="stack">
            {openConflicts.map((c) => <ConflictCard key={c.id} c={c} byId={byId} onResolved={reloadAll} />)}
          </div>
        )}
        {resolvedConflicts.length > 0 && (
          <details className="subsection">
            <summary>{resolvedConflicts.length} resolved conflict(s)</summary>
            <ul className="plain-list small">
              {resolvedConflicts.map((c) => (
                <li key={c.id}>#{c.id} {c.field}: {c.description} — <span className="muted">{c.resolution}</span></li>
              ))}
            </ul>
          </details>
        )}
      </Section>

      {canWrite && <AddFactForm roles={(facts.data ?? []).filter((f) => f.kind === "role" && f.status !== "REJECTED")} onAdded={facts.reload} />}

      <Section title="Facts" actions={<button className="btn btn-small" onClick={facts.reload} disabled={facts.loading}>Refresh</button>}>
        <div className="toolbar">
          <div className="seg">
            {(["ALL", "PENDING", "APPROVED", "REJECTED"] as const).map((s) => (
              <button key={s} className={statusFilter === s ? "seg-btn active" : "seg-btn"} onClick={() => setStatusFilter(s)}>
                {s === "ALL" ? `All (${facts.data?.length ?? 0})` : `${s.toLowerCase()} (${counts[s]})`}
              </button>
            ))}
          </div>
          {canWrite && (
            <div className="toolbar-actions">
              <button className="btn btn-small" onClick={() => setSelected(new Set(visible.map((f) => f.id)))} disabled={!visible.length}>Select all shown</button>
              <button className="btn btn-small" onClick={() => setSelected(new Set())} disabled={!selected.size}>Clear</button>
              <button className="btn btn-small btn-ok" onClick={() => applyDecision("APPROVED")} disabled={!selected.size || decide.busy}>Approve selected ({selected.size})</button>
              <button className="btn btn-small btn-danger" onClick={() => applyDecision("REJECTED")} disabled={!selected.size || decide.busy}>Reject selected</button>
              <button className="btn btn-small" onClick={() => applyDecision("PENDING")} disabled={!selected.size || decide.busy} title="Move back to pending (makes them editable again)">Reset to pending</button>
            </div>
          )}
        </div>
        <ActionFeedback action={decide} />
        {lastDecision && lastDecision.skipped.length > 0 && (
          <div className="alert alert-warn">
            <strong>Skipped (still selected):</strong>
            <ul className="plain-list">
              {lastDecision.skipped.map((s) => <li key={s.id}>fact #{s.id}: {s.reason}</li>)}
            </ul>
          </div>
        )}
        <ErrorBox error={facts.error} />
        {facts.loading && !facts.data ? <Loading /> : visible.length === 0 ? (
          <Empty>{facts.data?.length ? "No facts match this filter." : "No facts yet. Upload a resume or LinkedIn export in Setup, or add facts manually above."}</Empty>
        ) : (
          GROUP_ORDER.map((g) => {
            // Achievements nest under their role when that role is shown; otherwise they are listed on their own.
            const list = visible.filter((f) => f.kind === g.kind && !(g.kind === "achievement" && f.parent_id !== null && visibleRoleIds.has(f.parent_id)));
            if (!list.length) return null;
            return (
              <div className="fact-group" key={g.kind}>
                <h3>{g.title} <span className="muted small">({list.length})</span></h3>
                <ul className="fact-list">
                  {list.map((f) => (
                    <li key={f.id}>
                      <FactRow f={f} selected={selected.has(f.id)} onToggle={toggle} onChanged={facts.reload} />
                      {f.kind === "role" && (() => {
                        const kids = visible.filter((k) => k.parent_id === f.id);
                        return kids.length ? (
                          <ul className="fact-list nested">
                            {kids.map((k) => (
                              <li key={k.id}><FactRow f={k} selected={selected.has(k.id)} onToggle={toggle} onChanged={facts.reload} /></li>
                            ))}
                          </ul>
                        ) : null;
                      })()}
                    </li>
                  ))}
                </ul>
              </div>
            );
          })
        )}
      </Section>
    </div>
  );
}

function FactRow({ f, selected, onToggle, onChanged }: { f: Fact; selected: boolean; onToggle: (id: number, on: boolean) => void; onChanged: () => void }) {
  const { canWrite } = useApp();
  const [editing, setEditing] = useState(false);
  return (
    <div className={`fact-row status-${f.status.toLowerCase()}`}>
      <div className="fact-main">
        {canWrite && (
          <input type="checkbox" checked={selected} onChange={(e) => onToggle(f.id, e.target.checked)} aria-label={`select fact ${f.id}`} />
        )}
        <div className="fact-body">
          <div className="fact-line">
            <span className="mono tiny muted">#{f.id}</span>
            <FactStatusBadge status={f.status} />
            <span className="fact-text">{factText(f)}</span>
          </div>
          <div className="fact-meta">
            <Badge tone="muted">{f.kind}</Badge>
            <Badge tone="muted" title="Where this fact came from">origin: {f.origin.replace(/_/g, " ")}</Badge>
            {f.document_id ? <span className="tiny muted">doc #{f.document_id}{f.char_start !== null ? ` @${f.char_start}–${f.char_end ?? "?"}` : ""}</span> : null}
            {f.has_open_conflict && <Badge tone="bad">open conflict</Badge>}
            {f.data.edited_by_user ? <Badge tone="info">edited by you</Badge> : null}
            {f.approved_at && <span className="tiny muted">approved {fmtDate(f.approved_at)}</span>}
            {canWrite && f.status === "PENDING" && !editing && (
              <button className="btn btn-link btn-small" onClick={() => setEditing(true)}>Edit</button>
            )}
          </div>
          {f.snippet && <blockquote className="snippet" title="Provenance: source text">{f.snippet}</blockquote>}
          {editing && <FactEditor f={f} onDone={(changed) => { setEditing(false); if (changed) onChanged(); }} />}
        </div>
      </div>
    </div>
  );
}

function FactEditor({ f, onDone }: { f: Fact; onDone: (changed: boolean) => void }) {
  const fields = FACT_FIELDS[f.kind] ?? [];
  const [vals, setVals] = useState<Record<string, string>>(() => Object.fromEntries(fields.map((k) => [k, str(f.data[k])])));
  const action = useAction();
  async function save(e: FormEvent) {
    e.preventDefault();
    const data = Object.fromEntries(fields.map((k) => [k, vals[k].trim() ? vals[k] : null]));
    const r = await action.run(() => api.editFact(f.id, data));
    if (r) onDone(true);
  }
  return (
    <form className="fact-editor" onSubmit={save}>
      <div className="grid-form">
        {fields.map((k) => (
          <Field key={k} label={k} wide={k === "text"}>
            {k === "text" ? (
              <textarea rows={3} value={vals[k]} onChange={(e) => setVals((v) => ({ ...v, [k]: e.target.value }))} maxLength={2000} />
            ) : (
              <input value={vals[k]} onChange={(e) => setVals((v) => ({ ...v, [k]: e.target.value }))} maxLength={2000}
                placeholder={k === "end" ? "YYYY-MM or present" : k === "start" ? "YYYY-MM" : undefined} />
            )}
          </Field>
        ))}
      </div>
      <ActionFeedback action={action} />
      <div className="form-actions">
        <button className="btn btn-primary btn-small" type="submit" disabled={action.busy}>Save</button>
        <button className="btn btn-small" type="button" onClick={() => onDone(false)}>Cancel</button>
      </div>
    </form>
  );
}

function AddFactForm({ roles, onAdded }: { roles: Fact[]; onAdded: () => void }) {
  const [kind, setKind] = useState<CreatableFactKind>("skill");
  const [vals, setVals] = useState<Record<string, string>>({});
  const [parent, setParent] = useState<string>("");
  const action = useAction();
  const fields = FACT_FIELDS[kind];

  async function add(e: FormEvent) {
    e.preventDefault();
    const data = Object.fromEntries(fields.map((k) => [k, (vals[k] ?? "").trim() || null]));
    const parentId = kind === "achievement" && parent ? Number(parent) : null;
    const r = await action.run(() => api.addFact(kind, data, parentId), (x) => `Added fact #${x.id} as PENDING. Approve it below to use it.`);
    if (r) {
      setVals({});
      onAdded();
    }
  }

  return (
    <Section title="Add a fact">
      <details>
        <summary>Enter a fact yourself (it is still reviewed and approved like any other)</summary>
        <form onSubmit={add} className="stack subsection">
          <div className="grid-form">
            <Field label="Kind">
              <select value={kind} onChange={(e) => { setKind(e.target.value as CreatableFactKind); setVals({}); }}>
                {CREATABLE_FACT_KINDS.map((k) => <option key={k} value={k}>{k}</option>)}
              </select>
            </Field>
            {kind === "achievement" && (
              <Field label="Belongs to role (optional)">
                <select value={parent} onChange={(e) => setParent(e.target.value)}>
                  <option value="">— standalone —</option>
                  {roles.map((r) => <option key={r.id} value={r.id}>#{r.id} {factText(r)}</option>)}
                </select>
              </Field>
            )}
            {fields.map((k) => (
              <Field key={k} label={k} wide={k === "text"}>
                {k === "text" ? (
                  <textarea rows={3} value={vals[k] ?? ""} onChange={(e) => setVals((v) => ({ ...v, [k]: e.target.value }))} maxLength={2000} required />
                ) : (
                  <input value={vals[k] ?? ""} onChange={(e) => setVals((v) => ({ ...v, [k]: e.target.value }))} maxLength={2000}
                    required={k === "name" || k === "title"}
                    placeholder={k === "end" ? "YYYY-MM or present" : k === "start" ? "YYYY-MM" : undefined} />
                )}
              </Field>
            ))}
          </div>
          <ActionFeedback action={action} />
          <div><button className="btn btn-primary" type="submit" disabled={action.busy}>Add fact</button></div>
        </form>
      </details>
    </Section>
  );
}

function ConflictCard({ c, byId, onResolved }: { c: Conflict; byId: Map<number, Fact>; onResolved: () => void }) {
  const { canWrite } = useApp();
  const [note, setNote] = useState("");
  const action = useAction();
  const a = byId.get(c.fact_a_id);
  const b = c.fact_b_id ? byId.get(c.fact_b_id) : undefined;

  async function resolve(keep: "a" | "b" | "both" | "neither") {
    const r = await action.run(() => api.resolveConflict(c.id, keep, note));
    if (r) onResolved();
  }

  const side = (label: string, id: number | null, f: Fact | undefined) => (
    <div className="conflict-side">
      <div className="small muted">{label}{id ? ` · fact #${id}` : ""}</div>
      {!id ? <div className="muted">(no second fact)</div> : !f ? <div className="muted">fact #{id} not loaded</div> : (
        <>
          <div className="fact-line"><FactStatusBadge status={f.status} /><span className="fact-text">{factText(f)}</span></div>
          <div className="fact-meta"><Badge tone="muted">origin: {f.origin.replace(/_/g, " ")}</Badge>{f.document_id ? <span className="tiny muted">doc #{f.document_id}</span> : null}</div>
          {f.snippet && <blockquote className="snippet">{f.snippet}</blockquote>}
        </>
      )}
    </div>
  );

  return (
    <div className="conflict">
      <div className="conflict-head"><Badge tone="bad">conflict #{c.id}</Badge> <strong>{c.field}</strong> — {c.description}</div>
      <div className="conflict-sides">
        {side("A", c.fact_a_id, a)}
        {side("B", c.fact_b_id, b)}
      </div>
      {canWrite && (
        <div className="conflict-actions">
          <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Note (optional)" maxLength={500} />
          <div className="btn-row">
            <button className="btn btn-small" onClick={() => resolve("a")} disabled={action.busy}>Keep A</button>
            <button className="btn btn-small" onClick={() => resolve("b")} disabled={action.busy || !c.fact_b_id}>Keep B</button>
            <button className="btn btn-small" onClick={() => resolve("both")} disabled={action.busy}>Keep both</button>
            <button className="btn btn-small btn-danger-outline" onClick={() => resolve("neither")} disabled={action.busy}>Keep neither</button>
          </div>
          <p className="tiny muted">“Keep” does not approve a fact; it rejects the other one and closes the conflict. Approve the kept fact afterwards.</p>
        </div>
      )}
      <ActionFeedback action={action} />
    </div>
  );
}
