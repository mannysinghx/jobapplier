import { useRef, useState, type FormEvent } from "react";
import { api, ApiError, type AlertImportResult, type ManualJobIn, type ManualJobResult } from "../api";
import { useApp } from "../components/context";
import { useAction } from "../components/useAsync";
import { ActionFeedback, Badge, Field, Notice, ReadOnlyNote, SafeLink, Section, SiteBadge, siteLabel } from "../components/ui";

const ALERT_DOMAINS = ["linkedin.com", "indeed.com", "ziprecruiter.com", "dice.com", "theladders.com"];

export function ImportPage() {
  const { canWrite } = useApp();
  return (
    <div className="page">
      <h1 className="page-title">Import jobs</h1>
      <p className="small muted">
        Bring in jobs from sites that do not allow automated access (LinkedIn, Indeed, ZipRecruiter, Dice, Ladders) using data you
        already have. Nothing here contacts those sites. You always apply on the site yourself, using the packet this app prepares.
      </p>
      {!canWrite && <ReadOnlyNote />}
      {canWrite && <AlertEmailSection />}
      {canWrite && <ManualAddSection />}
      <Section title="LinkedIn data export">
        <p className="small">
          Your LinkedIn data export (uploaded on <a href="#profile">Setup / Profile</a>) now also imports your <strong>Saved Jobs</strong>{" "}
          and your <strong>Job Applications</strong>. Past applications are recorded as already submitted, so the duplicate guard warns
          you before you apply to the same role again. Request the export on LinkedIn under Settings &amp; Privacy → Data privacy →
          “Get a copy of your data”. jobApplier never asks for your LinkedIn password.
        </p>
      </Section>
    </div>
  );
}

function AlertEmailSection() {
  const ref = useRef<HTMLInputElement>(null);
  const act = useAction();
  const [result, setResult] = useState<AlertImportResult | null>(null);

  async function upload(e: FormEvent) {
    e.preventDefault();
    const f = ref.current?.files?.[0];
    if (!f) return;
    setResult(null);
    const r = await act.run(() => api.importAlertEmails(f),
      (x) => `Read ${x.messages} message(s): ${x.jobs_new} new job(s) of ${x.jobs_found} found.`);
    if (r) {
      setResult(r);
      if (ref.current) ref.current.value = "";
    }
  }

  return (
    <Section title="Job-alert emails">
      <div className="two-col">
        <div className="stack">
          <p className="small">
            Upload the job-alert emails those sites send you. Accepted: a single <code>.eml</code>, an <code>.mbox</code> mailbox, or a{" "}
            <code>.zip</code> of <code>.eml</code> files (up to 50 MB).
          </p>
          <ul className="plain-list small">
            <li><strong>Gmail:</strong> open the email → ⋮ (More) → <em>Download message</em> (.eml), or export a label with Google Takeout (.mbox).</li>
            <li><strong>Apple Mail:</strong> select the messages → File → <em>Save As…</em>, or drag them to Finder (.eml); or Mailbox → <em>Export Mailbox…</em> (.mbox).</li>
            <li><strong>Outlook:</strong> open the email → File → <em>Save As</em> → .eml (or drag the message to a folder).</li>
          </ul>
        </div>
        <Notice tone="info">
          <strong>What is read:</strong> only emails sent from {ALERT_DOMAINS.join(", ")}. Every other email is skipped unread.
          Raw emails are not stored; only the extracted job title, employer, location, salary line and link are kept.
          Job links are <strong>never opened automatically</strong>; they are only shown to you, and only if they point at the real site.
        </Notice>
      </div>
      <form onSubmit={upload} className="stack subsection">
        <input ref={ref} type="file" accept=".eml,.mbox,.zip,message/rfc822,application/mbox,application/zip" required />
        <div><button className="btn btn-primary" type="submit" disabled={act.busy}>{act.busy ? "Importing…" : "Import alert emails"}</button></div>
      </form>
      <ActionFeedback action={act} />
      {result && <AlertResult r={result} />}
    </Section>
  );
}

function AlertResult({ r }: { r: AlertImportResult }) {
  const sites = Object.entries(r.by_site ?? {});
  const matched = Object.entries(r.matched ?? {});
  return (
    <div className="subsection stack">
      <div className="kv-inline">
        <Stat v={r.messages} k="messages read" />
        <Stat v={r.alert_messages} k="alert emails" />
        <Stat v={r.skipped_non_alert_messages} k="skipped (not an alert)" />
        <Stat v={r.jobs_found} k="jobs found" />
        <Stat v={r.jobs_new} k="new jobs" />
        <Stat v={r.expired_stale} k="old imports expired" />
      </div>
      {sites.length > 0 && (
        <div className="badges">
          <span className="small muted">By site:</span>
          {sites.map(([site, n]) => <span key={site} className="badges"><SiteBadge site={site} /><span className="small">{n}</span></span>)}
        </div>
      )}
      {r.rejected_links > 0 ? (
        <Notice tone="warn">
          <strong>{r.rejected_links} link(s) rejected.</strong> These links did not point to the real site’s own domain (for example a
          look-alike or redirect domain), which can be a sign of phishing. Those jobs were not imported. Do not click links in those
          emails; open the job directly on the site instead.
        </Notice>
      ) : r.alert_messages > 0 ? (
        <p className="tiny muted">No suspicious links: every job link pointed at the real site.</p>
      ) : null}
      {r.alert_messages === 0 && r.messages > 0 && (
        <Notice tone="info">No alert emails were found in this file. Only mail sent from {ALERT_DOMAINS.join(", ")} is read.</Notice>
      )}
      {matched.length > 0 && (
        <div className="small">
          Re-match: {matched.map(([k, v]) => `${String(v)} ${k}`).join(" · ")}. <a href="#jobs">View jobs →</a>
        </div>
      )}
    </div>
  );
}

function Stat({ v, k }: { v: number; k: string }) {
  return <span className="stat"><span className="stat-v">{v}</span> <span className="stat-k">{k}</span></span>;
}

const EMPTY_FORM = { url: "", title: "", employer: "", location: "", salary: "", description: "" };

function ManualAddSection() {
  const [f, setF] = useState(EMPTY_FORM);
  const act = useAction();
  const [result, setResult] = useState<ManualJobResult | null>(null);
  const set = (k: keyof typeof EMPTY_FORM, v: string) => setF((x) => ({ ...x, [k]: v }));
  const urlOk = f.url.trim().startsWith("https://");

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!urlOk) return;
    const body: ManualJobIn = {
      url: f.url.trim(),
      title: f.title.trim(),
      employer: f.employer.trim(),
      location: f.location.trim() || null,
      salary: f.salary.trim() || null,
      description: f.description.trim() || null,
    };
    setResult(null);
    const r = await act.run(() => api.addManualJob(body), (x) => `Added “${x.job.title}” (${siteLabel(x.job.site)}).`);
    if (r) {
      setResult(r);
      setF(EMPTY_FORM);
    }
  }

  return (
    <Section title="Add a job manually">
      <p className="small muted">
        Viewing a job on LinkedIn, Indeed, ZipRecruiter, Dice, Ladders or a company site? Paste its link and details here. Pasting the full
        description gives a much better match score; you can also add it later from the job’s page.
      </p>
      <form onSubmit={submit} className="stack">
        <div className="grid-form">
          <Field label="Job URL * (https://)" wide hint={f.url && !urlOk ? <span className="text-bad">Must start with https://</span> : undefined}>
            <input type="url" value={f.url} onChange={(e) => set("url", e.target.value)} required maxLength={600} placeholder="https://www.linkedin.com/jobs/view/…" />
          </Field>
          <Field label="Job title *">
            <input value={f.title} onChange={(e) => set("title", e.target.value)} required maxLength={300} />
          </Field>
          <Field label="Employer *">
            <input value={f.employer} onChange={(e) => set("employer", e.target.value)} required maxLength={200} />
          </Field>
          <Field label="Location">
            <input value={f.location} onChange={(e) => set("location", e.target.value)} maxLength={200} placeholder="e.g. Remote (US)" />
          </Field>
          <Field label="Salary (as shown)">
            <input value={f.salary} onChange={(e) => set("salary", e.target.value)} maxLength={120} placeholder="e.g. $150K–$170K/yr" />
          </Field>
          <Field label="Full description (optional)" wide hint="Copy it from the job page in your browser.">
            <textarea rows={6} value={f.description} onChange={(e) => set("description", e.target.value)} maxLength={60000} />
          </Field>
        </div>
        <div><button className="btn btn-primary" type="submit" disabled={act.busy || !urlOk}>{act.busy ? "Adding…" : "Add job"}</button></div>
      </form>
      <ActionFeedback action={act} />
      {result && <ManualResult r={result} />}
    </Section>
  );
}

function ManualResult({ r }: { r: ManualJobResult }) {
  const follow = useAction();
  const [done, setDone] = useState(false);
  const sb = r.suggested_board;

  async function followBoard() {
    if (!sb) return;
    const ok = await follow.run(async () => {
      const sources = await api.sources();
      const src = sources.find((s) => s.key === sb.source_key);
      if (!src) throw new Error(`source ${sb.source_key} is not in the registry`);
      if (!src.enabled) await api.patchSource(sb.source_key, { enabled: true });
      try {
        await api.addBoard({ source_key: sb.source_key, board_token: sb.board_token, employer_name: r.job.employer || null });
      } catch (e) {
        if (e instanceof ApiError && e.status === 409) return "already";
        throw e;
      }
      return "added";
    }, (x) => (x === "already"
      ? `You already follow ${siteLabel(sb.source_key)}/${sb.board_token}.`
      : `Following ${siteLabel(sb.source_key)}/${sb.board_token}. New postings from this employer arrive on the next poll.`));
    if (ok) setDone(true);
  }

  return (
    <div className="subsection stack">
      <div className="small">
        <SiteBadge site={r.job.site} />{" "}
        {r.application_id ? <a href={`#jobs/${r.application_id}`}>Open “{r.job.title}” →</a> : <span className="muted">No application was created (for example the listing is a duplicate).</span>}
        {r.job.meta?.summary_only && <> <Badge tone="warn">summary only</Badge> <span className="tiny muted">add the full description on the job page for a better score</span></>}
      </div>
      {sb && (
        <Notice tone="info">
          This listing is on {r.job.employer}’s public {siteLabel(sb.source_key)} board (<code>{sb.board_token}</code>). Follow the board to get
          all of this employer’s postings automatically through the official API.{" "}
          <button className="btn btn-small btn-primary" onClick={followBoard} disabled={follow.busy || done}>
            {done ? "Following" : "Follow this employer’s board"}
          </button>
        </Notice>
      )}
      <ActionFeedback action={follow} />
      <div className="tiny muted">Listing: <SafeLink href={r.job.apply_url}>{r.job.apply_url ?? ""}</SafeLink></div>
    </div>
  );
}
