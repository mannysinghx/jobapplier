import { useCallback, useEffect, useRef, useState, type FormEvent, type KeyboardEvent as ReactKeyboardEvent, type ReactNode } from "react";
import { api, errorMessage, type AssistData } from "../api";
import { copyAsync, copyText, loadChecklist, openInMainWindow, saveChecklist } from "../components/assist";
import { useApp } from "../components/context";
import { useAction, useAsync } from "../components/useAsync";
import {
  ActionFeedback, AnswerStatusBadge, Badge, ErrorBox, Field, Loading, Notice, SafeLink, SiteBadge, StateBadge, fmtDate, siteLabel,
} from "../components/ui";

/**
 * Apply Assistant: a narrow pop-out that sits beside the job site in the user's own browser.
 * It NEVER automates anything on the job site. It copies prepared values to the clipboard and records what the user did.
 */
export function AssistPage({ id }: { id: number }) {
  const { canWrite, navigate } = useApp();
  const res = useAsync(() => api.assist(id), [id]);
  const d = res.data;
  const [help, setHelp] = useState(false);

  const openJob = useCallback(() => {
    const url = d?.job.apply_url;
    if (url && url.startsWith("https://")) window.open(url, "_blank", "noopener,noreferrer");
  }, [d]);

  const goNext = useCallback(() => {
    if (d?.next_application_id) navigate(`#assist/${d.next_application_id}`);
  }, [d, navigate]);

  // Keyboard: j/k move between copyable rows, c/Enter copy, o opens the job page, n = next, ? = help.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      const t = e.target as HTMLElement | null;
      if (t && (t.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName))) return;
      const rows = Array.from(document.querySelectorAll<HTMLElement>("[data-copy-row]"));
      const active = document.activeElement as HTMLElement | null;
      const row = active?.closest<HTMLElement>("[data-copy-row]") ?? null;
      const idx = row ? rows.indexOf(row) : -1;
      const focusRow = (i: number) => {
        const r = rows[Math.max(0, Math.min(rows.length - 1, i))];
        if (r) {
          r.focus();
          r.scrollIntoView({ block: "nearest" });
        }
      };
      switch (e.key) {
        case "j":
          e.preventDefault();
          focusRow(idx + 1);
          break;
        case "k":
          e.preventDefault();
          focusRow(idx < 0 ? 0 : idx - 1);
          break;
        case "c":
          if (row) {
            e.preventDefault();
            row.querySelector<HTMLButtonElement>("[data-copy-btn]")?.click();
          }
          break;
        case "Enter":
          if (row && active === row) {
            e.preventDefault();
            row.querySelector<HTMLButtonElement>("[data-copy-btn]")?.click();
          }
          break;
        case "o":
          e.preventDefault();
          openJob();
          break;
        case "n":
          e.preventDefault();
          goNext();
          break;
        case "?":
          e.preventDefault();
          setHelp((h) => !h);
          break;
        case "Escape":
          setHelp(false);
          break;
        default:
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [openJob, goNext]);

  useEffect(() => {
    document.title = d ? `Apply: ${d.job.title} — jobApplier` : "Apply Assistant — jobApplier";
    return () => {
      document.title = "jobApplier";
    };
  }, [d]);

  return (
    <div className="assist">
      <div className="assist-toolbar">
        <span className="tiny muted">Apply Assistant · nothing is automated on the job site</span>
        <div className="assist-help-wrap">
          <button className="btn btn-small" onClick={() => setHelp((h) => !h)} aria-expanded={help} aria-label="Keyboard shortcuts">?</button>
          {help && (
            <div className="assist-help" role="dialog" aria-label="Keyboard shortcuts">
              <ul className="plain-list small">
                <li><kbd>j</kbd> / <kbd>k</kbd> next / previous copyable row</li>
                <li><kbd>c</kbd> or <kbd>Enter</kbd> copy the focused row</li>
                <li><kbd>o</kbd> open the job page</li>
                <li><kbd>n</kbd> next application</li>
                <li><kbd>?</kbd> toggle this help · <kbd>Esc</kbd> close</li>
              </ul>
              <p className="tiny muted">Shortcuts are ignored while you type in a field.</p>
            </div>
          )}
        </div>
      </div>
      <ErrorBox error={res.error} />
      {res.loading && !d ? <Loading /> : !d ? null : (
        <>
          <JobCard d={d} />
          <StatusBar d={d} onChanged={res.reload} />
          <FieldsSection d={d} />
          <AnswersSection d={d} canReveal={canWrite} />
          <OtherAnswersSection d={d} canReveal={canWrite} />
          <CoverLetterSection d={d} />
          <OpenItemsSection d={d} />
          {canWrite && <DoneSection d={d} onRecorded={res.reload} />}
          {!canWrite && d.next_application_id && (
            <button className="btn btn-block" onClick={goNext}>Next application →</button>
          )}
        </>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ copy button & row */
function CopyButton({ getText, label = "Copy", disabled, title }: {
  getText: () => string | Promise<string>;
  label?: string;
  disabled?: boolean;
  title?: string;
}) {
  const [state, setState] = useState<"idle" | "ok" | "err">("idle");
  const [err, setErr] = useState<string | null>(null);
  const timer = useRef<number | undefined>(undefined);
  useEffect(() => () => window.clearTimeout(timer.current), []);

  async function onClick() {
    setErr(null);
    try {
      const v = getText();
      if (typeof v === "string") await copyText(v);
      else await copyAsync(() => v);
      setState("ok");
    } catch (e) {
      setState("err");
      setErr(errorMessage(e));
    }
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setState("idle"), 1600);
  }

  return (
    <button type="button" className={`btn btn-small copy-btn ${state === "ok" ? "copied" : ""}`} data-copy-btn onClick={onClick}
      disabled={disabled} title={err ?? title} aria-live="polite">
      {state === "ok" ? "✓ Copied" : state === "err" ? "✗ Failed" : label}
    </button>
  );
}

function CopyRow({ label, sub, value, children }: { label: ReactNode; sub?: ReactNode; value: ReactNode; children: ReactNode }) {
  return (
    <div className="assist-row" data-copy-row tabIndex={0}
      onKeyDown={(e: ReactKeyboardEvent) => { if (e.key === " " && e.target === e.currentTarget) e.preventDefault(); }}>
      <div className="assist-row-main">
        <div className="assist-label">{label}</div>
        <div className="assist-value">{value}</div>
        {sub}
      </div>
      <div className="assist-row-act">{children}</div>
    </div>
  );
}

function Block({ title, children, right }: { title: ReactNode; children: ReactNode; right?: ReactNode }) {
  return (
    <section className="assist-block">
      <div className="assist-block-head"><h2>{title}</h2>{right}</div>
      {children}
    </section>
  );
}

/* ------------------------------------------------------------------ sections */
function JobCard({ d }: { d: AssistData }) {
  const j = d.job;
  return (
    <section className="assist-block assist-job">
      <div className="assist-job-top">
        <SiteBadge site={d.site} />
        {d.application.score !== null && <Badge tone="info">score {d.application.score.toFixed(0)}</Badge>}
        <StateBadge state={d.application.state} />
        {d.queue_position !== null && <span className="tiny muted">{d.queue_position} of {d.queue_length}</span>}
      </div>
      <h1 className="assist-title">{j.title}</h1>
      <div className="assist-emp">{j.employer}{j.location ? <span className="muted"> · {j.location}</span> : null}</div>
      <div className="assist-open">
        <SafeLink href={j.apply_url}>Open job page ↗</SafeLink>
      </div>
      {d.self_apply_note && <p className="small apply-self">{d.self_apply_note}</p>}
      {j.source === "dice" && d.source_attribution && <Notice tone="info"><strong>Dice:</strong> {d.source_attribution}</Notice>}
      {d.prior_applications.length > 0 && (
        <Notice tone="warn">
          <strong>You may already have applied to this role:</strong>
          <ul className="plain-list small">
            {d.prior_applications.map((p) => (
              <li key={p.application_id}>
                <a href={`#jobs/${p.application_id}`} onClick={(e) => { e.preventDefault(); openInMainWindow(`#jobs/${p.application_id}`); }}>
                  #{p.application_id}
                </a>{" "}
                {p.state.replace(/_/g, " ").toLowerCase()} via {siteLabel(p.source)}, {fmtDate(p.updated_at)}{p.note ? ` (${p.note})` : ""}
              </li>
            ))}
          </ul>
        </Notice>
      )}
      {d.application.notes && <p className="small"><span className="muted">Notes: </span><span className="pre-wrap">{d.application.notes}</span></p>}
    </section>
  );
}

function StatusBar({ d, onChanged }: { d: AssistData; onChanged: () => void }) {
  const { canWrite } = useApp();
  const act = useAction();
  const n = d.open_items.length;

  async function approve() {
    const ok = window.confirm(
      `Approve packet v${d.packet?.version ?? "?"} for this application?\n\n` +
      (n ? `${n} open item(s) will be marked ON_SITE: you answer them yourself on the job site. ` : "") +
      "Approval records exactly which packet you used, so your submission record matches what you pasted.",
    );
    if (ok && (await act.run(() => api.approve(d.application.id, true), "Approved. Values below are ready to paste."))) onChanged();
  }

  return (
    <section className={`assist-status ${d.ready ? "is-ready" : "not-ready"}`}>
      {d.ready ? (
        <div className="small">
          <strong>Ready.</strong> Packet v{d.packet?.version} approved {fmtDate(d.packet?.approved_at)}. Paste the values below into the job site.
        </div>
      ) : (
        <div className="small">
          {d.can_prepare && "No packet yet. Prepare one to get tailored values to paste."}
          {d.can_approve && "Not approved yet. Approval records exactly which packet version you used for this application."}
          {!d.can_prepare && !d.can_approve && `This application is ${d.application.state.replace(/_/g, " ").toLowerCase()}; nothing to approve here.`}
        </div>
      )}
      {canWrite && (d.can_prepare || d.can_approve) && (
        <div className="btn-row">
          {d.can_prepare && (
            <button className="btn btn-primary" disabled={act.busy}
              onClick={async () => { if (await act.run(() => api.prepare(d.application.id), (r) => `Packet prepared (${r.unsupported_count} open item(s)).`)) onChanged(); }}>
              Prepare packet
            </button>
          )}
          {d.can_approve && (
            <button className="btn btn-ok" disabled={act.busy} onClick={approve}>Approve (answer open items on the site)</button>
          )}
        </div>
      )}
      <ActionFeedback action={act} />
    </section>
  );
}

function FieldsSection({ d }: { d: AssistData }) {
  if (!d.fields.length) return null;
  return (
    <Block title="Your details">
      {d.fields.map((f) => (
        <CopyRow key={f.key} label={f.label} value={f.value}>
          <CopyButton getText={() => f.value} />
        </CopyRow>
      ))}
    </Block>
  );
}

function DownloadLinks({ d, which }: { d: AssistData; which: "resume" | "cover_letter" | "both" }) {
  const dl = d.downloads;
  if (!dl) return <span className="tiny muted">prepare a packet first</span>;
  const safe = (u: string) => (u.startsWith("/api/") ? u : null); // relative API paths only
  const resume = safe(dl.resume_docx);
  const letter = safe(dl.cover_letter_txt);
  return (
    <span className="assist-dl">
      {(which === "resume" || which === "both") && resume && <a className="btn btn-small" href={resume} download>Resume .docx</a>}
      {(which === "cover_letter" || which === "both") && letter && <a className="btn btn-small" href={letter} download>Cover letter .txt</a>}
    </span>
  );
}

function AnswersSection({ d, canReveal }: { d: AssistData; canReveal: boolean }) {
  if (!d.packet) return null;
  return (
    <Block title="Form answers">
      {d.answers.length === 0 && <p className="small muted">No form questions in this packet.</p>}
      {d.answers.map((a) => {
        const sub = (
          <div className="assist-sub">
            <AnswerStatusBadge status={a.status} />
            {a.required && <span className="tiny muted">required</span>}
            {a.sensitive && <Badge tone="bad">sensitive</Badge>}
            {a.note && a.value === null && !a.file && <span className="tiny muted">{a.note}</span>}
          </div>
        );
        if (a.file) {
          return (
            <div className="assist-row" key={a.index}>
              <div className="assist-row-main">
                <div className="assist-label">{a.question}</div>
                {sub}
              </div>
              <div className="assist-row-act"><DownloadLinks d={d} which={a.file} /></div>
            </div>
          );
        }
        if (a.value === null) {
          return (
            <div className="assist-row no-value" key={a.index}>
              <div className="assist-row-main">
                <div className="assist-label">{a.question}</div>
                <div className="assist-value muted small">{a.status === "ON_SITE" || a.status === "ATTESTATION" || a.status === "SENSITIVE_MISSING" ? "Answer this yourself on the site." : "No prepared answer."}</div>
                {sub}
              </div>
            </div>
          );
        }
        if (a.sensitive) {
          const aid = a.answer_id;
          return (
            <CopyRow key={a.index} label={a.question} sub={sub} value={<span className="muted">•••••• (hidden)</span>}>
              <CopyButton disabled={!canReveal || aid === null} title={!canReveal ? "Admins only" : "Reveals the value only to copy it (audited)"}
                getText={() => api.assistReveal(d.application.id, aid as number).then((r) => r.value)} />
            </CopyRow>
          );
        }
        const v = a.value;
        return (
          <CopyRow key={a.index} label={a.question} sub={sub} value={v}>
            <CopyButton getText={() => v} />
          </CopyRow>
        );
      })}
    </Block>
  );
}

function OtherAnswersSection({ d, canReveal }: { d: AssistData; canReveal: boolean }) {
  if (!d.other_answers.length) return null;
  return (
    <Block title="Other approved answers">
      <p className="tiny muted">Approved answers not already used above, for extra questions the site asks.</p>
      {d.other_answers.map((o) => (
        <CopyRow key={o.answer_id} label={o.label}
          sub={o.sensitive ? <div className="assist-sub"><Badge tone="bad">sensitive</Badge></div> : undefined}
          value={o.sensitive ? <span className="muted">•••••• (hidden)</span> : o.value}>
          {o.sensitive ? (
            <CopyButton disabled={!canReveal} title={!canReveal ? "Admins only" : "Reveals the value only to copy it (audited)"}
              getText={() => api.assistReveal(d.application.id, o.answer_id).then((r) => r.value)} />
          ) : (
            <CopyButton getText={() => o.value} />
          )}
        </CopyRow>
      ))}
    </Block>
  );
}

function CoverLetterSection({ d }: { d: AssistData }) {
  if (!d.packet) return null;
  const text = d.cover_letter_text;
  return (
    <Block title="Cover letter">
      {text ? (
        <CopyRow label="Full letter" value={<span className="tiny muted">{text.length.toLocaleString()} characters</span>}>
          <CopyButton label="Copy full letter" getText={() => text} />
        </CopyRow>
      ) : <p className="small muted">No cover letter in this packet.</p>}
      <div className="assist-dl-row"><DownloadLinks d={d} which="both" /></div>
      {text && (
        <details className="assist-preview">
          <summary className="small">Preview</summary>
          <pre className="untrusted">{text}</pre>
        </details>
      )}
    </Block>
  );
}

function OpenItemsSection({ d }: { d: AssistData }) {
  const appId = d.application.id;
  const [checked, setChecked] = useState<string[]>(() => loadChecklist(appId));
  useEffect(() => setChecked(loadChecklist(appId)), [appId]);
  if (!d.open_items.length) return null;
  const keyOf = (i: number, k: string) => `${i}:${k}`;
  const done = d.open_items.filter((x, i) => checked.includes(keyOf(i, x.key))).length;

  function toggle(k: string, on: boolean) {
    const next = on ? [...checked.filter((x) => x !== k), k] : checked.filter((x) => x !== k);
    setChecked(next);
    saveChecklist(appId, next);
  }

  return (
    <Block title="Open items" right={<span className="tiny muted">{done}/{d.open_items.length} done</span>}>
      <p className="tiny muted">Answer these yourself on the job site. Ticks are only remembered in this browser.</p>
      <ul className="assist-checklist">
        {d.open_items.map((x, i) => {
          const k = keyOf(i, x.key);
          return (
            <li key={k}>
              <label className="checkbox">
                <input type="checkbox" checked={checked.includes(k)} onChange={(e) => toggle(k, e.target.checked)} />
                <span>
                  {x.question} <AnswerStatusBadge status={x.status} />
                  {x.note && <span className="tiny muted block">{x.note}</span>}
                </span>
              </label>
            </li>
          );
        })}
      </ul>
    </Block>
  );
}

function DoneSection({ d, onRecorded }: { d: AssistData; onRecorded: () => void }) {
  const { navigate } = useApp();
  const act = useAction();
  const [confId, setConfId] = useState("");
  const [confUrl, setConfUrl] = useState("");
  const [recorded, setRecorded] = useState<{ next: number | null } | null>(null);
  useEffect(() => setRecorded(null), [d.application.id]);
  const urlBad = confUrl.trim() !== "" && !confUrl.trim().startsWith("https://");

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (urlBad) return;
    const next = d.next_application_id; // captured before the reload drops this application from the queue
    const r = await act.run(() => api.manualSubmission(d.application.id, confId.trim() || null, confUrl.trim() || null),
      (x) => `Recorded. State: ${x.state.toLowerCase()}.`);
    if (r) {
      setRecorded({ next });
      setConfId("");
      setConfUrl("");
      onRecorded();
    }
  }

  if (recorded) {
    return (
      <Block title="Done applying?">
        <div className="alert alert-ok"><strong>✓ Recorded.</strong> Your submission and the exact packet you used are saved.</div>
        {recorded.next ? (
          <button className="btn btn-primary btn-block" onClick={() => navigate(`#assist/${recorded.next}`)}>Next application →</button>
        ) : (
          <p className="small">Queue finished. <a href="#apply" onClick={(e) => { e.preventDefault(); openInMainWindow("#apply"); }}>Back to the Apply queue</a></p>
        )}
      </Block>
    );
  }

  const already = ["SUBMITTED", "CONFIRMED", "FOLLOW_UP"].includes(d.application.state);
  return (
    <Block title="Done applying?">
      {already ? (
        <p className="small">Already recorded as {d.application.state.toLowerCase()}.</p>
      ) : !d.ready ? (
        <p className="small muted">Approve the packet first (above). Then submit on the job site and record it here.</p>
      ) : (
        <form onSubmit={submit} className="stack">
          <p className="tiny muted">After you submit on the job site, record it. Both fields are optional.</p>
          <Field label="Confirmation number">
            <input value={confId} onChange={(e) => setConfId(e.target.value)} maxLength={200} autoComplete="off" />
          </Field>
          <Field label="Confirmation URL (https://)" hint={urlBad ? <span className="text-bad">Must start with https://</span> : undefined}>
            <input type="url" value={confUrl} onChange={(e) => setConfUrl(e.target.value)} maxLength={600} placeholder="https://…" />
          </Field>
          <button className="btn btn-primary btn-block" type="submit" disabled={act.busy || urlBad}>I submitted it — record</button>
        </form>
      )}
      <ActionFeedback action={act} />
      {d.next_application_id && (
        <button className="btn btn-block assist-skip" onClick={() => navigate(`#assist/${d.next_application_id}`)}>Skip to next application →</button>
      )}
    </Block>
  );
}
