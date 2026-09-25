import { useState, type FormEvent } from "react";
import { api, errorMessage, type MatchCounts, type PollRun, type Source, type SourcePatch } from "../api";
import { useApp } from "../components/context";
import { useAction, useAsync } from "../components/useAsync";
import { ActionFeedback, Badge, Empty, ErrorBox, Field, Loading, Notice, ReadOnlyNote, SafeLink, Section, YesNo, fmtDate, type Tone } from "../components/ui";

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

export function SourcesPage() {
  const { canWrite } = useApp();
  const sources = useAsync(() => api.sources(), []);
  const anyAutoSubmit = (sources.data ?? []).some((s) => autoSubmitBlockers(s).length === 0);

  return (
    <div className="page">
      <h1 className="page-title">Sources</h1>
      {!canWrite && <ReadOnlyNote />}
      <PipelineSection />
      <Section title="Job sources" actions={<button className="btn btn-small" onClick={sources.reload} disabled={sources.loading}>Refresh</button>}>
        {sources.data && !anyAutoSubmit && (
          <Notice tone="info">
            <strong>Auto-submit is unavailable for every source today.</strong> No source both permits automated submission and has a
            verified submission integration. This is expected: every approved application becomes a <a href="#handoffs">handoff</a> with
            a ready packet, and you submit it yourself on the employer’s site. Reading (discovery) works for enabled sources.
          </Notice>
        )}
        <ErrorBox error={sources.error} />
        {sources.loading && !sources.data ? <Loading /> : !sources.data?.length ? <Empty>No sources in the registry.</Empty> : (
          <div className="source-grid">
            {sources.data.map((s) => (
              <SourceCard key={s.key} s={s} onChange={(ns) => sources.setData(sources.data?.map((x) => (x.key === ns.key ? ns : x)))} />
            ))}
          </div>
        )}
      </Section>
      <BoardsSection implemented={(sources.data ?? []).filter((s) => s.implemented)} />
      <HealthSection />
    </div>
  );
}

function SourceCard({ s, onChange }: { s: Source; onChange: (s: Source) => void }) {
  const { canWrite } = useApp();
  const [msg, setMsg] = useState<{ tone: "bad" | "ok"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [limit, setLimit] = useState(String(s.daily_submit_limit));
  const autoBlock = autoSubmitBlockers(s);
  const enBlock = enableBlockers(s);

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
          <div className="tiny muted">{s.method} · {s.rate_limit_per_minute}/min</div>
        </div>
        <div className="badges">
          {s.implemented ? <Badge tone="ok">connector implemented</Badge> : <Badge tone="muted">not implemented</Badge>}
          {s.verified_adapter ? <Badge tone="ok">verified submit adapter</Badge> : <Badge tone="muted">no verified adapter</Badge>}
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
          <div className="tiny"><SafeLink href={s.terms_url}>terms</SafeLink></div>
        </dd>
        {s.notes && (<><dt>Notes</dt><dd className="small">{s.notes}</dd></>)}
      </dl>
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
      {msg && <div className={`alert alert-${msg.tone} small`}>{msg.text}</div>}
    </div>
  );
}

function BoardsSection({ implemented }: { implemented: Source[] }) {
  const { canWrite } = useApp();
  const boards = useAsync(() => api.boards(), []);
  const [sourceKey, setSourceKey] = useState("");
  const [token, setToken] = useState("");
  const [employer, setEmployer] = useState("");
  const add = useAction();
  const rowAction = useAction();
  const effectiveKey = sourceKey || implemented[0]?.key || "";

  async function submit(e: FormEvent) {
    e.preventDefault();
    const r = await add.run(() => api.addBoard(effectiveKey, token.trim(), employer.trim() || null), (b) => `Added ${b.source_key}/${b.board_token}.`);
    if (r) {
      setToken("");
      setEmployer("");
      boards.reload();
    }
  }

  return (
    <Section title="Company boards" actions={<button className="btn btn-small" onClick={boards.reload} disabled={boards.loading}>Refresh</button>}>
      <p className="small muted">
        Board token = the company slug in <code>boards.greenhouse.io/&lt;token&gt;</code>, <code>jobs.lever.co/&lt;token&gt;</code>,{" "}
        <code>jobs.ashbyhq.com/&lt;token&gt;</code>. Only public job-board APIs are read (GET only).
      </p>
      {canWrite && (
        <form onSubmit={submit} className="grid-form board-form">
          <Field label="Source">
            <select value={effectiveKey} onChange={(e) => setSourceKey(e.target.value)} required>
              {implemented.length === 0 && <option value="">(no implemented sources)</option>}
              {implemented.map((s) => <option key={s.key} value={s.key}>{s.name}</option>)}
            </select>
          </Field>
          <Field label="Board token">
            <input value={token} onChange={(e) => setToken(e.target.value)} required maxLength={120} pattern="[A-Za-z0-9_.\-]{1,120}" placeholder="e.g. acme" title="letters, digits, '.', '_' and '-'" />
          </Field>
          <Field label="Employer name (optional)">
            <input value={employer} onChange={(e) => setEmployer(e.target.value)} maxLength={200} placeholder="Acme Corp" />
          </Field>
          <div className="field field-end"><button className="btn btn-primary" type="submit" disabled={add.busy || !effectiveKey}>Add board</button></div>
        </form>
      )}
      <ActionFeedback action={add} />
      <ActionFeedback action={rowAction} />
      <ErrorBox error={boards.error} />
      {boards.loading && !boards.data ? <Loading /> : !boards.data?.length ? <Empty>No boards yet. Add a company above to start discovering jobs.</Empty> : (
        <div className="table-wrap">
          <table className="table">
            <thead><tr><th>Source</th><th>Token</th><th>Employer</th><th>Enabled</th><th>Failures</th><th>Next attempt</th><th>Last success</th>{canWrite && <th />}</tr></thead>
            <tbody>
              {boards.data.map((b) => (
                <tr key={b.id}>
                  <td>{b.source_key}</td>
                  <td><code>{b.board_token}</code></td>
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
                          if (!window.confirm(`Remove board ${b.source_key}/${b.board_token}? Already discovered jobs are kept.`)) return;
                          if ((await rowAction.run(() => api.deleteBoard(b.id).then(() => true), "Board removed.")) === true) boards.reload();
                        }}>Remove</button>
                    </td>
                  )}
                </tr>
              ))}
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
