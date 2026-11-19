# Grants on demand — grant-matching portal

[![CI](../../actions/workflows/ci.yml/badge.svg)](../../actions/workflows/ci.yml)

An internal portal of a project office that raises grant funding for clients (municipalities,
utilities, farms, small businesses): **client requests → explainable grant matching →
applications moving through gated stages → versioned documents with sign-off.** A person always
submits the application; the portal never does.

This repository is a **public showcase**: the code of a system used in production, with every
piece of real data removed. The demo runs on synthetic data from `app/seed_demo.py` (fictional
funders and programmes on `*.example` domains, fictional clients and people).

![Grants portal — Today](docs/screenshot-today.jpg)

## Run it

```bash
docker compose up --build        # http://localhost:8070
```

Accounts and a 3-minute walkthrough: [DEMO.md](DEMO.md). `docker compose down -v` resets the data.

Without Docker (Python 3.12):

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
HRANTY_ENV=demo python -m app.seed_demo
HRANTY_ENV=demo uvicorn app.main:app --port 8070
```

## What is inside

| Area | What it does |
|---|---|
| Grant registry | Programmes with funder, type, amount range, deadline and reliability; idempotent import (`POST /api/ingest/radar`, Bearer token) with an ingest gate — invalid rows are counted and reported, never silently dropped. |
| Client requests | What the client needs money for; tags are derived from the text and can be corrected by hand. |
| Matching | Six weighted components, **each with a reason**: sector tags 0.35 · applicant type 0.20 (hard filter) · amount 0.15 · deadline 0.15 · geography 0.10 · reliability 0.05. Missing data scores a neutral 0.5 and is shown as "no data", never as zero. |
| Applications | Idea → Screening → Documents → Submission → Result. Each transition has a **gate** (score or a stated reason · verdict "apply" · required documents signed off · submission recorded by a person). |
| Documents | Versions grouped per application, sign-off by a person with the right **who is not the author** (identity is the account id), in-browser preview for PDF/text (office formats optionally via LibreOffice). |
| Today | Deadlines, new strong matches, team tasks and a "what changed" feed. |
| Daily self-maintenance | A background job (once a day, in-process) closes programmes whose deadline passed, flags those closing within 14 days, opens follow-up tasks for applications in progress on a closed programme, recomputes matches and announces new strong ones. Each run is journaled (`IngestLog`, feed `obslugovuvannya`); repeated runs are idempotent. |
| Knowledge | Short articles and briefs with sources; Markdown is HTML-escaped and link targets are limited to http(s), mailto and same-site paths. |

## Security

Implemented in [`app/authkit.py`](app/authkit.py) (shared with the sibling OFIS platform) and covered by tests:

- **Individual accounts** (the former shared team password and the "open mode" are gone).
- **Passwords**: argon2id; legacy `sha256(secret+password)` hashes are upgraded on first login.
- **Sessions**: server-side, absolute expiry + idle timeout, revocable (logout, "sign out everywhere"), rotated on login.
- **CSRF**: double-submit token + Origin/Referer check on every unsafe browser request;
  the Bearer-token API does not use cookies.
- **Login rate limiting** per client IP and per login.
- **Headers**: strict CSP with nonces (no inline scripts, handlers or styles), CSP reporting,
  `frame-ancestors 'none'`, `nosniff`, COOP/CORP, HSTS in production; documents are previewed
  in a sandboxed frame with their own CSP.
- **Deny-by-default authorisation on the route**: roles with capabilities (approve, send, delete,
  data); a generated test walks every route (anonymous → login, missing CSRF → 403, missing capability → 403).
- File uploads: extension allow-list, size limit, random stored names, path-traversal guard, SHA-256 dedup.
- Open-redirect guard on every `next`/return parameter.

## Quality gates

Run in CI ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)): ruff · mypy (strict on the
security/i18n core) · pytest with an every-screen crawl and generated access-boundary tests ·
coverage ≥ 75 % · pip-audit · bandit · PII pattern scan of the repo and a freshly seeded demo
database · Docker build + health check. Numbers: `python scripts/metrics.py`.

## Internationalisation

English by default, Ukrainian as the second language. Catalogue: [`app/locale/en.json`](app/locale/en.json);
a test crawls every screen in English and fails on any untranslated string.

## Known limits

- SQLite, single process (in-memory login limiter); sized for a team.
- Identifiers are transliterated Ukrainian (`potreby` = requests, `zayavky` = applications, `reyestr` = registry).
- The production system's programme-document harvester (a CLI crawler) is not part of this
  showcase, and the planned agent worker (stage B) is not implemented — the API exposes only the
  ingest endpoint.

## License

[MIT](LICENSE)
