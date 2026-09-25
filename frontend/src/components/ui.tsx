import { useEffect, useState, type ReactNode } from "react";
import type { AnswerStatus, Provenance } from "../api";
import type { ActionState } from "./useAsync";

export type Tone = "ok" | "warn" | "bad" | "info" | "muted" | "accent";

export function Badge({ tone = "muted", children, title }: { tone?: Tone; children: ReactNode; title?: string }) {
  return (
    <span className={`badge badge-${tone}`} title={title}>
      {children}
    </span>
  );
}

const STATE_TONES: Record<string, Tone> = {
  DISCOVERED: "muted",
  MATCHED: "info",
  PREPARED: "accent",
  NEEDS_REVIEW: "warn",
  APPROVED: "ok",
  SUBMITTED: "ok",
  CONFIRMED: "ok",
  FOLLOW_UP: "info",
  REJECTED_BY_USER: "muted",
  BLOCKED_BY_POLICY: "bad",
  EXPIRED: "muted",
  DUPLICATE: "muted",
  FAILED: "bad",
};

export function StateBadge({ state }: { state: string }) {
  return <Badge tone={STATE_TONES[state] ?? "muted"}>{state.replace(/_/g, " ")}</Badge>;
}

const ANSWER_TONES: Record<AnswerStatus, Tone> = {
  SUPPORTED: "ok",
  USER_APPROVED: "ok",
  NEEDS_REVIEW: "warn",
  SENSITIVE_MISSING: "bad",
  ATTESTATION: "bad",
  ON_SITE: "info",
};

export function AnswerStatusBadge({ status }: { status: string }) {
  return <Badge tone={ANSWER_TONES[status as AnswerStatus] ?? "muted"}>{status.replace(/_/g, " ")}</Badge>;
}

export function FactStatusBadge({ status }: { status: string }) {
  const tone: Tone = status === "APPROVED" ? "ok" : status === "REJECTED" ? "bad" : "warn";
  return <Badge tone={tone}>{status}</Badge>;
}

export function Check({ ok, label }: { ok: boolean; label?: string }) {
  return (
    <span className={ok ? "check check-ok" : "check check-bad"} aria-label={ok ? "passes" : "fails"}>
      {ok ? "✓" : "✗"}
      {label ? ` ${label}` : ""}
    </span>
  );
}

export function YesNo({ value }: { value: boolean }) {
  return <Badge tone={value ? "ok" : "muted"}>{value ? "yes" : "no"}</Badge>;
}

export function ErrorBox({ error }: { error: string | null | undefined }) {
  if (!error) return null;
  const needsProfile = /create your profile first/i.test(error);
  return (
    <div className="alert alert-bad" role="alert">
      {error}
      {needsProfile ? (
        <>
          {" "}
          — <a href="#profile">go to Setup / Profile</a>
        </>
      ) : null}
    </div>
  );
}

export function Notice({ tone = "info", children }: { tone?: "info" | "warn" | "ok" | "bad"; children: ReactNode }) {
  return <div className={`alert alert-${tone}`}>{children}</div>;
}

export function ActionFeedback({ action }: { action: ActionState }) {
  if (action.error) return <ErrorBox error={action.error} />;
  if (action.message) return <div className="alert alert-ok">{action.message}</div>;
  return null;
}

export function Loading({ what = "Loading" }: { what?: string }) {
  return <div className="loading">{what}…</div>;
}

export function Section({ title, actions, children, id }: { title: ReactNode; actions?: ReactNode; children: ReactNode; id?: string }) {
  return (
    <section className="card" id={id}>
      <div className="card-head">
        <h2>{title}</h2>
        {actions ? <div className="card-actions">{actions}</div> : null}
      </div>
      {children}
    </section>
  );
}

export function Field({ label, hint, children, wide }: { label: string; hint?: ReactNode; children: ReactNode; wide?: boolean }) {
  return (
    <label className={wide ? "field field-wide" : "field"}>
      <span className="field-label">{label}</span>
      {children}
      {hint ? <span className="field-hint">{hint}</span> : null}
    </label>
  );
}

/** Only https:// URLs become links, always with rel="noopener noreferrer" target="_blank". Anything else is shown as text. */
export function SafeLink({ href, children }: { href: string | null | undefined; children?: ReactNode }) {
  if (!href) return <span className="muted">—</span>;
  if (!href.startsWith("https://")) {
    return (
      <span className="muted" title="Not an https:// URL, so it is not rendered as a link">
        {children ?? href} (not https — link disabled)
      </span>
    );
  }
  return (
    <a href={href} rel="noopener noreferrer" target="_blank">
      {children ?? href}
    </a>
  );
}

/** Untrusted text (job descriptions etc.): rendered as plain text only, never as HTML. */
export function UntrustedText({ text, label = "Untrusted text from the job source, shown as plain text" }: { text: string | null | undefined; label?: string }) {
  return (
    <pre className="untrusted" aria-label={label}>
      {text && text.trim() ? text : "(empty)"}
    </pre>
  );
}

export function fmtDate(v: string | number | null | undefined): string {
  if (v === null || v === undefined || v === "") return "—";
  const d = typeof v === "number" ? new Date(v * 1000) : new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(v) ? v : `${v}Z`);
  if (Number.isNaN(d.getTime())) return String(v);
  return d.toLocaleString(undefined, { year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

export function splitList(s: string): string[] {
  return s
    .split(",")
    .map((x) => x.trim())
    .filter(Boolean);
}

/** Comma-separated list input with chip preview. */
export function ListInput({ value, onChange, placeholder, disabled }: {
  value: string[];
  onChange: (v: string[]) => void;
  placeholder?: string;
  disabled?: boolean;
}) {
  const [raw, setRaw] = useState(value.join(", "));
  // Re-sync when the parent replaces the value (e.g. after a reload), without clobbering in-progress typing.
  useEffect(() => {
    if (splitList(raw).join("\u0000") !== value.join("\u0000")) setRaw(value.join(", "));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value]);
  return (
    <div className="list-input">
      <input
        type="text"
        value={raw}
        disabled={disabled}
        placeholder={placeholder ?? "comma, separated, values"}
        onChange={(e) => {
          setRaw(e.target.value);
          onChange(splitList(e.target.value));
        }}
      />
      {value.length > 0 && (
        <div className="chips">
          {value.map((v, i) => (
            <span className="chip" key={`${v}-${i}`}>
              {v}
              {!disabled && (
                <button
                  type="button"
                  className="chip-x"
                  aria-label={`remove ${v}`}
                  onClick={() => {
                    const next = value.filter((_, j) => j !== i);
                    onChange(next);
                    setRaw(next.join(", "));
                  }}
                >
                  ×
                </button>
              )}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

/** Provenance chips: every generated line must cite approved facts, profile fields, job fields or fixed template text. */
export function ProvenanceChips({ p }: { p: Provenance }) {
  const chips: ReactNode[] = [];
  for (const id of p.fact_ids ?? []) chips.push(<span key={`f${id}`} className="chip chip-fact">fact #{id}</span>);
  for (const f of p.profile_fields ?? []) chips.push(<span key={`p${f}`} className="chip chip-profile">profile: {f}</span>);
  for (const f of p.job_fields ?? []) chips.push(<span key={`j${f}`} className="chip chip-job">job: {f}</span>);
  if (p.template) chips.push(<span key="t" className="chip chip-template">template</span>);
  if (chips.length === 0) chips.push(<span key="none" className="chip chip-bad">no provenance</span>);
  return <span className="chips inline">{chips}</span>;
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

export function ReadOnlyNote() {
  return <Notice tone="info">You are signed in as a viewer. Changes are disabled.</Notice>;
}
