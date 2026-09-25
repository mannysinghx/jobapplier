# Roadmap

Every source or submission route below is added **only after** a permission review (see `SOURCE_REGISTRY.md`).

## Next (high value, low risk)
1. **Greenhouse application questions**: `GET /v1/boards/{token}/jobs/{id}?questions=true` (a public GET, already permitted) to draft answers against the real form, including EEOC/compliance questions (always sensitive).
2. **More discovery sources** after review: USAJOBS, SmartRecruiters, Workable, Recruitee, Personio public feeds.
3. **Employer discovery helper**: suggest boards to follow from a user-maintained company list. No crawling.
4. **Better extraction**: a richer date grammar, multi-column PDF handling, and optional Apache Tika in a sandbox container.
5. **Encryption key rotation** command.
6. **Follow-up reminders**: FOLLOW_UP due dates plus local notifications.

## Later
7. ~~Optional local LLM for cover letters~~: **done** (Ollama + claim verifier). Next: use it for resume bullet ordering/summary under the same verifier.
8. **Visible Playwright assist** (not auto-submit): opens the employer's apply page in a normal visible browser for the user and pre-fills only SUPPORTED fields. It stops at any CAPTCHA/MFA/attestation. Only for sites whose terms allow it.
9. **Cloud folders**: Google Drive / OneDrive / Dropbox via the official OAuth APIs, read-only scope, one selected folder.
10. **Multi-user**: Keycloak (OIDC), per-tenant isolation, admin MFA enforced by the IdP.
11. **Grafana dashboards** and alert rules (connector failure streaks, stale polling, open handoffs).

## Submission routes: what "verified" requires
- The provider's terms or docs **explicitly** allow a candidate or their agent to submit programmatically, with a documented method.
- The adapter implements `SubmissionAdapter`: `should_abort` before irreversible steps, CHALLENGE on any CAPTCHA/MFA/attestation/anti-bot, and a confirmation capture.
- A supervised pilot: auto-submit on for one source with a daily limit of 1–2, every submission reviewed. Record it in OPS_LOG.
- Only then set `submit_permitted: true` and `verified = True`.
