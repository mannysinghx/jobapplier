# jobApplier

A self-hosted, open-source platform that finds fresh jobs on **permitted** sources, scores them against your **approved** resume facts, prepares truthful tailored application packets, and tracks every application. It submits automatically only where a source expressly permits it, and today no source does.

> **Status: MVP, search-and-prepare mode.** Discovery (Greenhouse, Lever, Ashby public job-board APIs), explainable matching, packet generation and tracking all work. Auto-submit is fully gated and tested but **off**: no job source currently permits candidate-side automated submission (see [docs/SOURCE_REGISTRY.md](docs/SOURCE_REGISTRY.md)). Each approved application becomes a **handoff**: an apply link plus a prefilled packet you submit yourself, then record in one click.

## What it does
- **Profile & facts**: import a PDF/DOCX resume from a folder you consent to (read-only) or by upload, plus your own LinkedIn data-export ZIP. Facts are extracted with provenance (document + character span). Resume/LinkedIn conflicts are flagged. **Nothing is used until you approve it.**
- **Job sites**: LinkedIn, Indeed, ZipRecruiter, Dice and Ladders via their **permitted** routes: Dice's official search connector, your own job-alert emails, your LinkedIn data export and manual adds. None of them allows automated applying, so those are handoffs.
- **Local AI cover letters** (optional): a local Ollama model drafts from approved facts only. Every sentence is verified against the facts it cites, and anything unsupported is dropped.
- **Continuous discovery**: poll employer job boards on a schedule with rate limits, backoff, health reporting, deduplication (same source and across sources) and expiry detection.
- **Explainable matching**: title 25 / skills 25 / seniority 15 / location 15 / domain 10 / compensation 10 (configurable). Hard exclusions override the score. Every point cites evidence, and missing evidence costs points.
- **Packets**: a tailored DOCX resume and cover letter built only from approved facts. Every line carries provenance. Form answers are drafted from your profile and approved standard answers. **Sensitive/legal questions are never inferred.**
- **Deterministic policy gate** for any submission: 19 checks, including permission, opt-in, freshness, duplicates, threshold, answers, limits and pause. Plus idempotency keys, challenge handoff and a kill switch.
- **Security & privacy**: argon2 + TOTP MFA, CSRF, encrypted files and PII fields, isolated parser subprocess, append-only hash-chained audit log, export/delete, retention.

## Quick start
See [docs/SETUP.md](docs/SETUP.md). Short version (dev, no Docker):
```bash
cd backend && python3.12 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
export JA_ENCRYPTION_KEY=$(.venv/bin/python -m app.cli gen-key)
.venv/bin/python -m app.cli init-db && .venv/bin/python -m app.cli create-admin admin
.venv/bin/uvicorn app.main:app --port 8100
cd ../frontend && npm install && npm run dev      # http://localhost:5180
```

## Docs
| | |
|---|---|
| [Architecture, data flow, state machine, milestones](docs/ARCHITECTURE.md) | [Source permission registry](docs/SOURCE_REGISTRY.md) (+ [`config/sources.yaml`](config/sources.yaml)) |
| [Threat model](docs/THREAT_MODEL.md) | [REST API](docs/API.md) (+ [`openapi.json`](docs/openapi.json)) |
| [Setup & backups](docs/SETUP.md) | [User guide](docs/USER_GUIDE.md) |
| [Privacy & retention](docs/PRIVACY.md) | [Incident & kill-switch runbook](docs/RUNBOOK.md) |
| [Dependency licenses](docs/LICENSES.md) | [Roadmap](docs/ROADMAP.md) |
| [Ops log](docs/OPS_LOG.md) | |

## Tests
`cd backend && .venv/bin/python -m pytest -q`. The suite covers permissions, provenance, prompt injection, unsupported/sensitive answers, freshness, duplicates, rate limits, pause/kill switch, idempotency, audit immutability, export and deletion. It uses synthetic data only and makes no network calls.

## License
Apache-2.0.
