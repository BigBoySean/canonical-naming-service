# Canonical Naming Service

Resolves raw PE fund / partnership names to canonical entities via a tiered matching cascade.

## Status

Early scaffold. Only `GET /health` is live in this commit; the resolution pipeline (`/resolve`, `/resolve/batch`, `/entities`) lands in subsequent commits per the roadmap. See [`DECISIONS.md`](./DECISIONS.md) for the design rationale and the 4-stage cascade overview.

## Requirements

- Python 3.11 or newer
- An Anthropic API key — **only** required when the LLM matching tier is enabled (`LLM_ENABLED=true`). Tests run fully offline without one; the LLM tier is mocked by default.

## Setup

```bash
git clone https://github.com/BigBoySean/canonical-naming-service.git
cd canonical-naming-service

python -m venv .venv
# bash / zsh:
source .venv/bin/activate
# Windows (PowerShell):
# .venv\Scripts\Activate.ps1
# Windows (cmd):
# .venv\Scripts\activate.bat

make install
cp .env.example .env
```

The default `.env` runs with `LLM_ENABLED=false`, so no API key is needed to start the server or run the test suite.

## Run

```bash
make run
```

The server listens on `http://localhost:8000`. Interactive OpenAPI docs are at `http://localhost:8000/docs`.

## Test

```bash
make test
```

Tests run **fully offline** — the LLM tier is mocked by default (`LLM_ENABLED=false`). No network, no API key, no flakiness.

## API

Full OpenAPI documentation is available at `http://localhost:8000/docs` once the server is running.

This commit ships the health endpoint only:

```bash
curl http://localhost:8000/health
# {"status":"ok","version":"0.1.0"}
```

`POST /resolve`, `POST /resolve/batch`, `GET /entities/{id}`, and `POST /entities` land in subsequent commits.

## Architecture

Layered package structure: `api/` (HTTP boundary) → `services/` (orchestration) → `matching/` (normalizer + matchers) → `repos/` (storage), with `models/` for shared Pydantic schemas. Configuration is centralised via `pydantic-settings` (env-driven, no hard-coded secrets).

Resolution uses a **4-stage matching cascade** with early exit at each stage:

1. **Normalize** — diacritics, casing, Roman→Arabic numerals, legal-suffix handling, abbreviation expansion.
2. **Exact match** — O(1) lookup against canonical names + known aliases.
3. **Fuzzy match** — `rapidfuzz` token-set / partial-ratio above a threshold (default 92).
4. **LLM disambiguation** — Anthropic Claude (Haiku 4.5) picks from the top fuzzy candidates; invoked only when stages 2–3 don't decide.
5. **`needs_review`** fallback for genuine no-matches.

See [`DECISIONS.md`](./DECISIONS.md) for the full Decision / Alternatives / Why-rejected breakdown.

## See also

- [`DECISIONS.md`](./DECISIONS.md) — design choices and trade-offs.
