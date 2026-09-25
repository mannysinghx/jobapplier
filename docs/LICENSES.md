# Dependency & license inventory

Checked 2026-09-24 against the package metadata of the pinned versions. Re-check when upgrading.
Project license: Apache-2.0.

## Backend (Python, runtime)
| Package | Version | License | Notes |
|---|---|---|---|
| fastapi | 0.141.1 | MIT | |
| uvicorn[standard] | 0.53.0 | BSD-3-Clause | |
| SQLAlchemy | 2.1.0 | MIT | |
| alembic | 1.20.0 | MIT | |
| pydantic / pydantic-settings | 2.13.5 / 2.15.0 | MIT | |
| email-validator | 2.3.0 | Unlicense | |
| psycopg[binary] | 3.3.6 | LGPL-3.0-only | Used unmodified via dynamic import. Fine for self-hosting and distribution with source available. |
| celery | 5.6.3 | BSD-3-Clause | |
| redis (client) | 8.1.0 | MIT | |
| httpx | 0.28.1 | BSD-3-Clause | |
| pypdf | 6.19.0 | BSD-3-Clause | |
| python-docx | 1.2.0 | MIT | |
| cryptography | 50.0.1 | Apache-2.0 OR BSD-3-Clause | |
| argon2-cffi | 25.1.0 | MIT | |
| PyOTP | 2.10.0 | MIT | |
| prometheus_client | 0.26.0 | Apache-2.0 AND BSD-2-Clause | |
| PyYAML | 6.0.3 | MIT | |
| python-multipart | 0.0.32 | Apache-2.0 | |
| snowballstemmer | 3.1.1 | BSD-3-Clause | Porter2 stemming for the cover-letter claim verifier |

## Backend (dev/test)
| Package | Version | License |
|---|---|---|
| pytest | 9.1.1 | MIT |
| respx | 0.23.1 | BSD-3-Clause |

## Frontend
See `frontend/LICENSES.md` (React, Vite and TypeScript: MIT / Apache-2.0).

## Services (Docker images)
| Image | License | Notes |
|---|---|---|
| postgres:16-alpine | PostgreSQL License | |
| valkey/valkey:8-alpine | BSD-3-Clause | Chosen over Redis ≥7.4 (RSAL/SSPL, not OSI-approved) |
| nginx:1.27-alpine | BSD-2-Clause | |
| prom/prometheus | Apache-2.0 | optional |
| clamav/clamav | GPL-2.0 | optional. Separate process over the network protocol only, so its license does not extend to this code. |

## Deliberately not used
- Keycloak (Apache-2.0): fine license, but too heavy for the single-user MVP (roadmap).
- Apache Tika (Apache-2.0): needs a JVM; pypdf + python-docx are sufficient (roadmap/optional).
- Playwright (Apache-2.0): no permitted submission route to automate yet.
- Any proprietary SaaS or hosted LLM API.
