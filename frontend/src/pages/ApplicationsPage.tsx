import { useState } from "react";
import { api, APP_STATES, type AppState } from "../api";
import { useAsync } from "../components/useAsync";
import { Badge, Empty, ErrorBox, Loading, StateBadge } from "../components/ui";

// Remembered while the app is open so returning from a detail page keeps the filters.
let rememberedStates: AppState[] = [];
let rememberedMin = "";

export function scoreTone(score: number | null): "ok" | "warn" | "muted" | "bad" {
  if (score === null || score === undefined) return "muted";
  if (score >= 75) return "ok";
  if (score >= 50) return "warn";
  return "bad";
}

export function ApplicationsPage() {
  const [states, setStatesRaw] = useState<AppState[]>(rememberedStates);
  const [minScore, setMinScoreRaw] = useState(rememberedMin);
  const setStates = (v: AppState[]) => { rememberedStates = v; setStatesRaw(v); };
  const setMinScore = (v: string) => { rememberedMin = v; setMinScoreRaw(v); };
  const min = minScore.trim() === "" || Number.isNaN(Number(minScore)) ? null : Number(minScore);
  const apps = useAsync(() => api.applications(states, min), [states.join(","), min]);

  const toggle = (s: AppState) => setStates(states.includes(s) ? states.filter((x) => x !== s) : [...states, s]);

  return (
    <div className="page">
      <h1 className="page-title">Jobs &amp; applications</h1>
      <div className="card">
        <div className="filter-chips" role="group" aria-label="Filter by state">
          <button className={states.length === 0 ? "fchip active" : "fchip"} onClick={() => setStates([])}>All states</button>
          {APP_STATES.map((s) => (
            <button key={s} className={states.includes(s) ? "fchip active" : "fchip"} onClick={() => toggle(s)} aria-pressed={states.includes(s)}>
              {s.replace(/_/g, " ").toLowerCase()}
            </button>
          ))}
        </div>
        <div className="toolbar">
          <label className="inline-field">
            <span className="small">Min score</span>
            <input type="number" min={0} max={100} value={minScore} onChange={(e) => setMinScore(e.target.value)} className="num-sm" placeholder="0" />
          </label>
          <span className="small muted">{apps.data ? `${apps.data.length} shown (max 200, highest score first)` : ""}</span>
          <button className="btn btn-small" onClick={apps.reload} disabled={apps.loading}>Refresh</button>
        </div>
      </div>
      <ErrorBox error={apps.error} />
      {apps.loading && !apps.data ? <Loading /> : !apps.data?.length ? (
        <Empty>No applications match. Add company boards and run “Poll now” and “Re-match” on the <a href="#sources">Sources</a> tab.</Empty>
      ) : (
        <div className="app-list">
          <div className="app-row app-head" aria-hidden="true">
            <span>Score</span><span>Role</span><span>Location</span><span>Source</span><span>State</span><span>Top unmet</span>
          </div>
          {apps.data.map((a) => (
            <a key={a.id} className="app-row" href={`#jobs/${a.id}`}>
              <span className="app-score"><Badge tone={scoreTone(a.score)}>{a.score === null ? "—" : a.score.toFixed(0)}</Badge></span>
              <span className="app-role">
                <span className="app-title">{a.job.title}</span>
                <span className="app-emp">{a.job.employer}</span>
              </span>
              <span className="app-loc small">
                {a.job.location || "—"}
                {a.job.work_arrangement && a.job.work_arrangement !== "unknown" ? <span className="muted"> · {a.job.work_arrangement}</span> : null}
              </span>
              <span className="app-src small muted">{a.job.source}</span>
              <span className="app-state"><StateBadge state={a.state} /></span>
              <span className="app-unmet tiny">
                {a.exclusions.length > 0 && <span className="text-bad">excluded: {a.exclusions[0]}{a.exclusions.length > 1 ? ` (+${a.exclusions.length - 1})` : ""}</span>}
                {a.exclusions.length === 0 && a.unmet.slice(0, 2).map((u, i) => <span key={i} className="unmet-item">{u}</span>)}
                {a.exclusions.length === 0 && a.unmet.length > 2 && <span className="muted">+{a.unmet.length - 2} more</span>}
              </span>
            </a>
          ))}
        </div>
      )}
    </div>
  );
}
