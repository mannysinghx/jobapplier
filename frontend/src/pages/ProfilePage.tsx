import { useEffect, useRef, useState, type FormEvent } from "react";
import { api, type Profile, type ProfileIn } from "../api";
import { useApp } from "../components/context";
import { useAction, useAsync } from "../components/useAsync";
import {
  ActionFeedback, Badge, Empty, ErrorBox, Field, Loading, Notice, ReadOnlyNote, Section, fmtBytes, fmtDate,
} from "../components/ui";

/* Folder imports must refresh the documents list in a sibling section; a window event keeps the sections independent. */
const DOCS_EVENT = "ja:documents-changed";

const EMPTY: ProfileIn = {
  first_name: "", last_name: "", email: "", phone: null, location: null,
  timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC", linkedin_url: null, portfolio_links: [],
};

function toForm(p: Profile | null | undefined): ProfileIn {
  if (!p) return EMPTY;
  return {
    first_name: p.first_name, last_name: p.last_name, email: p.email, phone: p.phone, location: p.location,
    timezone: p.timezone, linkedin_url: p.linkedin_url, portfolio_links: p.portfolio_links ?? [],
  };
}

export function ProfilePage() {
  const { canWrite } = useApp();
  const profile = useAsync(() => api.profile(), []);
  const p = profile.data;

  return (
    <div className="page">
      <h1 className="page-title">Setup / Profile</h1>
      {!canWrite && <ReadOnlyNote />}
      <ErrorBox error={profile.error} />
      {profile.loading && p === undefined ? (
        <Loading />
      ) : (
        <>
          {p === null && (
            <Notice tone="warn">No profile yet. Fill in the form below and save; everything else (documents, facts, preferences) needs a profile first.</Notice>
          )}
          <ProfileForm profile={p ?? null} onSaved={(np) => profile.setData(np)} />
          {p && <FolderSection profile={p} onChanged={(np) => profile.setData(np)} />}
          {p && <UploadsAndDocuments />}
        </>
      )}
    </div>
  );
}

function ProfileForm({ profile, onSaved }: { profile: Profile | null; onSaved: (p: Profile) => void }) {
  const { canWrite } = useApp();
  const [form, setForm] = useState<ProfileIn>(() => toForm(profile));
  const [links, setLinks] = useState(() => (profile?.portfolio_links ?? []).join("\n"));
  const action = useAction();

  useEffect(() => {
    setForm(toForm(profile));
    setLinks((profile?.portfolio_links ?? []).join("\n"));
  }, [profile]);

  const set = <K extends keyof ProfileIn>(k: K, v: ProfileIn[K]) => setForm((f) => ({ ...f, [k]: v }));
  const nullable = (s: string) => (s.trim() ? s.trim() : null);

  async function save(e: FormEvent) {
    e.preventDefault();
    const body: ProfileIn = {
      ...form,
      first_name: form.first_name.trim(),
      last_name: form.last_name.trim(),
      email: form.email.trim(),
      phone: nullable(form.phone ?? ""),
      location: nullable(form.location ?? ""),
      timezone: form.timezone.trim() || "UTC",
      linkedin_url: nullable(form.linkedin_url ?? ""),
      portfolio_links: links.split(/\n|,/).map((s) => s.trim()).filter(Boolean),
    };
    const r = await action.run(() => api.saveProfile(body), "Profile saved.");
    if (r) onSaved(r);
  }

  const ro = !canWrite;
  return (
    <Section title="Profile">
      <p className="muted small">These fields answer identity questions on applications (name, email, phone, LinkedIn, website, location). Nothing here is inferred.</p>
      <form onSubmit={save}>
        <fieldset disabled={ro} className="grid-form">
          <Field label="First name *">
            <input value={form.first_name} onChange={(e) => set("first_name", e.target.value)} required maxLength={100} autoComplete="given-name" />
          </Field>
          <Field label="Last name *">
            <input value={form.last_name} onChange={(e) => set("last_name", e.target.value)} required maxLength={100} autoComplete="family-name" />
          </Field>
          <Field label="Email *">
            <input type="email" value={form.email} onChange={(e) => set("email", e.target.value)} required autoComplete="email" />
          </Field>
          <Field label="Phone">
            <input type="tel" value={form.phone ?? ""} onChange={(e) => set("phone", e.target.value)} maxLength={40} autoComplete="tel" />
          </Field>
          <Field label="Location" hint="e.g. Toronto, ON, Canada">
            <input value={form.location ?? ""} onChange={(e) => set("location", e.target.value)} maxLength={200} />
          </Field>
          <Field label="Timezone" hint="IANA name, e.g. America/Toronto">
            <input value={form.timezone} onChange={(e) => set("timezone", e.target.value)} maxLength={64} list="tz-list" />
          </Field>
          <Field label="LinkedIn profile URL" hint="Only the URL is stored. No LinkedIn login or scraping." wide>
            <input type="url" value={form.linkedin_url ?? ""} onChange={(e) => set("linkedin_url", e.target.value)} placeholder="https://www.linkedin.com/in/your-name" />
          </Field>
          <Field label="Portfolio links" hint="One URL per line (max 10). The first one answers “website/portfolio” questions." wide>
            <textarea rows={3} value={links} onChange={(e) => setLinks(e.target.value)} placeholder="https://github.com/you" />
          </Field>
        </fieldset>
        <datalist id="tz-list">
          {["UTC", "America/New_York", "America/Chicago", "America/Denver", "America/Los_Angeles", "America/Toronto", "America/Vancouver",
            "Europe/London", "Europe/Berlin", "Europe/Paris", "Asia/Kolkata", "Asia/Singapore", "Australia/Sydney"].map((z) => <option key={z} value={z} />)}
        </datalist>
        <ActionFeedback action={action} />
        {canWrite && (
          <div className="form-actions">
            <button className="btn btn-primary" type="submit" disabled={action.busy}>{action.busy ? "Saving…" : profile ? "Save profile" : "Create profile"}</button>
          </div>
        )}
      </form>
    </Section>
  );
}

function FolderSection({ profile, onChanged }: { profile: Profile; onChanged: (p: Profile) => void }) {
  const { canWrite } = useApp();
  const [folder, setFolder] = useState(profile.resume_folder ?? "");
  const [consent, setConsent] = useState(false);
  const action = useAction();
  const consented = Boolean(profile.resume_folder && profile.folder_consent_at);
  const files = useAsync(() => (consented ? api.folderFiles() : Promise.resolve([])), [consented, profile.resume_folder]);
  const importAction = useAction();

  async function grant(e: FormEvent) {
    e.preventDefault();
    const r = await action.run(() => api.grantFolder(folder.trim(), consent), "Folder consent recorded.");
    if (r) {
      setConsent(false);
      onChanged(r);
    }
  }

  async function revoke() {
    if (!window.confirm("Revoke read access to this folder? Already-imported documents are kept.")) return;
    const r = await action.run(() => api.revokeFolder(), "Folder consent revoked.");
    if (r) onChanged(r);
  }

  return (
    <Section title="Resume folder (optional, read-only)">
      <p className="muted small">
        Grant read-only access to one local folder. Only top-level PDF and DOCX files are listed, nothing is written, and
        nothing is read until you click Import. The path is resolved on the machine running the API (inside the container when using Docker).
      </p>
      {consented ? (
        <div className="kv">
          <div><span className="k">Folder</span><code className="break">{profile.resume_folder}</code></div>
          <div><span className="k">Consent given</span>{fmtDate(profile.folder_consent_at)}</div>
          {canWrite && <div><button className="btn btn-danger-outline btn-small" onClick={revoke} disabled={action.busy}>Revoke consent</button></div>}
        </div>
      ) : (
        <form onSubmit={grant}>
          <fieldset disabled={!canWrite} className="stack">
            <Field label="Absolute folder path">
              <input value={folder} onChange={(e) => setFolder(e.target.value)} placeholder="/home/you/Documents/resumes" required maxLength={1000} />
            </Field>
            <label className="checkbox">
              <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} />
              <span>I consent to read-only access to PDF/DOCX files in this folder</span>
            </label>
            <div>
              <button className="btn btn-primary" type="submit" disabled={!consent || !folder.trim() || action.busy}>Grant access</button>
            </div>
          </fieldset>
        </form>
      )}
      <ActionFeedback action={action} />
      {consented && (
        <div className="subsection">
          <div className="row-between">
            <h3>Files in folder</h3>
            <button className="btn btn-small" onClick={files.reload} disabled={files.loading}>Refresh</button>
          </div>
          <ErrorBox error={files.error} />
          <ActionFeedback action={importAction} />
          {files.loading ? <Loading /> : files.data && files.data.length === 0 ? (
            <Empty>No PDF or DOCX files at the top level of this folder.</Empty>
          ) : (
            <div className="table-wrap">
              <table className="table">
                <thead><tr><th>File</th><th>Size</th><th>Modified</th><th /></tr></thead>
                <tbody>
                  {(files.data ?? []).map((f) => (
                    <tr key={f.name}>
                      <td className="break">{f.name}</td>
                      <td>{fmtBytes(f.size)}</td>
                      <td>{fmtDate(f.modified)}</td>
                      <td className="right">
                        {canWrite && (
                          <button
                            className="btn btn-small"
                            disabled={importAction.busy}
                            onClick={async () => {
                              const d = await importAction.run(() => api.importFromFolder(f.name),
                                (r) => `Imported ${r.filename} (v${r.version}, parse: ${r.parse_status}). Review the extracted facts in the Facts tab.`);
                              if (d) window.dispatchEvent(new Event(DOCS_EVENT));
                            }}
                          >
                            Import
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </Section>
  );
}


function UploadsAndDocuments() {
  const { canWrite } = useApp();
  const docs = useAsync(() => api.documents(), []);
  const resumeAction = useAction();
  const liAction = useAction();
  const resumeRef = useRef<HTMLInputElement>(null);
  const liRef = useRef<HTMLInputElement>(null);
  const reload = docs.reload;

  useEffect(() => {
    window.addEventListener(DOCS_EVENT, reload);
    return () => window.removeEventListener(DOCS_EVENT, reload);
  }, [reload]);

  async function uploadResume(e: FormEvent) {
    e.preventDefault();
    const f = resumeRef.current?.files?.[0];
    if (!f) return;
    const r = await resumeAction.run(() => api.uploadResume(f),
      (d) => `Uploaded ${d.filename} (v${d.version}, parse: ${d.parse_status}${d.parse_error ? ` — ${d.parse_error}` : ""}). Review the extracted facts in the Facts tab.`);
    if (r) {
      if (resumeRef.current) resumeRef.current.value = "";
      docs.reload();
    }
  }

  async function uploadLinkedIn(e: FormEvent) {
    e.preventDefault();
    const f = liRef.current?.files?.[0];
    if (!f) return;
    const r = await liAction.run(() => api.uploadLinkedIn(f),
      (x) => x.duplicate
        ? "This export was already imported (identical file). Nothing changed."
        : `Imported: ${x.facts_created} candidate facts, ${x.conflicts} conflicts with existing facts. Review them in the Facts tab.`);
    if (r) {
      if (liRef.current) liRef.current.value = "";
      docs.reload();
    }
  }

  return (
    <>
      {canWrite && (
        <div className="two-col">
          <Section title="Upload a resume">
            <form onSubmit={uploadResume} className="stack">
              <p className="muted small">PDF or DOCX, up to 10 MB. The file is type-checked, parsed in an isolated process and stored encrypted. Extracted facts start as PENDING until you approve them.</p>
              <input ref={resumeRef} type="file" accept=".pdf,.docx,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document" required />
              <div><button className="btn btn-primary" type="submit" disabled={resumeAction.busy}>{resumeAction.busy ? "Uploading…" : "Upload resume"}</button></div>
              <ActionFeedback action={resumeAction} />
            </form>
          </Section>
          <Section title="Import LinkedIn data export (optional)">
            <form onSubmit={uploadLinkedIn} className="stack">
              <p className="muted small">
                This is <strong>your own</strong> LinkedIn data export: on LinkedIn go to Settings &amp; Privacy → Data privacy →
                “Get a copy of your data”, download the ZIP LinkedIn emails you, and upload it here (up to 50 MB).
                jobApplier never asks for your LinkedIn password and never logs in to or scrapes LinkedIn.
                Differences from your resume show up as conflicts for you to resolve.
              </p>
              <input ref={liRef} type="file" accept=".zip,application/zip" required />
              <div><button className="btn btn-primary" type="submit" disabled={liAction.busy}>{liAction.busy ? "Importing…" : "Import export ZIP"}</button></div>
              <ActionFeedback action={liAction} />
            </form>
          </Section>
        </div>
      )}
      <Section title="Documents" actions={<button className="btn btn-small" onClick={docs.reload} disabled={docs.loading}>Refresh</button>}>
        <ErrorBox error={docs.error} />
        {docs.loading && !docs.data ? <Loading /> : !docs.data || docs.data.length === 0 ? (
          <Empty>No documents yet.</Empty>
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>ID</th><th>File</th><th>Kind</th><th>Version</th><th>Size</th><th>Parse status</th><th>Added</th></tr></thead>
              <tbody>
                {docs.data.map((d) => (
                  <tr key={d.id}>
                    <td>{d.id}</td>
                    <td className="break">{d.filename}<div className="mono tiny muted" title={d.sha256}>sha256 {d.sha256.slice(0, 12)}…</div></td>
                    <td>{d.kind.replace(/_/g, " ")}</td>
                    <td>v{d.version}</td>
                    <td>{fmtBytes(d.size_bytes)}</td>
                    <td>
                      <Badge tone={d.parse_status === "parsed" ? "ok" : d.parse_status === "pending" ? "warn" : "bad"}>{d.parse_status}</Badge>
                      {d.parse_error ? <div className="small text-bad">{d.parse_error}</div> : null}
                    </td>
                    <td>{fmtDate(d.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>
    </>
  );
}
