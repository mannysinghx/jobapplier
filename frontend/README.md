# jobApplier — frontend

React 18 + TypeScript single-page app for the self-hosted jobApplier API. No runtime dependencies beyond React:
no router, UI kit, state library or CSS framework. Plain CSS (`src/styles.css`) with light/dark themes.

## Development

```sh
npm install && npm run dev
```

- Dev server: <http://localhost:5180> (`strictPort`, so it fails instead of picking another port).
- Expects the FastAPI backend on **127.0.0.1:8100**. Vite proxies `/api` there, so cookies are same-origin.
- Sign in with a user created on the backend (username, password, 6-digit TOTP code for admins).

Other scripts:

| Command | What it does |
|---|---|
| `npm run build` | `tsc -b` (strict) then `vite build` into `dist/` |
| `npm run typecheck` | type-check only |
| `npm run preview` | serve `dist/` locally on port 5181 (API proxy is **not** configured for preview) |

## Production (Docker)

`Dockerfile` builds with `node:20-alpine` and serves `dist/` from `nginx:1.27-alpine`. `nginx.conf`:

- proxies `/api/` → `http://api:8100` (the `api` service in `docker-compose.yml`), and serves the SPA with an `index.html` fallback;
- sets `Content-Security-Policy: default-src 'self'; frame-ancestors 'none'`, `X-Content-Type-Options: nosniff` and
  `Referrer-Policy: no-referrer` on every response;
- allows request bodies up to 55 MB (LinkedIn export ZIPs are capped at 50 MB by the API).

The compose file publishes the web container on `127.0.0.1:5180`.

## How it talks to the API

- `src/api.ts` is the only place that calls `fetch`. All requests use `credentials: "include"`. Every non-GET request
  sends `x-csrf-token` with the value of the `ja_csrf` cookie (falling back to the `csrf_token` returned by login / `/auth/me`).
- Any 401 returns the app to the login screen. Error text comes from FastAPI's `detail` (422 validation arrays are flattened
  to `field: message`).
- Types in `src/api.ts` mirror the response dicts in `backend/app/api/*.py`.

## Security notes

- No `dangerouslySetInnerHTML` anywhere. Job descriptions and other text from job sources are untrusted and rendered as
  plain text in a wrapped `<pre>`.
- URLs from job sources are only rendered as links when they start with `https://`, and always with
  `rel="noopener noreferrer" target="_blank"`.
- No inline scripts, external fonts, CDNs or `data:` assets, so the strict CSP holds.
- Viewer accounts get a read-only UI. The server enforces roles regardless.

## Layout

```
src/
  main.tsx            entry
  App.tsx             session bootstrap, header, PAUSED banner, hash-based tabs (#profile, #jobs/42, ...)
  api.ts              typed API client + response types
  styles.css          all styles (CSS variables, light/dark, down to 375px wide)
  components/         useAsync/useAction hooks, app context, login, shared UI (badges, SafeLink, UntrustedText, ...)
  pages/              one file per tab, plus ApplicationDetailPage
```
