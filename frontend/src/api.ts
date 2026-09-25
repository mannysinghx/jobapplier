/**
 * Typed client for the jobApplier FastAPI backend. Every path is under /api.
 *
 * Auth: httpOnly session cookie (sent automatically with credentials: "include") plus CSRF double-submit:
 * every non-GET request carries `x-csrf-token` = the `ja_csrf` cookie (also returned by login and /auth/me).
 * Types mirror backend/app/api/{admin,profile,jobs}.py response dicts field-for-field.
 */

// ------------------------------------------------------------------ transport
export class ApiError extends Error {
  status: number;
  detail: unknown;
  constructor(status: number, message: string, detail: unknown) {
    super(message);
    this.status = status;
    this.detail = detail;
  }
}

let csrfToken: string | null = null;
let unauthorizedHandler: (() => void) | null = null;

export function setCsrfToken(tok: string | null): void {
  csrfToken = tok;
}

/** Called on any 401 (except the login request itself) so the app can show the login screen. */
export function setUnauthorizedHandler(fn: (() => void) | null): void {
  unauthorizedHandler = fn;
}

function readCookie(name: string): string | null {
  for (const part of document.cookie.split(";")) {
    const [k, ...rest] = part.trim().split("=");
    if (k === name) return decodeURIComponent(rest.join("="));
  }
  return null;
}

/** FastAPI errors: {detail: string} or, for 422 validation, {detail: [{loc, msg, type}, ...]}. */
export function formatDetail(detail: unknown): string {
  if (detail == null) return "";
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((d) => {
        if (d && typeof d === "object" && "msg" in d) {
          const item = d as { loc?: unknown[]; msg?: unknown };
          const loc = Array.isArray(item.loc) ? item.loc.filter((x) => x !== "body" && x !== "query").join(".") : "";
          return loc ? `${loc}: ${String(item.msg)}` : String(item.msg);
        }
        return typeof d === "string" ? d : JSON.stringify(d);
      })
      .join("; ");
  }
  if (typeof detail === "object") return JSON.stringify(detail);
  return String(detail);
}

export function errorMessage(e: unknown): string {
  if (e instanceof ApiError) return e.message;
  if (e instanceof Error) return e.message;
  return String(e);
}

type Method = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";

async function request<T>(method: Method, path: string, body?: unknown, opts: { noAuthRedirect?: boolean } = {}): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  let payload: BodyInit | undefined;
  if (body instanceof FormData) {
    payload = body; // browser sets the multipart boundary
  } else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }
  if (method !== "GET") {
    const tok = readCookie("ja_csrf") ?? csrfToken;
    if (tok) headers["x-csrf-token"] = tok;
  }
  let res: Response;
  try {
    res = await fetch(`/api${path}`, { method, headers, body: payload, credentials: "include" });
  } catch (e) {
    throw new ApiError(0, `Network error: ${errorMessage(e)}. Is the API running?`, null);
  }
  if (res.status === 204) return undefined as T;
  const ctype = res.headers.get("content-type") ?? "";
  const data: unknown = ctype.includes("application/json") ? await res.json().catch(() => null) : await res.text();
  if (!res.ok) {
    const detail = data && typeof data === "object" && "detail" in data ? (data as { detail: unknown }).detail : data;
    const msg = formatDetail(detail) || `${res.status} ${res.statusText}`;
    if (res.status === 401 && !opts.noAuthRedirect && unauthorizedHandler) unauthorizedHandler();
    throw new ApiError(res.status, msg, detail);
  }
  return data as T;
}

/** Fetches a binary endpoint and triggers a browser download (used for the privacy export). */
export async function downloadBlob(path: string, fallbackName: string): Promise<void> {
  const res = await fetch(`/api${path}`, { credentials: "include" });
  if (!res.ok) {
    const data: unknown = await res.json().catch(() => null);
    const detail = data && typeof data === "object" && "detail" in data ? (data as { detail: unknown }).detail : null;
    if (res.status === 401 && unauthorizedHandler) unauthorizedHandler();
    throw new ApiError(res.status, formatDetail(detail) || `${res.status} ${res.statusText}`, detail);
  }
  const blob = await res.blob();
  const cd = res.headers.get("content-disposition") ?? "";
  const m = /filename="?([^";]+)"?/.exec(cd);
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = m ? m[1] : fallbackName;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
}

// ------------------------------------------------------------------ shared
export type ISODate = string;
export type Role = "admin" | "viewer";

export const APP_STATES = [
  "DISCOVERED", "MATCHED", "PREPARED", "NEEDS_REVIEW", "APPROVED", "SUBMITTED", "CONFIRMED",
  "REJECTED_BY_USER", "BLOCKED_BY_POLICY", "EXPIRED", "DUPLICATE", "FAILED", "FOLLOW_UP",
] as const;
export type AppState = (typeof APP_STATES)[number];

/** Mirror of backend/app/statemachine.py TRANSITIONS, used only to decide which buttons to show. The server enforces. */
export const TRANSITIONS: Record<AppState, AppState[]> = {
  DISCOVERED: ["MATCHED", "BLOCKED_BY_POLICY", "EXPIRED", "DUPLICATE", "REJECTED_BY_USER"],
  MATCHED: ["PREPARED", "NEEDS_REVIEW", "DISCOVERED", "BLOCKED_BY_POLICY", "EXPIRED", "DUPLICATE", "REJECTED_BY_USER"],
  PREPARED: ["NEEDS_REVIEW", "APPROVED", "PREPARED", "BLOCKED_BY_POLICY", "EXPIRED", "DUPLICATE", "REJECTED_BY_USER"],
  NEEDS_REVIEW: ["PREPARED", "NEEDS_REVIEW", "APPROVED", "BLOCKED_BY_POLICY", "EXPIRED", "DUPLICATE", "REJECTED_BY_USER"],
  APPROVED: ["SUBMITTED", "FAILED", "NEEDS_REVIEW", "BLOCKED_BY_POLICY", "EXPIRED", "DUPLICATE", "REJECTED_BY_USER"],
  SUBMITTED: ["CONFIRMED", "FAILED"],
  CONFIRMED: ["FOLLOW_UP"],
  FOLLOW_UP: ["FOLLOW_UP"],
  FAILED: ["APPROVED", "NEEDS_REVIEW", "REJECTED_BY_USER"],
  BLOCKED_BY_POLICY: ["DISCOVERED", "EXPIRED", "DUPLICATE", "REJECTED_BY_USER"],
  REJECTED_BY_USER: ["DISCOVERED"],
  EXPIRED: [],
  DUPLICATE: [],
};

export function canTransition(src: string, dst: AppState): boolean {
  return (TRANSITIONS[src as AppState] ?? []).includes(dst);
}

export const ANSWER_STATUSES = ["SUPPORTED", "USER_APPROVED", "NEEDS_REVIEW", "SENSITIVE_MISSING", "ATTESTATION", "ON_SITE"] as const;
export type AnswerStatus = (typeof ANSWER_STATUSES)[number];

// ------------------------------------------------------------------ auth / controls / audit / privacy (admin.py)
export interface Me {
  username: string;
  role: Role;
  csrf_token: string | null;
}

export interface PauseState {
  paused: boolean;
  db_flag: boolean;
  kill_switch_file: boolean;
  reason: string | null;
  warning?: string; // only on /controls/resume when the kill-switch file is still present
}

export interface Retention {
  expired_job_days: number;
  audit_days: number;
  connector_run_days: number;
  rejected_application_days: number;
}

export interface Controls extends PauseState {
  retention: Retention;
}

export interface AuditEvent {
  id: number;
  ts: ISODate;
  actor: string;
  action: string;
  entity_type: string | null;
  entity_id: string | null;
  details: Record<string, unknown> | null;
  hash: string; // first 12 hex chars
}

export interface AuditVerify {
  ok: boolean;
  first_bad_id: number | null;
}

export interface DeleteResult {
  documents: number;
  facts: number;
  applications: number;
}

// ------------------------------------------------------------------ profile / documents / facts / answers / prefs (profile.py)
export interface Profile {
  id: number;
  first_name: string;
  last_name: string;
  email: string;
  phone: string | null;
  location: string | null;
  timezone: string;
  linkedin_url: string | null;
  portfolio_links: string[];
  resume_folder: string | null;
  folder_consent_at: ISODate | null;
}

export interface ProfileIn {
  first_name: string;
  last_name: string;
  email: string;
  phone: string | null;
  location: string | null;
  timezone: string;
  linkedin_url: string | null;
  portfolio_links: string[];
}

export interface FolderFile {
  name: string;
  size: number;
  modified: number; // unix seconds
}

export interface DocumentOut {
  id: number;
  kind: string; // resume | linkedin_export | generated_resume | cover_letter
  filename: string;
  sha256: string;
  size_bytes: number;
  version: number;
  parse_status: string; // pending | parsed | failed | rejected
  parse_error: string | null;
  created_at: ISODate;
}

export interface LinkedInImportResult {
  document_id: number;
  facts_created: number;
  conflicts: number;
  duplicate: boolean;
}

export const FACT_KINDS = ["role", "achievement", "education", "skill", "certification", "project", "summary", "contact"] as const;
export type FactKind = (typeof FACT_KINDS)[number];
/** Kinds accepted by POST /api/facts (contact facts come only from documents). */
export const CREATABLE_FACT_KINDS = ["role", "achievement", "education", "skill", "certification", "project", "summary"] as const;
export type CreatableFactKind = (typeof CREATABLE_FACT_KINDS)[number];

/** Mirror of FACT_FIELDS in profile.py: the only data keys the server keeps for each kind. */
export const FACT_FIELDS: Record<FactKind, string[]> = {
  role: ["title", "employer", "start", "end", "location"],
  achievement: ["text"],
  project: ["text"],
  summary: ["text"],
  skill: ["name"],
  certification: ["name", "authority"],
  education: ["degree", "institution", "year"],
  contact: ["field", "value"],
};

export type FactStatus = "PENDING" | "APPROVED" | "REJECTED";

export interface Fact {
  id: number;
  kind: FactKind;
  data: Record<string, unknown>;
  parent_id: number | null;
  status: FactStatus;
  origin: string; // resume | linkedin_export | user
  document_id: number | null;
  char_start: number | null;
  char_end: number | null;
  snippet: string | null;
  approved_at: ISODate | null;
  has_open_conflict: boolean;
}

export interface FactDecisionResult {
  updated: number[];
  skipped: { id: number; reason: string }[];
}

export interface Conflict {
  id: number;
  fact_a_id: number;
  fact_b_id: number | null;
  field: string;
  description: string;
  status: "OPEN" | "RESOLVED";
  resolution: string | null;
}

export interface StandardAnswer {
  id: number;
  question_key: string;
  sensitive: boolean;
  approved: boolean;
  answer: string; // "••••••" when sensitive and not revealed
  updated_at: ISODate;
}

export interface AnswersResponse {
  answers: StandardAnswer[];
  sensitive_keys: string[];
}

export const WEIGHT_KEYS = ["title", "skills", "seniority", "location", "domain", "compensation"] as const;
export const WORK_ARRANGEMENTS = ["remote", "hybrid", "onsite"] as const;
export const EMPLOYMENT_TYPES = ["full_time", "part_time", "contract", "internship", "temporary"] as const;

export interface PreferencesIn {
  titles: string[];
  keywords: string[];
  seniority: string[];
  geographies: string[];
  work_arrangements: string[];
  employment_types: string[];
  salary_floor: number | null;
  salary_currency: string;
  industries: string[];
  excluded_employers: string[];
  excluded_terms: string[];
  min_score: number;
  polling_minutes: number;
  daily_application_limit: number;
  weights: Record<string, number>;
}

export interface Preferences extends PreferencesIn {
  updated_at?: ISODate;
  seniority_levels?: string[]; // only on GET
}

// ------------------------------------------------------------------ sources / boards / pipeline (jobs.py)
export interface Source {
  key: string;
  name: string;
  method: string;
  terms_url: string;
  date_checked: string;
  review_current: boolean;
  rate_limit_per_minute: number;
  read_permitted: boolean;
  submit_permitted: boolean;
  read_basis: string | null;
  submit_basis: string | null;
  notes: string | null;
  enabled: boolean;
  auto_submit_opt_in: boolean;
  daily_submit_limit: number;
  verified_adapter: boolean;
  implemented: boolean;
}

export interface SourcePatch {
  enabled?: boolean;
  auto_submit_opt_in?: boolean;
  daily_submit_limit?: number;
}

export interface Board {
  id: number;
  source_key: string;
  board_token: string;
  employer_name: string | null;
  enabled: boolean;
  consecutive_failures: number;
  next_attempt_at: ISODate | null;
  last_success_at: ISODate | null;
}

export interface ConnectorHealth {
  board_id: number;
  source: string;
  board: string;
  enabled: boolean;
  consecutive_failures: number;
  next_attempt_at: ISODate | null;
  last_success_at: ISODate | null;
  last_status: string | null;
  last_error: string | null;
  last_run_at: ISODate | null;
  last_counts: { seen: number; new: number; expired: number } | null;
}

export interface PollRun {
  source: string;
  board: string;
  status: string;
  seen: number;
  new: number;
  expired: number;
  error: string | null;
}

/** {new, matched, blocked, expired, duplicate} or {matched: 0, reason: "no preferences"}. */
export type MatchCounts = Record<string, number | string>;

// ------------------------------------------------------------------ applications
export interface Job {
  id: number;
  source: string;
  board: string;
  external_id: string;
  employer: string;
  title: string;
  location: string | null;
  work_arrangement: string;
  employment_type: string;
  salary_min: number | null;
  salary_max: number | null;
  salary_currency: string | null;
  posted_at: ISODate | null;
  first_seen_at: ISODate;
  last_seen_at: ISODate;
  expired_at: ISODate | null;
  duplicate_of_id: number | null;
  canonical_url: string | null;
  apply_url: string | null;
  permission: Record<string, unknown>;
}

export interface ApplicationSummary {
  id: number;
  state: AppState;
  score: number | null;
  job: Job;
  updated_at: ISODate;
  unmet: string[];
  exclusions: string[];
}

export interface ScoreComponent {
  points: number;
  max: number;
  [extra: string]: unknown; // job_level, required_years, arrangement, hits, ...
}

export interface Evidence {
  criterion: string;
  detail: string;
  fact_ids?: number[];
}

export interface MatchData {
  score: number;
  components: Record<string, ScoreComponent>;
  evidence: Evidence[];
  unmet: string[];
  exclusions: string[];
  notes: string[];
  required_skills: string[];
  matched_skill_fact_ids: Record<string, number[]>;
}

export interface Provenance {
  fact_ids: number[];
  profile_fields: string[];
  job_fields?: string[];
  template: boolean;
}

export interface ResumeLine extends Provenance {
  text: string;
  section: string;
}

export interface CoverUnit extends Provenance {
  text: string;
  generator?: string; // e.g. "ollama:qwen3.6:35b" when drafted by the local model and verified
}

export interface LetterGeneration {
  generator: string; // "template" or "ollama:<model>"
  kept?: number;
  facts_sent?: number;
  dropped?: { text: string; reasons: string[] }[];
  fallback_reason?: string;
}

export interface LLMModel {
  name: string;
  size_gb: number;
  parameters: string | null;
  family: string | null;
  local: boolean;
}

export interface LLMStatus {
  enabled: boolean;
  model: string;
  base_url: string;
  endpoint_is_local: boolean;
  reachable: boolean;
  models: LLMModel[];
  error: string | null;
}

export interface PacketAnswer {
  question: string;
  key: string;
  required: boolean;
  sensitive: boolean;
  status: AnswerStatus;
  source: { profile_field?: string; answer_id?: number; packet_file?: string } | null;
  note: string;
  answer: string | null;
}

export interface Packet {
  id: number;
  version: number;
  approved_at: ISODate | null;
  resume_lines: ResumeLine[];
  cover_letter: CoverUnit[];
  answers: PacketAnswer[];
  unsupported_count: number;
  generation?: { cover_letter?: LetterGeneration };
}

export interface HandoffRef {
  id: number;
  reasons: string[];
  status: string; // OPEN | DONE | DISMISSED
  created_at: ISODate;
}

export interface Attempt {
  id: number;
  adapter: string;
  status: string;
  confirmation_id: string | null;
  confirmation_url: string | null;
  error: string | null;
  started_at: ISODate;
  finished_at: ISODate | null;
}

export interface AutoSubmitPolicy {
  allowed: boolean;
  reasons: string[];
  checks: Record<string, boolean>;
}

export interface ApplicationDetail extends ApplicationSummary {
  match: MatchData | null;
  description: string;
  requirements: unknown[];
  packet: Packet | null;
  handoffs: HandoffRef[];
  attempts: Attempt[];
  auto_submit_policy: AutoSubmitPolicy;
}

export interface SubmitResult {
  submitted: boolean;
  reasons?: string[];
  checks?: Record<string, boolean>;
  confirmation_id?: string | null;
}

export interface HandoffItem {
  id: number;
  application_id: number;
  state: AppState;
  reasons: string[];
  created_at: ISODate;
  employer: string;
  title: string;
  apply_url: string | null;
}

// ------------------------------------------------------------------ endpoints
const q = (params: Record<string, string | number | boolean | null | undefined>): string => {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== "") sp.set(k, String(v));
  const s = sp.toString();
  return s ? `?${s}` : "";
};
const enc = encodeURIComponent;

export const api = {
  // auth
  login: (username: string, password: string, totp: string) =>
    request<Me>("POST", "/auth/login", { username, password, totp: totp || null }, { noAuthRedirect: true }),
  logout: () => request<{ ok: boolean }>("POST", "/auth/logout", {}),
  me: () => request<Me>("GET", "/auth/me", undefined, { noAuthRedirect: true }),

  // controls / audit / privacy
  controls: () => request<Controls>("GET", "/controls"),
  pause: (reason: string) => request<PauseState>("POST", "/controls/pause", { reason }),
  resume: (reason: string) => request<PauseState>("POST", "/controls/resume", { reason }),
  setRetention: (r: Partial<Retention>) => request<Retention>("PUT", "/controls/retention", r),
  llm: () => request<LLMStatus>("GET", "/llm"),
  setLlm: (enabled: boolean, model: string) => request<{ enabled: boolean; model: string }>("PUT", "/llm", { enabled, model }),
  audit: (limit = 200, beforeId?: number) => request<AuditEvent[]>("GET", `/audit${q({ limit, before_id: beforeId })}`),
  verifyAudit: () => request<AuditVerify>("GET", "/audit/verify"),
  exportData: () => downloadBlob("/privacy/export", "jobapplier_export.zip"),
  deleteAll: (confirm: string) => request<DeleteResult>("POST", "/privacy/delete", { confirm }),

  // profile & folder
  profile: () => request<Profile | null>("GET", "/profile"),
  saveProfile: (p: ProfileIn) => request<Profile>("PUT", "/profile", p),
  grantFolder: (folder: string, consent: boolean) => request<Profile>("POST", "/profile/folder-consent", { folder, consent }),
  revokeFolder: () => request<Profile>("DELETE", "/profile/folder-consent"),
  folderFiles: () => request<FolderFile[]>("GET", "/folder/files"),

  // documents
  documents: () => request<DocumentOut[]>("GET", "/documents"),
  importFromFolder: (name: string) => request<DocumentOut>("POST", "/documents/import-from-folder", { name }),
  uploadResume: (file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return request<DocumentOut>("POST", "/documents/upload", fd);
  },
  uploadLinkedIn: (file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return request<LinkedInImportResult>("POST", "/documents/linkedin-export", fd);
  },

  // facts & conflicts
  facts: () => request<Fact[]>("GET", "/facts"),
  editFact: (id: number, data: Record<string, string | null>) => request<Fact>("PATCH", `/facts/${id}`, { data }),
  addFact: (kind: CreatableFactKind, data: Record<string, string | null>, parentId: number | null) =>
    request<Fact>("POST", "/facts", { kind, data, parent_id: parentId }),
  decideFacts: (ids: number[], decision: FactStatus) => request<FactDecisionResult>("POST", "/facts/decide", { ids, decision }),
  conflicts: () => request<Conflict[]>("GET", "/conflicts"),
  resolveConflict: (id: number, keep: "a" | "b" | "both" | "neither", note: string) =>
    request<{ id: number; status: string }>("POST", `/conflicts/${id}/resolve`, { keep, note }),

  // standard answers
  answers: (reveal = false) => request<AnswersResponse>("GET", `/answers${reveal ? "?reveal=true" : ""}`),
  saveAnswer: (key: string, answer: string, approved: boolean, sensitive?: boolean) =>
    request<StandardAnswer>("PUT", `/answers/${enc(key)}`, { answer, approved, ...(sensitive ? { sensitive: true } : {}) }),
  deleteAnswer: (key: string) => request<void>("DELETE", `/answers/${enc(key)}`),

  // preferences
  preferences: () => request<Preferences>("GET", "/preferences"),
  savePreferences: (p: PreferencesIn) => request<Preferences>("PUT", "/preferences", p),

  // sources / boards / pipeline
  sources: () => request<Source[]>("GET", "/sources"),
  patchSource: (key: string, patch: SourcePatch) => request<Source>("PATCH", `/sources/${enc(key)}`, patch),
  boards: () => request<Board[]>("GET", "/boards"),
  addBoard: (source_key: string, board_token: string, employer_name: string | null) =>
    request<Board>("POST", "/boards", { source_key, board_token, employer_name }),
  setBoardEnabled: (id: number, enabled: boolean) => request<Board>("PATCH", `/boards/${id}`, { enabled }),
  deleteBoard: (id: number) => request<void>("DELETE", `/boards/${id}`),
  connectorHealth: () => request<ConnectorHealth[]>("GET", "/health/connectors"),
  pollNow: () => request<PollRun[]>("POST", "/pipeline/poll", {}),
  matchNow: () => request<MatchCounts>("POST", "/pipeline/match", {}),

  // applications
  applications: (states: string[], minScore: number | null) =>
    request<ApplicationSummary[]>("GET", `/applications${q({ state: states.join(","), min_score: minScore })}`),
  application: (id: number) => request<ApplicationDetail>("GET", `/applications/${id}`),
  prepare: (id: number) => request<{ packet_id: number; state: AppState; unsupported_count: number }>("POST", `/applications/${id}/prepare`, {}),
  mapAnswer: (id: number, index: number, answerId: number | null) =>
    request<{ unsupported_count: number }>("POST", `/applications/${id}/answers/${index}`, { answer_id: answerId }),
  approve: (id: number, answerOnSite: boolean) =>
    request<{ state: AppState }>("POST", `/applications/${id}/approve`, { answer_on_site: answerOnSite }),
  submit: (id: number) => request<SubmitResult>("POST", `/applications/${id}/submit`, {}),
  manualSubmission: (id: number, confirmation_id: string | null, confirmation_url: string | null) =>
    request<{ state: AppState; attempt_id: number }>("POST", `/applications/${id}/manual-submission`, { confirmation_id, confirmation_url }),
  reject: (id: number, reason = "") => request<{ state: AppState }>("POST", `/applications/${id}/reject`, { reason }),
  restore: (id: number, reason = "") => request<{ state: AppState }>("POST", `/applications/${id}/restore`, { reason }),
  confirm: (id: number, reason = "") => request<{ state: AppState }>("POST", `/applications/${id}/confirm`, { reason }),
  followUp: (id: number, reason = "") => request<{ state: AppState }>("POST", `/applications/${id}/follow-up`, { reason }),
  resumeDocxUrl: (id: number) => `/api/applications/${id}/resume.docx`,
  coverLetterUrl: (id: number) => `/api/applications/${id}/cover-letter.txt`,

  // handoffs
  handoffs: (status = "OPEN") => request<HandoffItem[]>("GET", `/handoffs${q({ status })}`),
  dismissHandoff: (id: number) => request<{ id: number; status: string }>("POST", `/handoffs/${id}/dismiss`, {}),
};
