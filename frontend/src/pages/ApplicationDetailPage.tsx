import { useState, type FormEvent } from "react";
import {
  api, canTransition, type ApplicationDetail, type AutoSubmitPolicy, type MatchData, type Packet, type StandardAnswer,
  type SubmitResult,
} from "../api";
import { useApp } from "../components/context";
import { useAction, useAsync, type ActionState } from "../components/useAsync";
import {
  ActionFeedback, AnswerStatusBadge, Badge, Check, Empty, ErrorBox, Field, Loading, Notice, ProvenanceChips, SafeLink,
  Section, StateBadge, UntrustedText, fmtDate,
} from "../components/ui";
import { scoreTone } from "./ApplicationsPage";

const CHECK_LABELS: Record<string, string> = {
  not_paused: "System not paused (DB flag and kill-switch file)",
  source_registered: "Source is in the registry",
  source_submit_permitted: "Source terms permit automated submission",
  source_review_current: "Source permission review is current",
  adapter_verified: "Verified submission integration exists",
  user_enabled_source: "You enabled this source",
  user_opted_in: "You opted in to auto-submit for this source",
  state_approved: "Application is APPROVED",
  packet_approved: "Latest packet is approved",
  listing_active: "Listing is not expired",
  listing_unique: "Listing is not a duplicate",
  listing_fresh: "Listing was seen recently",
  apply_url_https: "Apply URL is https://",
  not_already_applied: "Not already applied (duplicate guard)",
  no_hard_exclusions: "No hard exclusions",
  score_meets_threshold: "Score meets your minimum",
  answers_supported: "Every answer is supported or approved",
  global_daily_limit: "Under your daily application limit",
  source_daily_limit: "Under the per-source daily limit",
};

const humanize = (k: string) => CHECK_LABELS[k] ?? k.replace(/_/g, " ");

function money(n: number | null, cur: string | null): string {
  if (n === null || n === undefined) return "";
  return `${cur ? `${cur} ` : ""}${Math.round(n).toLocaleString()}`;
}

export function ApplicationDetailPage({ id }: { id: number }) {
  const { canWrite } = useApp();
  const res = useAsync(() => api.application(id), [id]);
  const a = res.data;

  return (
    <div className="page">
      <a href="#jobs" className="back-link">← All jobs</a>
      <ErrorBox error={res.error} />
      {res.loading && !a ? <Loading /> : !a ? null : (
        <>
          <JobHeader a={a} onRefresh={res.reload} loading={res.loading} />
          {canWrite && <ActionsCard a={a} onChanged={res.reload} />}
          <PolicyCard policy={a.auto_submit_policy} />
          {a.match ? <MatchCard m={a.match} /> : <Section title="Match"><Empty>Not scored yet.</Empty></Section>}
          <PacketCard a={a} onChanged={res.reload} />
          <HistoryCard a={a} />
          <Section title="Job description">
            <p className="tiny muted">Untrusted text from the job source. Shown as plain text only; nothing in it can change what this app does.</p>
            <UntrustedText text={a.description} />
            {a.requirements && a.requirements.length > 0 && (
              <>
                <h3>Listed requirements</h3>
                <ul className="plain-list small">
                  {a.requirements.map((r, i) => <li key={i}>{typeof r === "string" ? r : JSON.stringify(r)}</li>)}
                </ul>
              </>
            )}
          </Section>
        </>
      )}
    </div>
  );
}

function JobHeader({ a, onRefresh, loading }: { a: ApplicationDetail; onRefresh: () => void; loading: boolean }) {
  const j = a.job;
  const salary = j.salary_min !== null || j.salary_max !== null
    ? [money(j.salary_min, j.salary_currency), money(j.salary_max, j.salary_currency)].filter(Boolean).join(" – ")
    : null;
  return (
    <section className="card job-header">
      <div className="row-between">
        <div>
          <h1 className="job-title">{j.title}</h1>
          <div className="job-emp">{j.employer}</div>
        </div>
        <div className="badges">
          <Badge tone={scoreTone(a.score)}>score {a.score === null ? "—" : a.score.toFixed(1)}</Badge>
          <StateBadge state={a.state} />
          <button className="btn btn-small" onClick={onRefresh} disabled={loading}>Refresh</button>
        </div>
      </div>
      <div className="job-meta small">
        <span>{j.location || "Location not stated"}</span>
        <span>{j.work_arrangement}</span>
        <span>{j.employment_type.replace(/_/g, " ")}</span>
        {salary && <span>{salary}</span>}
        <span className="muted">{j.source} / {j.board}</span>
        <span className="muted">posted {fmtDate(j.posted_at)}</span>
        <span className="muted">last seen {fmtDate(j.last_seen_at)}</span>
        {j.expired_at && <Badge tone="bad">expired {fmtDate(j.expired_at)}</Badge>}
        {j.duplicate_of_id && <Badge tone="warn">duplicate of job #{j.duplicate_of_id}</Badge>}
      </div>
      <div className="job-links small">
        <span>Apply: <SafeLink href={j.apply_url}>open application page ↗</SafeLink></span>
        {j.canonical_url && j.canonical_url !== j.apply_url && <span>Listing: <SafeLink href={j.canonical_url}>view listing ↗</SafeLink></span>}
      </div>
    </section>
  );
}

function ActionsCard({ a, onChanged }: { a: ApplicationDetail; onChanged: () => void }) {
  const act = useAction();
  const [reason, setReason] = useState("");
  const [submitResult, setSubmitResult] = useState<SubmitResult | null>(null);
  const [confId, setConfId] = useState("");
  const [confUrl, setConfUrl] = useState("");
  const s = a.state;
  const pk = a.packet;
  const canPrepare = ["MATCHED", "PREPARED", "NEEDS_REVIEW"].includes(s);
  const canApprove = ["PREPARED", "NEEDS_REVIEW"].includes(s) && Boolean(pk);
  const openItems = pk ? pk.answers.filter((x) => !["SUPPORTED", "USER_APPROVED", "ON_SITE"].includes(x.status) && !(x.status === "NEEDS_REVIEW" && !x.required && !x.sensitive)) : [];

  async function go<R>(fn: () => Promise<R>, msg: string | ((r: R) => string)) {
    const r = await act.run(fn, msg);
    if (r !== undefined) onChanged();
    return r;
  }

  async function approveOnSite() {
    const ok = window.confirm(
      `Approve this packet and answer the ${openItems.length} open item(s) yourself on the employer’s site?\n\n` +
      "Those items are marked ON_SITE. They still block auto-submit, so this application will go to a handoff for you to complete.",
    );
    if (ok) await go(() => api.approve(a.id, true), (r) => `Approved (open items marked ON_SITE). State: ${r.state}.`);
  }

  async function manual(e: FormEvent) {
    e.preventDefault();
    const url = confUrl.trim();
    if (url && !url.startsWith("https://")) {
      if (!window.confirm("The confirmation URL is not https:// and will be discarded by the server. Continue without it?")) return;
    }
    const r = await go(() => api.manualSubmission(a.id, confId.trim() || null, url || null), (x) => `Submission recorded. State: ${x.state}.`);
    if (r) { setConfId(""); setConfUrl(""); }
  }

  const buttons = [];
  if (canPrepare) {
    buttons.push(
      <button key="prep" className="btn btn-primary" disabled={act.busy}
        onClick={() => go(() => api.prepare(a.id), (r) => `Packet prepared (${r.unsupported_count} open item(s)). State: ${r.state}.`)}>
        {pk ? "Re-prepare packet" : "Prepare packet"}
      </button>,
    );
  }
  if (canApprove) {
    buttons.push(
      <button key="appr" className="btn btn-ok" disabled={act.busy || openItems.length > 0}
        title={openItems.length ? "Resolve open items first, or use the on-site option" : undefined}
        onClick={() => go(() => api.approve(a.id, false), (r) => `Packet approved. State: ${r.state}.`)}>
        Approve
      </button>,
    );
    if (openItems.length > 0) {
      buttons.push(
        <button key="apprsite" className="btn" disabled={act.busy} onClick={approveOnSite}>
          Approve — I’ll answer open items on the employer site
        </button>,
      );
    }
  }
  if (s === "APPROVED") {
    buttons.push(
      <button key="submit" className="btn" disabled={act.busy}
        onClick={async () => {
          const r = await go(() => api.submit(a.id), (x) => (x.submitted ? `Submitted${x.confirmation_id ? ` (confirmation ${x.confirmation_id})` : ""}.` : "Not submitted: the policy gate refused. A handoff was created."));
          if (r) setSubmitResult(r);
        }}>
        Try auto-submit
      </button>,
    );
  }
  if (s === "SUBMITTED") {
    buttons.push(<button key="conf" className="btn btn-ok" disabled={act.busy} onClick={() => go(() => api.confirm(a.id, reason), "Marked CONFIRMED.")}>Mark confirmed</button>);
  }
  if (canTransition(s, "FOLLOW_UP")) {
    buttons.push(<button key="fu" className="btn" disabled={act.busy} onClick={() => go(() => api.followUp(a.id, reason), "Follow-up recorded.")}>Record follow-up</button>);
  }
  if (canTransition(s, "REJECTED_BY_USER")) {
    buttons.push(
      <button key="rej" className="btn btn-danger-outline" disabled={act.busy}
        onClick={() => window.confirm("Reject this job? You can restore it later.") && go(() => api.reject(a.id, reason), "Rejected.")}>
        Reject
      </button>,
    );
  }
  if (s === "REJECTED_BY_USER" || s === "BLOCKED_BY_POLICY") {
    buttons.push(<button key="res" className="btn" disabled={act.busy} onClick={() => go(() => api.restore(a.id, reason), "Restored to DISCOVERED. Re-match to re-score it.")}>Restore</button>);
  }

  return (
    <Section title="Actions">
      {buttons.length === 0 ? <p className="small muted">No actions available in state {s}.</p> : (
        <>
          <div className="btn-row">{buttons}</div>
          {(canTransition(s, "REJECTED_BY_USER") || s === "SUBMITTED" || canTransition(s, "FOLLOW_UP") || s === "REJECTED_BY_USER" || s === "BLOCKED_BY_POLICY") && (
            <input className="reason-input" value={reason} onChange={(e) => setReason(e.target.value)} maxLength={300} placeholder="Reason / note for reject, restore, confirm or follow-up (optional, saved in the audit log)" />
          )}
        </>
      )}
      {canApprove && openItems.length > 0 && (
        <p className="small muted">
          {openItems.length} open item(s) in the packet ({[...new Set(openItems.map((x) => x.key))].join(", ")}). Map them to approved answers below,
          or approve and answer them yourself on the employer’s site.
        </p>
      )}
      {s === "FAILED" && <p className="small muted">A failed submission is never retried automatically. Check the employer site; you can reject this application here.</p>}
      <ActionFeedback action={act} />
      {submitResult && !submitResult.submitted && (
        <div className="alert alert-warn">
          <strong>Auto-submit refused.</strong> Reasons:
          <ul className="plain-list">{(submitResult.reasons ?? []).map((r, i) => <li key={i}>{r}</li>)}</ul>
          Complete it from the <a href="#handoffs">handoff</a>, then record the submission below.
        </div>
      )}
      {s === "APPROVED" && (
        <form onSubmit={manual} className="subsection">
          <h3>Record manual submission</h3>
          <p className="small muted">After you submit on the employer’s site yourself, record it here. This snapshots exactly what the packet contained and closes open handoffs.</p>
          <div className="grid-form">
            <Field label="Confirmation ID (optional)"><input value={confId} onChange={(e) => setConfId(e.target.value)} maxLength={200} /></Field>
            <Field label="Confirmation URL (optional, https:// only)"><input type="url" value={confUrl} onChange={(e) => setConfUrl(e.target.value)} maxLength={600} placeholder="https://…" /></Field>
          </div>
          <button className="btn btn-primary" type="submit" disabled={act.busy}>Record submission</button>
        </form>
      )}
      {pk && (
        <div className="downloads small">
          Downloads: <a href={api.resumeDocxUrl(a.id)} download>resume.docx</a> · <a href={api.coverLetterUrl(a.id)} download>cover-letter.txt</a>
        </div>
      )}
    </Section>
  );
}

function PolicyCard({ policy }: { policy: AutoSubmitPolicy }) {
  const entries = Object.entries(policy.checks ?? {});
  const failed = entries.filter(([, ok]) => !ok).length;
  return (
    <Section title={<>Auto-submit policy {policy.allowed ? <Badge tone="ok">allowed</Badge> : <Badge tone="bad">blocked</Badge>}</>}>
      <p className="small muted">
        A deterministic gate evaluated from system state only (never from job text or generated content). Every check must pass for
        automatic submission; otherwise the application goes to a handoff. {entries.length ? `${entries.length - failed}/${entries.length} checks pass.` : ""}
      </p>
      <ul className="checklist">
        {entries.map(([k, ok]) => (
          <li key={k} className={ok ? "pass" : "fail"}>
            <Check ok={ok} /> <span>{humanize(k)}</span> <code className="tiny muted">{k}</code>
          </li>
        ))}
      </ul>
      {policy.reasons.length > 0 && (
        <>
          <h3>Reasons</h3>
          <ul className="plain-list small">{policy.reasons.map((r, i) => <li key={i}>{r}</li>)}</ul>
        </>
      )}
    </Section>
  );
}

function MatchCard({ m }: { m: MatchData }) {
  const comps = Object.entries(m.components ?? {});
  return (
    <Section title={<>Match explanation <Badge tone={scoreTone(m.score)}>{m.score.toFixed(1)}</Badge></>}>
      <p className="small muted">Deterministic scoring. Only APPROVED facts count as evidence; missing evidence lowers the score and is never assumed.</p>
      <div className="table-wrap">
        <table className="table compact">
          <thead><tr><th>Component</th><th>Points / max</th><th /><th>Details</th></tr></thead>
          <tbody>
            {comps.map(([name, c]) => {
              const extras = Object.entries(c).filter(([k]) => k !== "points" && k !== "max");
              const pct = c.max ? Math.max(0, Math.min(100, (100 * c.points) / c.max)) : 0;
              return (
                <tr key={name}>
                  <td>{name}</td>
                  <td className="nowrap">{c.points} / {c.max}</td>
                  <td className="bar-cell"><progress className="meter" value={pct} max={100} aria-label={`${name} ${Math.round(pct)}%`} /></td>
                  <td className="tiny muted">
                    {extras.map(([k, v]) => `${k.replace(/_/g, " ")}: ${Array.isArray(v) ? v.join(", ") || "—" : v === null ? "—" : String(v)}`).join(" · ")}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="two-col">
        <div>
          <h3>Evidence</h3>
          {m.evidence.length === 0 ? <p className="small muted">None.</p> : (
            <ul className="plain-list small">
              {m.evidence.map((e, i) => (
                <li key={i}>
                  <Badge tone="info">{e.criterion}</Badge> {e.detail}{" "}
                  {(e.fact_ids ?? []).map((fid) => <span key={fid} className="chip chip-fact">fact #{fid}</span>)}
                </li>
              ))}
            </ul>
          )}
        </div>
        <div>
          <h3>Unmet</h3>
          {m.unmet.length === 0 ? <p className="small muted">None.</p> : <ul className="plain-list small">{m.unmet.map((u, i) => <li key={i} className="text-warn">{u}</li>)}</ul>}
          {m.exclusions.length > 0 && (
            <>
              <h3>Hard exclusions</h3>
              <ul className="plain-list small">{m.exclusions.map((u, i) => <li key={i} className="text-bad">{u}</li>)}</ul>
            </>
          )}
          {m.notes.length > 0 && (
            <>
              <h3>Notes</h3>
              <ul className="plain-list small muted">{m.notes.map((u, i) => <li key={i}>{u}</li>)}</ul>
            </>
          )}
          {m.required_skills.length > 0 && (
            <p className="tiny muted">Skills detected in listing: {m.required_skills.join(", ")}</p>
          )}
        </div>
      </div>
    </Section>
  );
}

function PacketCard({ a, onChanged }: { a: ApplicationDetail; onChanged: () => void }) {
  const pk = a.packet;
  if (!pk) {
    return (
      <Section title="Application packet">
        <Empty>No packet yet. {["MATCHED", "PREPARED", "NEEDS_REVIEW"].includes(a.state) ? "Use “Prepare packet” above." : "A packet can be prepared once the job is MATCHED."}</Empty>
      </Section>
    );
  }
  return (
    <Section title={<>Application packet v{pk.version} {pk.approved_at ? <Badge tone="ok">approved {fmtDate(pk.approved_at)}</Badge> : <Badge tone="warn">not approved</Badge>}{" "}
      {pk.unsupported_count > 0 ? <Badge tone="warn">{pk.unsupported_count} open item(s)</Badge> : <Badge tone="ok">no open items</Badge>}</>}>
      <p className="small muted">Every line cites its source: an approved fact, a profile field, a sanitized job field, or fixed template text. Nothing is invented.</p>
      <details open>
        <summary><strong>Resume</strong> ({pk.resume_lines.length} lines)</summary>
        <ul className="packet-lines">
          {pk.resume_lines.map((l, i) => (
            <li key={i} className={`sec-${l.section}`}>
              <span className="sec-tag tiny">{l.section}</span>
              <span className="line-text">{l.text}</span>
              <ProvenanceChips p={l} />
            </li>
          ))}
        </ul>
      </details>
      <details>
        <summary><strong>Cover letter</strong> ({pk.cover_letter.length} units)</summary>
        <ul className="packet-lines">
          {pk.cover_letter.map((u, i) => (
            <li key={i}>
              <span className="line-text">{u.text}</span>
              <ProvenanceChips p={u} />
            </li>
          ))}
        </ul>
      </details>
      <AnswersTable appId={a.id} pk={pk} onChanged={onChanged} />
    </Section>
  );
}

function AnswersTable({ appId, pk, onChanged }: { appId: number; pk: Packet; onChanged: () => void }) {
  const { canWrite } = useApp();
  const editable = canWrite && !pk.approved_at;
  const std = useAsync(() => (editable ? api.answers(false) : Promise.resolve(null)), [editable]);
  const approved: StandardAnswer[] = (std.data?.answers ?? []).filter((x) => x.approved);
  const act = useAction();

  return (
    <div className="subsection">
      <h3>Form answers</h3>
      <div className="legend tiny">
        <AnswerStatusBadge status="SUPPORTED" /> <AnswerStatusBadge status="USER_APPROVED" /> ready ·{" "}
        <AnswerStatusBadge status="NEEDS_REVIEW" /> needs you ·{" "}
        <AnswerStatusBadge status="SENSITIVE_MISSING" /> <AnswerStatusBadge status="ATTESTATION" /> only you can answer ·{" "}
        <AnswerStatusBadge status="ON_SITE" /> you answer on the employer site
      </div>
      <ErrorBox error={std.error} />
      <ActionFeedback action={act} />
      <div className="table-wrap">
        <table className="table compact">
          <thead><tr><th>Question</th><th>Status</th><th>Answer</th>{editable && <th>Use approved answer</th>}</tr></thead>
          <tbody>
            {pk.answers.map((x, i) => {
              const mappable = editable && x.status !== "ATTESTATION" && x.key !== "review_note" && x.status !== "ON_SITE" && !x.source?.profile_field && !x.source?.packet_file;
              return (
                <tr key={i}>
                  <td>
                    {x.question}
                    <div className="tiny muted"><code>{x.key}</code>{x.required ? " · required" : " · optional"}{x.sensitive ? " · sensitive" : ""}</div>
                  </td>
                  <td><AnswerStatusBadge status={x.status} /></td>
                  <td className="pre-wrap">
                    {x.answer ?? <span className="muted">—</span>}
                    {x.note && <div className="tiny muted">{x.note}</div>}
                  </td>
                  {editable && (
                    <td>
                      {mappable ? (
                        <MapSelect appId={appId} index={i} current={x.source?.answer_id ?? null} options={approved} act={act} onChanged={onChanged} />
                      ) : <span className="tiny muted">—</span>}
                    </td>
                  )}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {editable && approved.length === 0 && !std.loading && (
        <Notice tone="info">You have no approved standard answers to map. Add and approve them on the <a href="#answers">Answers</a> tab.</Notice>
      )}
    </div>
  );
}

function MapSelect({ appId, index, current, options, act, onChanged }: {
  appId: number; index: number; current: number | null; options: StandardAnswer[]; act: ActionState; onChanged: () => void;
}) {
  return (
    <select
      value={current === null ? "" : String(current)}
      disabled={act.busy}
      aria-label="Map to an approved standard answer"
      onChange={async (e) => {
        const v = e.target.value === "" ? null : Number(e.target.value);
        const r = await act.run(() => api.mapAnswer(appId, index, v), (x) => `Answer updated. ${x.unsupported_count} open item(s) remain.`);
        if (r) onChanged();
      }}
    >
      <option value="">— none (needs review) —</option>
      {options.map((o) => (
        <option key={o.id} value={o.id}>
          {o.question_key}{o.sensitive ? " (sensitive)" : `: ${o.answer.length > 40 ? `${o.answer.slice(0, 40)}…` : o.answer}`}
        </option>
      ))}
    </select>
  );
}

function HistoryCard({ a }: { a: ApplicationDetail }) {
  if (!a.attempts.length && !a.handoffs.length) return null;
  return (
    <Section title="Submission attempts & handoffs">
      {a.attempts.length > 0 && (
        <div className="table-wrap">
          <table className="table compact">
            <thead><tr><th>#</th><th>Adapter</th><th>Status</th><th>Confirmation</th><th>Started</th><th>Finished</th><th>Error</th></tr></thead>
            <tbody>
              {a.attempts.map((t) => (
                <tr key={t.id}>
                  <td>{t.id}</td>
                  <td>{t.adapter}</td>
                  <td><Badge tone={t.status === "CONFIRMED" || t.status === "SUBMITTED" ? "ok" : t.status === "PENDING" ? "warn" : "bad"}>{t.status}</Badge></td>
                  <td>{t.confirmation_id ?? ""} {t.confirmation_url && <SafeLink href={t.confirmation_url}>link ↗</SafeLink>}</td>
                  <td className="nowrap">{fmtDate(t.started_at)}</td>
                  <td className="nowrap">{fmtDate(t.finished_at)}</td>
                  <td className="tiny text-bad break">{t.error ?? ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {a.handoffs.length > 0 && (
        <ul className="plain-list">
          {a.handoffs.map((h) => (
            <li key={h.id} className="handoff-mini">
              <Badge tone={h.status === "OPEN" ? "warn" : "muted"}>handoff #{h.id} {h.status}</Badge> <span className="tiny muted">{fmtDate(h.created_at)}</span>
              <ul className="plain-list small">{h.reasons.map((r, i) => <li key={i}>{r}</li>)}</ul>
            </li>
          ))}
        </ul>
      )}
    </Section>
  );
}
