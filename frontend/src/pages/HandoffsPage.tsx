import { useState } from "react";
import { api } from "../api";
import { useApp } from "../components/context";
import { useAction, useAsync } from "../components/useAsync";
import { ActionFeedback, Badge, Empty, ErrorBox, Loading, SafeLink, StateBadge, fmtDate } from "../components/ui";

export function HandoffsPage() {
  const { canWrite, navigate } = useApp();
  const [status, setStatus] = useState<"OPEN" | "DONE" | "DISMISSED">("OPEN");
  const list = useAsync(() => api.handoffs(status), [status]);
  const act = useAction();

  return (
    <div className="page">
      <h1 className="page-title">Handoffs</h1>
      <p className="small muted">
        A handoff is an application that must be finished by you on the employer’s site, with the reasons automation stopped.
        Open the application to download the packet, submit on the site, then use “Record manual submission”.
      </p>
      <div className="toolbar">
        <div className="seg">
          {(["OPEN", "DONE", "DISMISSED"] as const).map((s) => (
            <button key={s} className={status === s ? "seg-btn active" : "seg-btn"} onClick={() => setStatus(s)}>{s.toLowerCase()}</button>
          ))}
        </div>
        <button className="btn btn-small" onClick={list.reload} disabled={list.loading}>Refresh</button>
      </div>
      <ErrorBox error={list.error} />
      <ActionFeedback action={act} />
      {list.loading && !list.data ? <Loading /> : !list.data?.length ? (
        <Empty>{status === "OPEN" ? "No open handoffs." : `No ${status.toLowerCase()} handoffs.`}</Empty>
      ) : (
        <div className="handoff-grid">
          {list.data.map((h) => (
            <div className="card handoff" key={h.id}>
              <div className="row-between">
                <div>
                  <div className="handoff-title">{h.title}</div>
                  <div className="muted">{h.employer}</div>
                </div>
                <StateBadge state={h.state} />
              </div>
              <div className="tiny muted">handoff #{h.id} · {fmtDate(h.created_at)}</div>
              <h3>Why automation stopped</h3>
              {h.reasons.length === 0 ? <p className="small muted">No reasons recorded.</p> : (
                <ul className="plain-list small reasons">{h.reasons.map((r, i) => <li key={i}>{r}</li>)}</ul>
              )}
              <div className="handoff-actions">
                <SafeLink href={h.apply_url}>Open application page ↗</SafeLink>
                <button className="btn btn-small btn-primary" onClick={() => navigate(`#jobs/${h.application_id}`)}>Open application #{h.application_id}</button>
                {canWrite && status === "OPEN" && (
                  <button className="btn btn-small" disabled={act.busy}
                    onClick={async () => {
                      if (!window.confirm("Dismiss this handoff? The application itself is not changed.")) return;
                      if (await act.run(() => api.dismissHandoff(h.id), "Handoff dismissed.")) list.reload();
                    }}>
                    Dismiss
                  </button>
                )}
              </div>
              {status !== "OPEN" && <Badge tone="muted">{status.toLowerCase()}</Badge>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
