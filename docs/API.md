# REST API

Base path `/api`. Interactive docs: `/api/docs` (Swagger UI); machine-readable spec: [`openapi.json`](openapi.json).

**Auth**: `POST /api/auth/login` sets an httpOnly session cookie + a `ja_csrf` cookie. Every non-GET request must send `x-csrf-token: <ja_csrf>`. Endpoints that write require role `admin`.

| Method | Path | Summary |
|---|---|---|
| POST | `/api/auth/login` | Login |
| POST | `/api/auth/logout` | Logout |
| GET | `/api/auth/me` | Me |
| GET | `/api/controls` | Get Controls |
| POST | `/api/controls/pause` | Pause |
| POST | `/api/controls/resume` | Resume |
| PUT | `/api/controls/retention` | Put Retention |
| GET | `/api/audit` | List Audit |
| GET | `/api/audit/verify` | Verify |
| GET | `/api/privacy/export` | Export |
| POST | `/api/privacy/delete` | Delete Everything |
| GET | `/api/profile` | Read Profile |
| PUT | `/api/profile` | Upsert Profile |
| POST | `/api/profile/folder-consent` | Folder Consent |
| DELETE | `/api/profile/folder-consent` | Revoke Folder |
| GET | `/api/documents` | List Documents |
| GET | `/api/folder/files` | Folder Files |
| POST | `/api/documents/import-from-folder` | Import From Folder |
| POST | `/api/documents/upload` | Upload |
| POST | `/api/documents/linkedin-export` | Linkedin Export |
| GET | `/api/documents/{doc_id}/text` | Document Text |
| GET | `/api/facts` | List Facts |
| POST | `/api/facts` | Add Fact |
| PATCH | `/api/facts/{fact_id}` | Edit Fact |
| POST | `/api/facts/decide` | Decide Facts |
| GET | `/api/conflicts` | List Conflicts |
| POST | `/api/conflicts/{cid}/resolve` | Resolve Conflict |
| GET | `/api/answers` | List Answers |
| PUT | `/api/answers/{key}` | Put Answer |
| DELETE | `/api/answers/{key}` | Delete Answer |
| GET | `/api/preferences` | Read Prefs |
| PUT | `/api/preferences` | Write Prefs |
| GET | `/api/sources` | List Sources |
| PATCH | `/api/sources/{key}` | Patch Source |
| GET | `/api/boards` | List Boards |
| POST | `/api/boards` | Add Board |
| PATCH | `/api/boards/{bid}` | Patch Board |
| DELETE | `/api/boards/{bid}` | Delete Board |
| GET | `/api/health/connectors` | Health |
| POST | `/api/pipeline/poll` | Poll Now |
| POST | `/api/pipeline/match` | Match Now |
| GET | `/api/applications` | List Applications |
| GET | `/api/applications/{app_id}` | Application Detail |
| POST | `/api/applications/{app_id}/prepare` | Prepare |
| POST | `/api/applications/{app_id}/answers/{index}` | Map Answer |
| POST | `/api/applications/{app_id}/approve` | Approve |
| POST | `/api/applications/{app_id}/submit` | Submit |
| POST | `/api/applications/{app_id}/manual-submission` | Manual Submission |
| POST | `/api/applications/{app_id}/reject` | Reject |
| POST | `/api/applications/{app_id}/restore` | Restore |
| POST | `/api/applications/{app_id}/confirm` | Confirm |
| POST | `/api/applications/{app_id}/follow-up` | Follow Up |
| GET | `/api/applications/{app_id}/resume.docx` | Resume Docx |
| GET | `/api/applications/{app_id}/cover-letter.txt` | Cover Letter |
| GET | `/api/handoffs` | List Handoffs |
| POST | `/api/handoffs/{hid}/dismiss` | Dismiss Handoff |
| GET | `/api/health` | Health |
| GET | `/metrics` | Prom |

Error format: `{"detail": string | [validation errors]}`. 401 = not logged in, 403 = CSRF/role, 409 = state/policy conflict, 422 = validation.
