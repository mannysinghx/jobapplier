# Privacy & retention guide

## What is stored, and how
| Data | Where | Protection |
|---|---|---|
| Name, location, LinkedIn/portfolio URLs | `profiles` | DB (private network) |
| Email, phone | `profiles` | Encrypted column (Fernet) |
| Resume files, LinkedIn export, extracted text, generated resumes | `data/store/**.enc` | Encrypted files, content-addressed by SHA-256 |
| Facts + provenance snippets | `facts` | DB. Snippets are short excerpts of your resume. |
| Standard answers (incl. sensitive) | `standard_answers` | Encrypted column. Sensitive values are masked in API listings. |
| Packets | `packets` | Answers stored as **references**, not values |
| Submission snapshots | `submission_attempts.snapshot` | Encrypted column: exactly what was submitted |
| Job listings | `jobs` | Public data from permitted sources |
| Audit log | `audit_events` | Append-only, hash-chained. Sensitive keys are redacted. No personal values. |

No data is sent to any model or third party. The only outbound network calls are GET requests to enabled job-board APIs.

## Job-alert email imports
- You choose what to upload (.eml, .mbox or a .zip of .eml). Messages not sent from linkedin.com, indeed.com, ziprecruiter.com, dice.com or theladders.com are skipped **unread**, and nothing from them is stored.
- From alert emails only the job fields are kept (title, company, location, salary line, link, email date, your mail provider's DKIM verdict). The raw email is never stored.
- Links are never opened by the app. They must point at the site's own domain or they are dropped (phishing guard).
- LinkedIn export: Saved Jobs and Job Applications are imported. Recruiter contact details and your question answers in the Job Applications CSV are **not** stored.

## Consent
- Folder access needs an explicit consent checkbox. It is recorded with a timestamp, audited and revocable. Access is read-only, top-level PDF/DOCX only.
- LinkedIn: only your URL and your own data export. The app never asks for your LinkedIn password.

## Export
**Controls & Privacy → Export** (or `GET /api/privacy/export`) downloads a ZIP with every table row about you, decrypted, plus your original documents.

## Deletion
**Controls & Privacy → Delete all** (type `DELETE MY DATA`) removes the profile, facts, answers, documents (DB rows **and** encrypted files), applications, packets, submission records and handoffs. Kept: public job listings (removed by retention) and audit events (no personal values; they record *that* a deletion happened).
Backups made before the deletion still contain the data. Rotate or destroy them per your own policy.

## Retention (defaults, editable in the UI)
| Setting | Default | Effect |
|---|---|---|
| `expired_job_days` | 90 | Delete expired listings you never applied to |
| `rejected_application_days` | 180 | Delete REJECTED_BY_USER / EXPIRED / DUPLICATE applications |
| `connector_run_days` | 30 | Delete connector health history |
| `audit_days` | 730 (min 30) | Purge old audit events through an audited maintenance window |

Retention runs daily via Celery beat (`app.tasks.retention`).

## Models / AI
Cover-letter drafting can use a **local Ollama model** (off by default; Controls → "Cover letters: local AI model").
- **Local only**: the endpoint must be loopback, a private address or `host.docker.internal`. Ollama cloud-offloaded models (`:cloud`, no local weights) are refused. Override only with `JA_LLM_ALLOW_REMOTE=true`, which you should not set.
- **Minimal input**: the model gets only numbered approved facts (roles, achievements, matched skills, education, certifications, summary), the sanitized job title and employer, and the matched skill names. It never receives your name, email, phone, location, sensitive answers or the job description.
- **No training**: nothing is sent for training. Ollama runs inference only.
- **Verification**: every sentence must cite facts and use only words and numbers found in them. Anything else is dropped, and the dropped sentences are shown to you on the packet. You still approve every packet.
