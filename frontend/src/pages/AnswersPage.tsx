import { useState, type FormEvent } from "react";
import { api, type StandardAnswer } from "../api";
import { useApp } from "../components/context";
import { useAction, useAsync } from "../components/useAsync";
import { ActionFeedback, Badge, Empty, ErrorBox, Field, Loading, Notice, ReadOnlyNote, Section, fmtDate } from "../components/ui";

const COMMON_KEYS = ["salary_expectation", "start_date", "relocation", "how_heard", "remote_preference"];
const MASK = "••••••";
const CUSTOM = "__custom__";

const KEY_HINTS: Record<string, string> = {
  salary_expectation: "Desired salary / compensation range, e.g. “USD 150,000–170,000 base”.",
  start_date: "Earliest start date or notice period, e.g. “4 weeks after offer”.",
  relocation: "Willingness to relocate, e.g. “Open to relocating within Canada”.",
  how_heard: "How you heard about roles, e.g. “Company careers page”.",
  remote_preference: "Remote / hybrid / on-site preference.",
  work_authorization: "Legal authorization to work. Answer exactly as you would on a form.",
  sponsorship: "Whether you need visa sponsorship now or in the future.",
  salary_history: "Many jurisdictions prohibit asking this; consider leaving it unanswered.",
};

export function AnswersPage() {
  const { canWrite } = useApp();
  const [reveal, setReveal] = useState(false);
  const res = useAsync(() => api.answers(reveal), [reveal]);
  const [editing, setEditing] = useState<{ key: string; answer: string; approved: boolean; sensitive: boolean } | null>(null);
  const del = useAction();
  const quick = useAction();

  const sensitiveKeys = res.data?.sensitive_keys ?? [];
  const answers = res.data?.answers ?? [];

  async function remove(a: StandardAnswer) {
    if (!window.confirm(`Delete the standard answer for “${a.question_key}”?`)) return;
    const r = await del.run(() => api.deleteAnswer(a.question_key).then(() => true), `Deleted ${a.question_key}.`);
    if (r) res.reload();
  }

  async function toggleApproved(a: StandardAnswer) {
    const r = await quick.run(() => api.saveAnswer(a.question_key, a.answer, !a.approved, a.sensitive),
      (x) => `${x.question_key} ${x.approved ? "approved" : "un-approved"}.`);
    if (r) res.reload();
  }

  return (
    <div className="page">
      <h1 className="page-title">Standard answers</h1>
      <Notice tone="warn">
        <strong>Sensitive and legal answers are never inferred.</strong> Work authorization, sponsorship, demographic, disability, veteran,
        criminal-history and similar questions are only answered with what you enter <em>and approve</em> here. Anything
        unanswered is flagged for you to answer yourself on the employer’s site. Attestations (“I certify…”) are always left to you.
      </Notice>
      {!canWrite && <ReadOnlyNote />}

      {canWrite && (
        <AnswerEditor
          key={editing ? `edit-${editing.key}` : "new"}
          initial={editing}
          sensitiveKeys={sensitiveKeys}
          existingKeys={answers.map((a) => a.question_key)}
          onSaved={() => { setEditing(null); res.reload(); }}
          onCancel={editing ? () => setEditing(null) : undefined}
        />
      )}

      <Section
        title="Saved answers"
        actions={
          <>
            {canWrite && (
              <label className="checkbox inline">
                <input type="checkbox" checked={reveal} onChange={(e) => setReveal(e.target.checked)} />
                <span>Reveal sensitive values</span>
              </label>
            )}
            <button className="btn btn-small" onClick={res.reload} disabled={res.loading}>Refresh</button>
          </>
        }
      >
        <ErrorBox error={res.error} />
        <ActionFeedback action={del} />
        <ActionFeedback action={quick} />
        {res.loading && !res.data ? <Loading /> : answers.length === 0 ? (
          <Empty>No standard answers yet.</Empty>
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>Question key</th><th>Answer</th><th>Status</th><th>Updated</th>{canWrite && <th />}</tr></thead>
              <tbody>
                {answers.map((a) => {
                  const masked = a.sensitive && a.answer === MASK;
                  return (
                    <tr key={a.id}>
                      <td><code>{a.question_key}</code>{a.sensitive && <div><Badge tone="bad">sensitive</Badge></div>}</td>
                      <td className="pre-wrap">{masked ? <span className="muted">{MASK} (hidden)</span> : a.answer}</td>
                      <td>{a.approved ? <Badge tone="ok">approved — used</Badge> : <Badge tone="warn">not approved — unused</Badge>}</td>
                      <td className="nowrap">{fmtDate(a.updated_at)}</td>
                      {canWrite && (
                        <td className="right nowrap">
                          {!masked && (
                            <button className="btn btn-small" onClick={() => toggleApproved(a)} disabled={quick.busy}>{a.approved ? "Un-approve" : "Approve"}</button>
                          )}{" "}
                          <button className="btn btn-small" onClick={() => setEditing({ key: a.question_key, answer: masked ? "" : a.answer, approved: a.approved, sensitive: a.sensitive })}>Edit</button>{" "}
                          <button className="btn btn-small btn-danger-outline" onClick={() => remove(a)} disabled={del.busy}>Delete</button>
                        </td>
                      )}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        {answers.some((a) => a.sensitive && a.answer === MASK) && canWrite && (
          <p className="tiny muted">Sensitive values are hidden. Tick “Reveal sensitive values” to see or quick-approve them (the reveal is admin-only).</p>
        )}
      </Section>
    </div>
  );
}

function AnswerEditor({ initial, sensitiveKeys, existingKeys, onSaved, onCancel }: {
  initial: { key: string; answer: string; approved: boolean; sensitive: boolean } | null;
  sensitiveKeys: string[];
  existingKeys: string[];
  onSaved: () => void;
  onCancel?: () => void;
}) {
  const known = new Set([...COMMON_KEYS, ...sensitiveKeys]);
  const customExisting = existingKeys.filter((k) => !known.has(k));
  const startKey = initial?.key ?? "salary_expectation";
  const [pick, setPick] = useState(known.has(startKey) || customExisting.includes(startKey) ? startKey : CUSTOM);
  const [custom, setCustom] = useState(known.has(startKey) || customExisting.includes(startKey) ? "" : startKey);
  const [answer, setAnswer] = useState(initial?.answer ?? "");
  const [approved, setApproved] = useState(initial?.approved ?? false);
  const [markSensitive, setMarkSensitive] = useState(initial?.sensitive ?? false);
  const action = useAction();

  const key = pick === CUSTOM ? custom.trim().toLowerCase().replace(/[^a-z0-9_]+/g, "_") : pick;
  const forcedSensitive = sensitiveKeys.includes(key);
  const validKey = /^[a-z0-9_]{1,80}$/.test(key) && key.replace(/_/g, "").length > 0;

  async function save(e: FormEvent) {
    e.preventDefault();
    if (!validKey) return;
    const r = await action.run(() => api.saveAnswer(key, answer, approved, forcedSensitive || markSensitive),
      (x) => `Saved ${x.question_key}${x.approved ? " (approved)" : " (not approved — it will not be used until approved)"}.`);
    if (r) {
      setAnswer("");
      setApproved(false);
      onSaved();
    }
  }

  return (
    <Section title={initial ? `Edit answer: ${initial.key}` : "Add or update an answer"}>
      <form onSubmit={save} className="stack">
        <div className="grid-form">
          <Field label="Question">
            <select value={pick} onChange={(e) => setPick(e.target.value)} disabled={Boolean(initial)}>
              <optgroup label="Common">
                {COMMON_KEYS.map((k) => <option key={k} value={k}>{k}</option>)}
              </optgroup>
              <optgroup label="Sensitive / legal (never inferred)">
                {sensitiveKeys.map((k) => <option key={k} value={k}>{k}</option>)}
              </optgroup>
              {customExisting.length > 0 && (
                <optgroup label="Your custom keys">
                  {customExisting.map((k) => <option key={k} value={k}>{k}</option>)}
                </optgroup>
              )}
              <option value={CUSTOM}>Custom key…</option>
            </select>
          </Field>
          {pick === CUSTOM && (
            <Field label="Custom key" hint={key ? <>Saved as <code>{key}</code></> : "letters, digits and underscores"}>
              <input value={custom} onChange={(e) => setCustom(e.target.value)} maxLength={80} placeholder="e.g. notice_period" disabled={Boolean(initial)} />
            </Field>
          )}
        </div>
        {KEY_HINTS[key] && <p className="small muted">{KEY_HINTS[key]}</p>}
        {forcedSensitive && <Notice tone="warn">“{key}” is a sensitive key. It is stored encrypted, masked in the UI, and only used if you approve it.</Notice>}
        <Field label="Your answer" wide>
          <textarea rows={3} value={answer} onChange={(e) => setAnswer(e.target.value)} required maxLength={4000}
            placeholder={initial && initial.sensitive && !initial.answer ? "Hidden — enter the full new value" : ""} />
        </Field>
        <div className="btn-row">
          <label className="checkbox">
            <input type="checkbox" checked={approved} onChange={(e) => setApproved(e.target.checked)} />
            <span>Approved — use this answer on applications</span>
          </label>
          {!forcedSensitive && (
            <label className="checkbox">
              <input type="checkbox" checked={markSensitive} onChange={(e) => setMarkSensitive(e.target.checked)} disabled={Boolean(initial?.sensitive)} />
              <span>Treat as sensitive (mask it)</span>
            </label>
          )}
        </div>
        <ActionFeedback action={action} />
        <div className="form-actions">
          <button className="btn btn-primary" type="submit" disabled={action.busy || !validKey || !answer.trim()}>Save answer</button>
          {onCancel && <button className="btn" type="button" onClick={onCancel}>Cancel</button>}
        </div>
      </form>
    </Section>
  );
}
