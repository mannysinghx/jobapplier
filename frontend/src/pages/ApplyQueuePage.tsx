import { api } from "../api";
import { openAssistant } from "../components/assist";
import { useApp } from "../components/context";
import { useAsync } from "../components/useAsync";
import { Badge, Empty, ErrorBox, Loading, Notice, SiteBadge, StateBadge } from "../components/ui";
import { scoreTone } from "./ApplicationsPage";

export function ApplyQueuePage() {
  const { navigate } = useApp();
  const queue = useAsync(() => api.assistQueue(), []);
  const ready = (queue.data ?? []).filter((q) => q.ready).length;

  return (
    <div className="page">
      <h1 className="page-title">Apply</h1>
      <Notice tone="info">
        <strong>How this works:</strong> open the job site in your normal browser and keep the Apply Assistant panel beside it. Copy
        each prepared value from the panel, paste it into the site’s form, submit on the site yourself, then record the submission in
        the panel. <strong>Nothing is automated on the job site</strong>: the assistant only copies values to your clipboard and records
        what you did.
      </Notice>
      <div className="toolbar">
        <span className="small muted">
          {queue.data ? `${queue.data.length} in queue · ${ready} ready (approved) · others need approval first` : ""}
        </span>
        <button className="btn btn-small" onClick={queue.reload} disabled={queue.loading}>Refresh</button>
      </div>
      <ErrorBox error={queue.error} />
      {queue.loading && !queue.data ? <Loading /> : !queue.data?.length ? (
        <Empty>Nothing to apply to yet. Prepare and approve matched jobs on the <a href="#jobs">Jobs</a> tab.</Empty>
      ) : (
        <div className="apply-queue">
          {queue.data.map((q) => (
            <div className="apply-row" key={q.application_id}>
              <span className="apply-score"><Badge tone={scoreTone(q.score)}>{q.score === null ? "—" : q.score.toFixed(0)}</Badge></span>
              <span className="apply-main">
                <a href={`#jobs/${q.application_id}`} className="app-title">{q.title}</a>
                <span className="app-emp">{q.employer}</span>
              </span>
              <span className="apply-badges">
                <SiteBadge site={q.site} />
                {q.ready ? <Badge tone="ok">ready</Badge> : <Badge tone="warn">needs approval</Badge>}
                {!q.ready && <StateBadge state={q.state} />}
                {q.open_items > 0 && <Badge tone="warn" title="Questions you answer yourself on the site">{q.open_items} open item(s)</Badge>}
                {q.handoff_open && <Badge tone="info">handoff open</Badge>}
              </span>
              <span className="apply-act">
                <button className="btn btn-primary btn-small" onClick={() => openAssistant(q.application_id, navigate)}>Start</button>
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
