import { useEffect, useState, type FormEvent } from "react";
import { api, EMPLOYMENT_TYPES, WEIGHT_KEYS, WORK_ARRANGEMENTS, type Preferences, type PreferencesIn } from "../api";
import { useApp } from "../components/context";
import { useAction, useAsync } from "../components/useAsync";
import { ActionFeedback, ErrorBox, Field, ListInput, Loading, ReadOnlyNote, Section, fmtDate } from "../components/ui";

const FALLBACK_SENIORITY = ["intern", "junior", "mid", "senior", "staff", "principal", "director", "vp"];
const DEFAULT_WEIGHTS: Record<string, number> = { title: 25, skills: 25, seniority: 15, location: 15, domain: 10, compensation: 10 };

function toIn(p: Preferences): PreferencesIn {
  return {
    titles: p.titles ?? [], keywords: p.keywords ?? [], seniority: p.seniority ?? [], geographies: p.geographies ?? [],
    work_arrangements: p.work_arrangements ?? [], employment_types: p.employment_types ?? [],
    salary_floor: p.salary_floor ?? null, salary_currency: p.salary_currency || "USD", industries: p.industries ?? [],
    excluded_employers: p.excluded_employers ?? [], excluded_terms: p.excluded_terms ?? [], min_score: p.min_score ?? 70,
    polling_minutes: p.polling_minutes ?? 60, daily_application_limit: p.daily_application_limit ?? 10,
    weights: { ...DEFAULT_WEIGHTS, ...(p.weights ?? {}) },
  };
}

function toggleIn(list: string[], v: string, on: boolean): string[] {
  return on ? (list.includes(v) ? list : [...list, v]) : list.filter((x) => x !== v);
}

export function PreferencesPage() {
  const { canWrite } = useApp();
  const res = useAsync(() => api.preferences(), []);
  const [form, setForm] = useState<PreferencesIn | null>(null);
  const action = useAction();

  useEffect(() => {
    if (res.data) setForm(toIn(res.data));
  }, [res.data]);

  const levels = res.data?.seniority_levels ?? FALLBACK_SENIORITY;
  const set = <K extends keyof PreferencesIn>(k: K, v: PreferencesIn[K]) => setForm((f) => (f ? { ...f, [k]: v } : f));

  async function save(e: FormEvent) {
    e.preventDefault();
    if (!form) return;
    const r = await action.run(() => api.savePreferences(form), "Preferences saved. Use “Re-match” on the Sources tab to re-score existing jobs now.");
    if (r) res.setData({ ...r, seniority_levels: res.data?.seniority_levels });
  }

  const weightTotal = form ? WEIGHT_KEYS.reduce((s, k) => s + (form.weights[k] ?? 0), 0) : 0;

  return (
    <div className="page">
      <h1 className="page-title">Preferences</h1>
      {!canWrite && <ReadOnlyNote />}
      <ErrorBox error={res.error} />
      {!form ? (res.loading ? <Loading /> : null) : (
        <form onSubmit={save}>
          <fieldset disabled={!canWrite} className="stack">
            <Section title="What you are looking for">
              <div className="grid-form">
                <Field label="Target titles" hint="Comma separated" wide>
                  <ListInput value={form.titles} onChange={(v) => set("titles", v)} placeholder="Senior Backend Engineer, Platform Engineer" disabled={!canWrite} />
                </Field>
                <Field label="Keywords / skills" hint="Extra skills to recognise in listings, comma separated" wide>
                  <ListInput value={form.keywords} onChange={(v) => set("keywords", v)} placeholder="python, fastapi, postgresql" disabled={!canWrite} />
                </Field>
                <Field label="Geographies" hint="Matched against the listing location" wide>
                  <ListInput value={form.geographies} onChange={(v) => set("geographies", v)} placeholder="Toronto, Canada, Remote - North America" disabled={!canWrite} />
                </Field>
                <Field label="Industries" wide>
                  <ListInput value={form.industries} onChange={(v) => set("industries", v)} placeholder="fintech, healthcare" disabled={!canWrite} />
                </Field>
              </div>
              <div className="check-groups">
                <fieldset className="check-group">
                  <legend>Seniority</legend>
                  {levels.map((l) => (
                    <label key={l} className="checkbox">
                      <input type="checkbox" checked={form.seniority.includes(l)} onChange={(e) => set("seniority", toggleIn(form.seniority, l, e.target.checked))} />
                      <span>{l}</span>
                    </label>
                  ))}
                </fieldset>
                <fieldset className="check-group">
                  <legend>Work arrangement</legend>
                  {WORK_ARRANGEMENTS.map((w) => (
                    <label key={w} className="checkbox">
                      <input type="checkbox" checked={form.work_arrangements.includes(w)} onChange={(e) => set("work_arrangements", toggleIn(form.work_arrangements, w, e.target.checked))} />
                      <span>{w}</span>
                    </label>
                  ))}
                </fieldset>
                <fieldset className="check-group">
                  <legend>Employment type</legend>
                  {EMPLOYMENT_TYPES.map((t) => (
                    <label key={t} className="checkbox">
                      <input type="checkbox" checked={form.employment_types.includes(t)} onChange={(e) => set("employment_types", toggleIn(form.employment_types, t, e.target.checked))} />
                      <span>{t.replace(/_/g, " ")}</span>
                    </label>
                  ))}
                </fieldset>
              </div>
            </Section>

            <Section title="Hard exclusions">
              <p className="small muted">A listing matching any exclusion is blocked regardless of its score.</p>
              <div className="grid-form">
                <Field label="Excluded employers" hint="Substring match on employer name" wide>
                  <ListInput value={form.excluded_employers} onChange={(v) => set("excluded_employers", v)} placeholder="Current Employer Inc" disabled={!canWrite} />
                </Field>
                <Field label="Excluded terms" hint="Whole-word match in title or description" wide>
                  <ListInput value={form.excluded_terms} onChange={(v) => set("excluded_terms", v)} placeholder="clearance, commission only" disabled={!canWrite} />
                </Field>
                <Field label="Salary floor" hint="Listings whose stated max is below this are excluded (same currency only)">
                  <input type="number" min={0} step={1000} value={form.salary_floor ?? ""} onChange={(e) => set("salary_floor", e.target.value === "" ? null : Math.max(0, Math.floor(Number(e.target.value))))} />
                </Field>
                <Field label="Currency" hint="3-letter code">
                  <input value={form.salary_currency} maxLength={3} minLength={3} required onChange={(e) => set("salary_currency", e.target.value.toUpperCase().replace(/[^A-Z]/g, ""))} />
                </Field>
              </div>
            </Section>

            <Section title="Thresholds & limits">
              <div className="grid-form">
                <Field label="Minimum score (0–100)" hint="Jobs at or above this become MATCHED">
                  <input type="number" min={0} max={100} required value={form.min_score} onChange={(e) => set("min_score", Number(e.target.value))} />
                </Field>
                <Field label="Polling interval (minutes)" hint="15 – 1440">
                  <input type="number" min={15} max={1440} required value={form.polling_minutes} onChange={(e) => set("polling_minutes", Number(e.target.value))} />
                </Field>
                <Field label="Daily application limit" hint="0 – 100, applies to automated submissions">
                  <input type="number" min={0} max={100} required value={form.daily_application_limit} onChange={(e) => set("daily_application_limit", Number(e.target.value))} />
                </Field>
              </div>
            </Section>

            <Section title="Score weights">
              <p className="small muted">Each weight is 0–100. The score is normalised by the total, so only the proportions matter (total now: {weightTotal}).</p>
              <div className="weights">
                {WEIGHT_KEYS.map((k) => {
                  const v = form.weights[k] ?? 0;
                  return (
                    <div className="weight" key={k}>
                      <label htmlFor={`w-${k}`}>{k}</label>
                      <input id={`w-${k}`} type="range" min={0} max={100} value={v} onChange={(e) => set("weights", { ...form.weights, [k]: Number(e.target.value) })} />
                      <input type="number" min={0} max={100} value={v} className="weight-num" aria-label={`${k} weight`}
                        onChange={(e) => set("weights", { ...form.weights, [k]: Math.max(0, Math.min(100, Number(e.target.value) || 0)) })} />
                      <span className="tiny muted weight-pct">{weightTotal ? Math.round((100 * v) / weightTotal) : 0}%</span>
                    </div>
                  );
                })}
              </div>
              {canWrite && <button type="button" className="btn btn-small" onClick={() => set("weights", { ...DEFAULT_WEIGHTS })}>Reset to defaults</button>}
            </Section>
          </fieldset>
          <ActionFeedback action={action} />
          {canWrite && (
            <div className="form-actions sticky-actions">
              <button className="btn btn-primary" type="submit" disabled={action.busy}>{action.busy ? "Saving…" : "Save preferences"}</button>
              <button className="btn" type="button" onClick={() => res.data && setForm(toIn(res.data))}>Discard changes</button>
              {res.data?.updated_at && <span className="tiny muted">Last saved {fmtDate(res.data.updated_at)}</span>}
            </div>
          )}
        </form>
      )}
    </div>
  );
}
