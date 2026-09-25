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
The MVP uses no language model. If one is added (roadmap), it must be local by default. External endpoints must be opt-in, receive redacted input (no email/phone), and never be used to train shared models.
