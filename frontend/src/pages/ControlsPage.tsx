import { useEffect, useState, type FormEvent } from "react";
import { api, type AuditVerify, type DeleteResult, type Retention } from "../api";
import { useApp } from "../components/context";
import { useAction, useAsync } from "../components/useAsync";
import { ActionFeedback, Badge, Empty, ErrorBox, Field, Loading, Notice, ReadOnlyNote, Section, fmtDate } from "../components/ui";

const CONFIRM_PHRASE = "DELETE MY DATA";

export function ControlsPage() {
  const { canWrite } = useApp();
  return (
    <div className="page">
      <h1 className="page-title">Controls &amp; privacy</h1>
      {!canWrite && <ReadOnlyNote />}
      <PauseSection />
      <LLMSection />
      <RetentionSection />
      {canWrite && <PrivacySection />}
      <AuditSection />
    </div>
  );
}

function PauseSection() {
  const { canWrite, controls, refreshControls } = useApp();
  const [reason, setReason] = useState("");
  const [warning, setWarning] = useState<string | null>(null);
  const act = useAction();

  async function pause() {
    const r = await act.run(() => api.pause(reason), "Paused. Polling and submission are halted.");
    if (r) { setReason(""); setWarning(null); refreshControls(); }
  }
  async function resume() {
    const r = await act.run(() => api.resume(reason), (x) => (x.paused ? "Database flag cleared, but the system is still paused." : "Resumed."));
    if (r) { setReason(""); setWarning(r.warning ?? null); refreshControls(); }
  }

  if (!controls) return <Section title="Global pause"><Loading /></Section>;
  return (
    <Section title="Global pause / kill switch">
      <div className={controls.paused ? "pause-state paused" : "pause-state running"}>
        <div className="pause-big">{controls.paused ? "PAUSED" : "RUNNING"}</div>
        <div className="small">
          {controls.paused ? "Polling and every submission path are halted." : "Polling and matching run on schedule. Submission still requires every policy check to pass."}
        </div>
        <div className="pause-signals small">
          <span>Database flag: <Badge tone={controls.db_flag ? "bad" : "ok"}>{controls.db_flag ? "paused" : "clear"}</Badge></span>
          <span>Kill-switch file: <Badge tone={controls.kill_switch_file ? "bad" : "ok"}>{controls.kill_switch_file ? "present" : "absent"}</Badge></span>
          {controls.reason && <span>Reason: {controls.reason}</span>}
        </div>
      </div>
      {controls.kill_switch_file && (
        <Notice tone="warn">
          The kill-switch file is present on the host. It pauses everything even if the database flag is cleared, and it cannot be
          removed from this UI. Delete the file on the host (e.g. <code>data/KILL_SWITCH</code>) to fully resume.
        </Notice>
      )}
      {warning && <Notice tone="warn">{warning}</Notice>}
      {canWrite && (
        <div className="stack">
          <input value={reason} onChange={(e) => setReason(e.target.value)} maxLength={500} placeholder="Reason (optional, recorded in the audit log)" />
          <div className="btn-row">
            <button className="btn btn-danger btn-big" onClick={pause} disabled={act.busy || controls.db_flag}>Pause everything</button>
            <button className="btn btn-ok btn-big" onClick={resume} disabled={act.busy || !controls.db_flag}>Resume</button>
          </div>
        </div>
      )}
      <ActionFeedback action={act} />
    </Section>
  );
}

const RETENTION_FIELDS: { key: keyof Retention; label: string; min: number; hint: string }[] = [
  { key: "expired_job_days", label: "Expired job listings (days)", min: 1, hint: "Listings you applied to are kept." },
  { key: "rejected_application_days", label: "Rejected / expired / duplicate applications (days)", min: 1, hint: "" },
  { key: "connector_run_days", label: "Connector run history (days)", min: 1, hint: "" },
  { key: "audit_days", label: "Audit log (days)", min: 30, hint: "Minimum 30." },
];

function RetentionSection() {
  const { canWrite, controls, refreshControls } = useApp();
  const [vals, setVals] = useState<Record<string, string>>({});
  const act = useAction();

  // Controls are refreshed periodically; only re-sync the form when the saved values actually change.
  const savedKey = controls?.retention ? JSON.stringify(controls.retention) : "";
  useEffect(() => {
    if (controls?.retention) setVals(Object.fromEntries(RETENTION_FIELDS.map((f) => [f.key, String(controls.retention[f.key])])));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [savedKey]);

  async function save(e: FormEvent) {
    e.preventDefault();
    const body: Partial<Retention> = {};
    for (const f of RETENTION_FIELDS) {
      const n = Number(vals[f.key]);
      if (vals[f.key] !== "" && Number.isFinite(n)) body[f.key] = Math.floor(n);
    }
    const r = await act.run(() => api.setRetention(body), "Retention saved. It is applied by the scheduled retention task.");
    if (r) refreshControls();
  }

  if (!controls) return null;
  return (
    <Section title="Data retention">
      <form onSubmit={save}>
        <fieldset disabled={!canWrite} className="grid-form">
          {RETENTION_FIELDS.map((f) => (
            <Field key={f.key} label={f.label} hint={f.hint || undefined}>
              <input type="number" min={f.min} required value={vals[f.key] ?? ""} onChange={(e) => setVals((v) => ({ ...v, [f.key]: e.target.value }))} />
            </Field>
          ))}
        </fieldset>
        <ActionFeedback action={act} />
        {canWrite && <div className="form-actions"><button className="btn btn-primary" type="submit" disabled={act.busy}>Save retention</button></div>}
      </form>
    </Section>
  );
}

function PrivacySection() {
  const exp = useAction();
  const del = useAction();
  const [phrase, setPhrase] = useState("");
  const [result, setResult] = useState<DeleteResult | null>(null);

  async function doDelete(e: FormEvent) {
    e.preventDefault();
    if (phrase !== CONFIRM_PHRASE) return;
    if (!window.confirm("This permanently deletes your profile, documents, facts, answers, preferences and applications. It cannot be undone. Continue?")) return;
    const r = await del.run(() => api.deleteAll(phrase), "All personal data deleted.");
    if (r) { setResult(r); setPhrase(""); }
  }

  return (
    <Section title="Your data">
      <div className="two-col">
        <div className="stack">
          <h3>Export</h3>
          <p className="small muted">Download everything stored about you (profile, documents, facts, answers, applications, audit) as a ZIP.</p>
          <div><button className="btn" onClick={() => exp.run(() => api.exportData(), "Export downloaded.")} disabled={exp.busy}>{exp.busy ? "Preparing…" : "Export my data (.zip)"}</button></div>
          <ActionFeedback action={exp} />
        </div>
        <form className="stack danger-zone" onSubmit={doDelete}>
          <h3>Delete everything</h3>
          <p className="small">
            Permanently deletes your profile and everything derived from it, including encrypted files. Audit events are kept (they
            hold no personal values); public job listings are kept until retention removes them. <strong>This cannot be undone.</strong>
          </p>
          <Field label={`Type ${CONFIRM_PHRASE} to confirm`}>
            <input value={phrase} onChange={(e) => setPhrase(e.target.value)} autoComplete="off" spellCheck={false} placeholder={CONFIRM_PHRASE} />
          </Field>
          <div><button className="btn btn-danger" type="submit" disabled={phrase !== CONFIRM_PHRASE || del.busy}>Delete all my data</button></div>
          <ActionFeedback action={del} />
          {result && <p className="small">Deleted {result.documents} document(s), {result.facts} fact(s), {result.applications} application(s).</p>}
        </form>
      </div>
    </Section>
  );
}

function AuditSection() {
  const audit = useAsync(() => api.audit(200), []);
  const [verify, setVerify] = useState<AuditVerify | null>(null);
  const [filter, setFilter] = useState("");
  const act = useAction();
  const rows = (audit.data ?? []).filter((e) => {
    if (!filter.trim()) return true;
    const f = filter.toLowerCase();
    return e.action.toLowerCase().includes(f) || e.actor.toLowerCase().includes(f) || (e.entity_type ?? "").toLowerCase().includes(f);
  });

  return (
    <Section
      title="Audit log"
      actions={
        <>
          <button className="btn btn-small" disabled={act.busy} onClick={async () => { const r = await act.run(() => api.verifyAudit()); if (r) setVerify(r); }}>Verify chain</button>
          <button className="btn btn-small" onClick={audit.reload} disabled={audit.loading}>Refresh</button>
        </>
      }
    >
      <p className="small muted">Append-only, hash-chained. “Verify chain” recomputes every hash to detect tampering.</p>
      {verify && (verify.ok
        ? <Notice tone="ok">✓ Audit chain verified: no tampering detected.</Notice>
        : <Notice tone="bad">✗ Audit chain broken at event #{verify.first_bad_id ?? "?"}. Records from that point may have been altered.</Notice>)}
      <ActionFeedback action={act} />
      <ErrorBox error={audit.error} />
      <input className="filter-input" value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="Filter by action, actor or entity…" />
      {audit.loading && !audit.data ? <Loading /> : rows.length === 0 ? <Empty>No events.</Empty> : (
        <div className="table-wrap audit-wrap">
          <table className="table compact">
            <thead><tr><th>#</th><th>Time</th><th>Actor</th><th>Action</th><th>Entity</th><th>Details</th><th>Hash</th></tr></thead>
            <tbody>
              {rows.map((e) => (
                <tr key={e.id}>
                  <td>{e.id}</td>
                  <td className="nowrap">{fmtDate(e.ts)}</td>
                  <td className="nowrap">{e.actor}</td>
                  <td><code>{e.action}</code></td>
                  <td className="nowrap">{e.entity_type ? `${e.entity_type}${e.entity_id ? ` #${e.entity_id}` : ""}` : "—"}</td>
                  <td className="tiny mono details-cell">{e.details && Object.keys(e.details).length ? JSON.stringify(e.details) : ""}</td>
                  <td className="tiny mono muted">{e.hash}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {audit.data && <p className="tiny muted">Showing the latest {audit.data.length} event(s).</p>}
    </Section>
  );
}


/** Local LLM (Ollama) for cover-letter drafting. Only models installed on this machine are offered. */
function LLMSection() {
  const { canWrite } = useApp();
  const st = useAsync(() => api.llm(), []);
  const act = useAction();
  const [model, setModel] = useState<string>("");
  const s = st.data;
  useEffect(() => {
    if (s) setModel(s.model);
  }, [s?.model]); // eslint-disable-line react-hooks/exhaustive-deps

  async function save(enabled: boolean) {
    const r = await act.run(() => api.setLlm(enabled, model), enabled ? "Local model enabled for cover letters." : "Local model disabled; template letters only.");
    if (r) st.reload();
  }

  return (
    <Section title="Cover letters: local AI model">
      {st.loading && <Loading />}
      {st.error && <ErrorBox error={st.error} />}
      {s && (
        <>
          <p className="tiny muted">
            When enabled, the letter body is drafted by an Ollama model on this machine from your approved facts only. The model
            never receives your contact details or the job description. Every sentence is checked, and any word or number not
            backed by a cited fact is rejected. If too little survives, the template letter is used. Cloud-hosted models are refused.
          </p>
          <div className="row-wrap">
            <Badge tone={s.enabled ? "ok" : "muted"}>{s.enabled ? "enabled" : "disabled"}</Badge>{" "}
            <Badge tone={s.reachable ? "ok" : "bad"}>{s.reachable ? "Ollama reachable" : "Ollama not reachable"}</Badge>{" "}
            <Badge tone={s.endpoint_is_local ? "ok" : "bad"}>{s.endpoint_is_local ? "local endpoint" : "non-local endpoint"}</Badge>{" "}
            <span className="tiny muted">{s.base_url}</span>
          </div>
          {s.error && <Notice tone="warn">{s.error}</Notice>}
          <fieldset disabled={!canWrite || !s.reachable} className="grid-form">
            <Field label="Model" hint="Installed local models only">
              <select value={model} onChange={(e) => setModel(e.target.value)}>
                {!s.models.some((m) => m.name === model) && <option value={model}>{model} (not installed)</option>}
                {s.models.map((m) => (
                  <option key={m.name} value={m.name}>
                    {m.name} · {m.parameters ?? "?"} · {m.size_gb} GB
                  </option>
                ))}
              </select>
            </Field>
          </fieldset>
          <ActionFeedback action={act} />
          {canWrite && (
            <div className="form-actions">
              <button className="btn btn-primary" disabled={act.busy || !s.reachable} onClick={() => save(true)}>
                {s.enabled ? "Save model" : "Enable"}
              </button>{" "}
              {s.enabled && <button className="btn" disabled={act.busy} onClick={() => save(false)}>Disable</button>}
            </div>
          )}
        </>
      )}
    </Section>
  );
}
