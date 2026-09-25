import { useState, type FormEvent } from "react";
import {
  api, DICE_EMPLOYMENT_TYPES, DICE_WORKPLACE_TYPES, errorMessage, type BoardIn, type DicePosted, type MatchCounts, type PollRun,
  type Source, type SourcePatch,
} from "../api";
import { useApp } from "../components/context";
import { useAction, useAsync } from "../components/useAsync";
import {
  ActionFeedback, Badge, Empty, ErrorBox, Field, Loading, Notice, ReadOnlyNote, SafeLink, Section, SiteBadge, YesNo, fmtDate,
  fmtSearchParams, type Tone,
} from "../components/ui";

/** ConnectorRun.status: running | ok | error | skipped */
function runTone(status: string): Tone {
  return status === "ok" ? "ok" : status === "error" ? "bad" : status === "skipped" ? "warn" : "info";
}

function autoSubmitBlockers(s: Source): string[] {
  const r: string[] = [];
  if (!s.submit_permitted) r.push("the source’s terms do not permit automated submission (registry)");
  if (!s.verified_adapter) r.push("no verified submission integration exists for this source");
  return r;
}

function enableBlockers(s: Source): string[] {
  const r: string[] = [];
  if (!s.read_permitted) r.push("registry does not permit reading this source");
  if (!s.implemented) r.push("no connector implemented");
  if (!s.review_current) r.push("permission review is out of date");
  return r;
}

type SourceGroup = "boards" | "search" | "imports" | "blocked";

const IMPORT_KEYS = new Set(["email_alert", "user_import"]);

/** Which section a registry source belongs in. Unknown future sources fall into "boards" or "blocked" by permission. */
function groupOf(s: Source): SourceGroup {
  if (IMPORT_KEYS.has(s.key) || s.method === "user_mailbox_import" || s.method === "user_provided") return "imports";
  if (!s.read_permitted) return "blocked";
  if (s.key === "dice" || s.method === "mcp_search") return "search";
  return "boards";
}

const GROUPS: { id: SourceGroup; title: string; blurb: string }[] = [
  { id: "boards", title: "Employer boards (Greenhouse / Lever / Ashby)",
    blurb: "Public job-board APIs that employers publish. You follow individual companies by their board token." },
  { id: "search", title: "Search (Dice)",
    blurb: "Saved searches run through Dice’s official MCP server. Full descriptions are fetched only when you ask, one job at a time." },
  { id: "imports", title: "Your imports (alert emails, manual, LinkedIn export)",
    blurb: "Data you provide yourself. No job site is contacted. These are always available from the Import tab." },
  { id: "blocked", title: "Sites without a permitted automated route (LinkedIn, Indeed, ZipRecruiter, Ladders)",
    blurb: "Their terms forbid bots and scrapers, so this app never contacts them directly. Their jobs still reach you through your own alert emails and imports." },
];

export function SourcesPage() {
  const { canWrite } = useApp();
  const sources = useAsync(() => api.sources(), []);
  const anyAutoSubmit = (sources.data ?? []).some((s) => autoSubmitBlockers(s).length === 0);
  const update = (ns: Source) => sources.setData(sources.data?.map((x) => (x.key === ns.key ? ns : x)));

  return (
    <div className="page">
      <h1 className="page-title">Sources</h1>
      {!canWrite && <ReadOnlyNote />}
      <PipelineSection />
      {sources.data && !anyAutoSubmit && (
        <Notice tone="info">
          <strong>Auto-submit is unavailable for every source today.</strong> No source both permits automated submission and has a
          verified submission integration. This is expected: every approved application becomes a <a href="#handoffs">handoff</a> with
          a ready packet, and you submit it yourself on the employer’s site. Reading (discovery) works for enabled sources.
        </Notice>
      )}
      <ErrorBox error={sources.error} />
      {sources.loading && !sources.data ? <Loading /> : !sources.data?.length ? <Empty>No sources in the registry.</Empty> : (
        GROUPS.map((g) => {
          const list = sources.data!.filter((s) => groupOf(s) === g.id);
          if (!list.length) return null;
          return (
            <Section key={g.id} title={g.title}
              actions={<button className="btn btn-small" onClick={sources.reload} disabled={sources.loading}>Refresh</button>}>
              <p className="small muted">{g.blurb}</p>
              <div className="source-grid">
                {list.map((s) => <SourceCard key={s.key} s={s} group={g.id} onChange={update} />)}
              </div>
            </Section>
          );
        })
      )}
      <BoardsSection sources={sources.data ?? []} onSourceChanged={update} />
      <HealthSection />
    </div>
  );
}

function SourceCard({ s, group, onChange }: { s: Source; group: SourceGroup; onChange: (s: Source) => void }) {
  const { canWrite } = useApp();
  const [msg, setMsg] = useState<{ tone: "bad" | "ok"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [limit, setLimit] = useState(String(s.daily_submit_limit));
  const autoBlock = autoSubmitBlockers(s);
  const enBlock = enableBlockers(s);
  const togglable = group === "boards" || group === "search";

  async function patch(p: SourcePatch, okText: string) {
    setBusy(true);
    setMsg(null);
    try {
      onChange(await api.patchSource(s.key, p));
      setMsg({ tone: "ok", text: okText });
    } catch (e) {
      setMsg({ tone: "bad", text: `Server refused: ${errorMessage(e)}` });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="source-card">
      <div className="row-between">
        <div>
          <strong>{s.name}</strong> <code className="tiny">{s.key}</code>
          <div className="tiny muted">{s.method}{s.rate_limit_per_minute ? ` · ${s.rate_limit_per_minute}/min` : ""}</div>
        </div>
        <div className="badges">
          {group === "imports" ? <Badge tone="ok">user-initiated</Badge>
            : group === "blocked" ? <Badge tone="bad">no automated access</Badge>
            : s.implemented ? <Badge tone="ok">connector implemented</Badge> : <Badge tone="muted">not implemented</Badge>}
          {togglable && (s.verified_adapter ? <Badge tone="ok">verified submit adapter</Badge> : <Badge tone="muted">no verified adapter</Badge>)}
        </div>
      </div>
      <dl className="source-facts">
        <dt>Read permitted</dt>
        <dd><YesNo value={s.read_permitted} />{s.read_basis && <blockquote className="basis">“{s.read_basis}”</blockquote>}</dd>
        <dt>Submit permitted</dt>
        <dd><YesNo value={s.submit_permitted} />{s.submit_basis && <blockquote className="basis">“{s.submit_basis}”</blockquote>}</dd>
        <dt>Terms checked</dt>
        <dd>
          {s.date_checked || "—"}{" "}
          {s.review_current ? <Badge tone="ok">review current</Badge> : <Badge tone="warn">review out of date</Badge>}
          {s.terms_url && <div className="tiny"><SafeLink href={s.terms_url}>terms</SafeLink></div>}
        </dd>
        {s.notes && (<><dt>Notes</dt><dd className="small">{s.notes}</dd></>)}
      </dl>
      {group === "imports" && (
        <div className="source-toggles small">
          <span><Badge tone="ok">always available</Badge> User-initiated imports: nothing runs until you upload or add something on the <a href="#import">Import</a> tab{s.key === "user_import" ? <> or upload your LinkedIn export on <a href="#profile">Setup / Profile</a></> : null}.</span>
        </div>
      )}
      {group === "blocked" && (
        <div className="source-toggles small">
          <span>
            Supported via job-alert emails, manual add{s.key === "linkedin" ? " and your LinkedIn data export" : ""} on the{" "}
            <a href="#import">Import</a> tab. You always apply on {s.name.replace(/\s*\(direct\)\s*/i, "")} yourself.
          </span>
        </div>
      )}
      {togglable && (
        <div className="source-toggles">
          <label className="checkbox">
            <input type="checkbox" checked={s.enabled} disabled={!canWrite || busy}
              onChange={(e) => patch({ enabled: e.target.checked }, e.target.checked ? "Source enabled for discovery." : "Source disabled.")} />
            <span>Enabled (discovery)</span>
          </label>
          {!s.enabled && enBlock.length > 0 && <div className="tiny muted">Enabling will be refused: {enBlock.join("; ")}.</div>}
          <label className="checkbox">
            <input type="checkbox" checked={s.auto_submit_opt_in} disabled={!canWrite || busy || (autoBlock.length > 0 && !s.auto_submit_opt_in)}
              onChange={(e) => patch({ auto_submit_opt_in: e.target.checked }, e.target.checked ? "Auto-submit opted in." : "Auto-submit opted out.")} />
            <span>Auto-submit opt-in</span>
          </label>
          {autoBlock.length > 0 && (
            <div className="tiny muted">Unavailable: {autoBlock.join("; ")}. Applications for this source go to handoff.</div>
          )}
          {autoBlock.length === 0 && canWrite && (
            <form className="inline-form" onSubmit={(e: FormEvent) => { e.preventDefault(); patch({ daily_submit_limit: Number(limit) }, "Daily limit saved."); }}>
              <label className="tiny">Daily submit limit</label>
              <input type="number" min={0} max={50} value={limit} onChange={(e) => setLimit(e.target.value)} className="num-sm" />
              <button className="btn btn-small" disabled={busy}>Save</button>
            </form>
          )}
        </div>
      )}
      {msg && <div className={`alert alert-${msg.tone} small`}>{msg.text}</div>}
    </div>
  );
}

const POSTED_OPTIONS: { v: DicePosted; label: string }[] = [
  { v: "ONE", label: "1 day" }, { v: "THREE", label: "3 days" }, { v: "SEVEN", label: "7 days" },
];

function BoardsSection({ sources, onSourceChanged }: { sources: Source[]; onSourceChanged: (s: Source) => void }) {
  const { canWrite } = useApp();
  const boards = useAsync(() => api.boards(), []);
  const implemented = sources.filter((s) => s.implemented && s.read_permitted);
  const [sourceKey, setSourceKey] = useState("");
  const [token, setToken] = useState("");
  const [employer, setEmployer] = useState("");
  const [keyword, setKeyword] = useState("");
  const [location, setLocation] = useState("");
  const [workplace, setWorkplace] = useState<string[]>([]);
  const [employment, setEmployment] = useState<string[]>([]);
  const [posted, setPosted] = useState<DicePosted>("SEVEN");
  const add = useAction();
  const rowAction = useAction();
  const effectiveKey = sourceKey || implemented[0]?.key || "";
  const selected = implemented.find((s) => s.key === effectiveKey);
  const isSearch = selected ? groupOf(selected) === "search" : false;

  const toggle = (list: string[], v: string, on: boolean) => (on ? [...list.filter((x) => x !== v), v] : list.filter((x) => x !== v));

  async function submit(e: FormEvent) {
    e.preventDefault();
    const body: BoardIn = isSearch
      ? {
          source_key: effectiveKey,
          params: {
            keyword: keyword.trim(),
            ...(location.trim() ? { location: location.trim() } : {}),
            ...(workplace.length ? { workplace_types: workplace } : {}),
            ...(employment.length ? { employment_types: employment } : {}),
            posted_date: posted,
          },
        }
      : { source_key: effectiveKey, board_token: token.trim(), employer_name: employer.trim() || null };
    const r = await add.run(() => api.addBoard(body),
      (b) => (isSearch ? `Saved search added: ${fmtSearchParams(b.params)}.` : `Added ${b.source_key}/${b.board_token}.`));
    if (r) {
      setToken("");
      setEmployer("");
      setKeyword("");
      setLocation("");
      boards.reload();
    }
  }

  return (
    <Section title="Company boards & saved searches" actions={<button className="btn btn-small" onClick={boards.reload} disabled={boards.loading}>Refresh</button>}>
      <p className="small muted">
        Board token = the company slug in <code>boards.greenhouse.io/&lt;token&gt;</code>, <code>jobs.lever.co/&lt;token&gt;</code>,{" "}
        <code>jobs.ashbyhq.com/&lt;token&gt;</code>. Only public job-board APIs are read (GET only). Choose Dice to save a search instead.
      </p>
      {canWrite && (
        <form onSubmit={submit} className="stack">
          <div className="grid-form board-form">
            <Field label="Source">
              <select value={effectiveKey} onChange={(e) => { setSourceKey(e.target.value); add.clear(); }} required>
                {implemented.length === 0 && <option value="">(no implemented sources)</option>}
                {implemented.map((s) => <option key={s.key} value={s.key}>{s.name}</option>)}
              </select>
            </Field>
            {isSearch ? (
              <>
                <Field label="Keyword *">
                  <input value={keyword} onChange={(e) => setKeyword(e.target.value)} required maxLength={100} placeholder="e.g. python backend engineer" />
                </Field>
                <Field label="Location (optional)">
                  <input value={location} onChange={(e) => setLocation(e.target.value)} maxLength={100} placeholder="e.g. Austin, TX" />
                </Field>
                <Field label="Posted within">
                  <select value={posted} onChange={(e) => setPosted(e.target.value as DicePosted)}>
                    {POSTED_OPTIONS.map((o) => <option key={o.v} value={o.v}>{o.label}</option>)}
                  </select>
                </Field>
              </>
            ) : (
              <>
                <Field label="Board token">
                  <input value={token} onChange={(e) => setToken(e.target.value)} required maxLength={120} pattern="[A-Za-z0-9_.\-]{1,120}" placeholder="e.g. acme" title="letters, digits, '.', '_' and '-'" />
                </Field>
                <Field label="Employer name (optional)">
                  <input value={employer} onChange={(e) => setEmployer(e.target.value)} maxLength={200} placeholder="Acme Corp" />
                </Field>
              </>
            )}
          </div>
          {isSearch && (
            <>
              <div className="check-groups">
                <fieldset className="check-group">
                  <legend>Workplace type (any if none)</legend>
                  {DICE_WORKPLACE_TYPES.map((w) => (
                    <label key={w} className="checkbox">
                      <input type="checkbox" checked={workplace.includes(w)} onChange={(e) => setWorkplace(toggle(workplace, w, e.target.checked))} />
                      <span>{w}</span>
                    </label>
                  ))}
                </fieldset>
                <fieldset className="check-group">
                  <legend>Employment type (any if none)</legend>
                  {DICE_EMPLOYMENT_TYPES.map((t) => (
                    <label key={t} className="checkbox">
                      <input type="checkbox" checked={employment.includes(t)} onChange={(e) => setEmployment(toggle(employment, t, e.target.checked))} />
                      <span>{t.toLowerCase().replace(/_/g, " ")}</span>
                    </label>
                  ))}
                </fieldset>
              </div>
              <p className="tiny muted">
                Searches return summaries only. Open a Dice job and click “Fetch full description” to load one job’s details.
                Dice listings are shown with Dice’s AI-search disclosure.
              </p>
              {selected && !selected.enabled && (
                <Notice tone="warn">
                  Dice is not enabled, so saved searches will not run.{" "}
                  {canWrite && (
                    <button type="button" className="btn btn-small" disabled={rowAction.busy}
                      onClick={async () => { const r = await rowAction.run(() => api.patchSource(selected.key, { enabled: true }), "Dice enabled."); if (r) onSourceChanged(r); }}>
                      Enable Dice
                    </button>
                  )}
                </Notice>
              )}
            </>
          )}
          <div>
            <button className="btn btn-primary" type="submit" disabled={add.busy || !effectiveKey || (isSearch && !keyword.trim())}>
              {isSearch ? "Save search" : "Add board"}
            </button>
          </div>
        </form>
      )}
      <ActionFeedback action={add} />
      <ActionFeedback action={rowAction} />
      <ErrorBox error={boards.error} />
      {boards.loading && !boards.data ? <Loading /> : !boards.data?.length ? <Empty>No boards or saved searches yet. Add one above to start discovering jobs.</Empty> : (
        <div className="table-wrap">
          <table className="table">
            <thead><tr><th>Source</th><th>Board / search</th><th>Employer</th><th>Enabled</th><th>Failures</th><th>Next attempt</th><th>Last success</th>{canWrite && <th />}</tr></thead>
            <tbody>
              {boards.data.map((b) => {
                const search = fmtSearchParams(b.params);
                return (
                  <tr key={b.id}>
                    <td><SiteBadge site={b.source_key} /></td>
                    <td>
                      {search ? <span className="small">{search}</span> : <code>{b.board_token}</code>}
                      {search && <div className="tiny muted mono">{b.board_token}</div>}
                    </td>
                    <td>{b.employer_name ?? <span className="muted">—</span>}</td>
                    <td>
                      <input type="checkbox" checked={b.enabled} disabled={!canWrite || rowAction.busy} aria-label="enabled"
                        onChange={async (e) => { if (await rowAction.run(() => api.setBoardEnabled(b.id, e.target.checked))) boards.reload(); }} />
                    </td>
                    <td>{b.consecutive_failures > 0 ? <Badge tone="warn">{b.consecutive_failures}</Badge> : 0}</td>
                    <td className="nowrap">{fmtDate(b.next_attempt_at)}</td>
                    <td className="nowrap">{fmtDate(b.last_success_at)}</td>
                    {canWrite && (
                      <td className="right">
                        <button className="btn btn-small btn-danger-outline" disabled={rowAction.busy}
                          onClick={async () => {
                            if (!window.confirm(`Remove ${search ? "saved search" : "board"} ${b.source_key}/${b.board_token}? Already discovered jobs are kept.`)) return;
                            if ((await rowAction.run(() => api.deleteBoard(b.id).then(() => true), "Removed.")) === true) boards.reload();
                          }}>Remove</button>
                      </td>
                    )}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </Section>
  );
}

function HealthSection() {
  const health = useAsync(() => api.connectorHealth(), []);
  return (
    <Section title="Connector health" actions={<button className="btn btn-small" onClick={health.reload} disabled={health.loading}>Refresh</button>}>
      <ErrorBox error={health.error} />
      {health.loading && !health.data ? <Loading /> : !health.data?.length ? <Empty>No boards configured.</Empty> : (
        <div className="table-wrap">
          <table className="table">
            <thead><tr><th>Source / board</th><th>Enabled</th><th>Last run</th><th>Status</th><th>Seen / new / expired</th><th>Failures</th><th>Next attempt</th><th>Last success</th></tr></thead>
            <tbody>
              {health.data.map((h) => (
                <tr key={h.board_id}>
                  <td>{h.source} / <code>{h.board}</code></td>
                  <td><YesNo value={h.enabled} /></td>
                  <td className="nowrap">{fmtDate(h.last_run_at)}</td>
                  <td>
                    {h.last_status ? <Badge tone={runTone(h.last_status)}>{h.last_status}</Badge> : <span className="muted">never run</span>}
                    {h.last_error && <div className="tiny text-bad break">{h.last_error}</div>}
                  </td>
                  <td>{h.last_counts ? `${h.last_counts.seen} / ${h.last_counts.new} / ${h.last_counts.expired}` : "—"}</td>
                  <td>{h.consecutive_failures}</td>
                  <td className="nowrap">{fmtDate(h.next_attempt_at)}</td>
                  <td className="nowrap">{fmtDate(h.last_success_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Section>
  );
}

function PipelineSection() {
  const { canWrite, controls } = useApp();
  const poll = useAction();
  const match = useAction();
  const [runs, setRuns] = useState<PollRun[] | null>(null);
  const [counts, setCounts] = useState<MatchCounts | null>(null);
  if (!canWrite) return null;
  return (
    <Section title="Pipeline">
      <p className="small muted">
        The worker polls and re-matches on its own schedule. Use these to run a step now. Poll fetches enabled boards; Re-match
        re-scores discovered jobs against your approved facts and preferences.
      </p>
      {controls?.paused && <Notice tone="warn">The system is paused; polling may be skipped until you resume.</Notice>}
      <div className="btn-row">
        <button className="btn btn-primary" disabled={poll.busy}
          onClick={async () => { const r = await poll.run(() => api.pollNow(), (x) => `Poll finished: ${x.length} board run(s).`); if (r) setRuns(r); }}>
          {poll.busy ? "Polling…" : "Poll now"}
        </button>
        <button className="btn" disabled={match.busy}
          onClick={async () => { const r = await match.run(() => api.matchNow(), "Re-match finished."); if (r) setCounts(r); }}>
          {match.busy ? "Matching…" : "Re-match"}
        </button>
      </div>
      <ActionFeedback action={poll} />
      <ActionFeedback action={match} />
      {counts && (
        <div className="kv-inline">
          {Object.entries(counts).map(([k, v]) => <span key={k} className="stat"><span className="stat-v">{String(v)}</span> <span className="stat-k">{k}</span></span>)}
        </div>
      )}
      {runs && (runs.length === 0 ? <Empty>No boards were due or enabled.</Empty> : (
        <div className="table-wrap">
          <table className="table">
            <thead><tr><th>Source / board</th><th>Status</th><th>Seen</th><th>New</th><th>Expired</th><th>Error</th></tr></thead>
            <tbody>
              {runs.map((r, i) => (
                <tr key={`${r.source}-${r.board}-${i}`}>
                  <td>{r.source} / <code>{r.board}</code></td>
                  <td><Badge tone={runTone(r.status)}>{r.status}</Badge></td>
                  <td>{r.seen}</td><td>{r.new}</td><td>{r.expired}</td>
                  <td className="tiny text-bad break">{r.error ?? ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}
    </Section>
  );
}
