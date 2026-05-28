# Canonical Naming Service

Resolves raw PE fund / partnership names to canonical entities via a tiered matching cascade.

## What it does

Private-equity data arrives with the same fund spelled a dozen different ways — `Blackstone Capital Partners VIII, L.P.`, `BCP VIII`, `Blackstone Cap. Partners 8`, `BX Cap Partners VIII (USD)` are all the same fund. This service takes a raw name and returns the canonical entity (id, canonical name, confidence, match method), so downstream systems can join on a single key instead of fuzzy-matching on the fly. It runs a four-tier cascade: normalise → exact → fuzzy → LLM, with a `needs_review` fallback for genuinely new entities.

## Quickstart (~5 minutes, no API key)

```bash
git clone https://github.com/BigBoySean/canonical-naming-service.git
cd canonical-naming-service

python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

make install
make test                           # runs fully offline, no API key needed
make run                            # http://localhost:8000  •  docs at /docs
```

`make test` and the running server both work with **no API key**: the cascade runs in 3-stage mode (exact + fuzzy + `needs_review`) and the LLM tier is a no-op. The LLM tier is opt-in — see below.

## Enabling the LLM tier (optional)

```bash
cp .env.example .env
# edit .env:
#   ANTHROPIC_API_KEY=sk-ant-...
#   LLM_ENABLED=true
```

With these set, the cascade adds a fourth tier — Anthropic `claude-haiku-4-5` is asked to disambiguate when exact + fuzzy can't decide. The cascade always reaches the LLM *last*, so cost stays bounded to the residual hard cases.

The service is fully functional without an API key — the LLM tier raises accuracy on inputs *beyond* well-aliased names (novel abbreviations, paraphrases). For the brief's own ten sample inputs, the deterministic tiers handle every resolvable case on their own.

## API

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/resolve` | Resolve a single raw name. Returns canonical id, name, confidence, match method, and a `needs_review` flag. |
| `POST` | `/resolve/batch` | Resolve a list of raw names in one call. Sequential v1; results in input order. |
| `GET` | `/entities/{id}` | Retrieve a canonical entity with all its known aliases. |
| `POST` | `/entities` | Register a new canonical entity. Returns 201 Created. |
| `GET` | `/health` | Standard health probe. |

Full OpenAPI documentation lives at `http://localhost:8000/docs` once the server is running.

## Example requests

The curl snippets below are real — they were captured against this service running with `LLM_ENABLED=false` on the published commit.

**Resolve a name (matches exactly via an alias):**

```bash
$ curl -s -X POST http://localhost:8000/resolve \
    -H 'Content-Type: application/json' \
    -d '{"raw_name":"BCP VIII (USD)"}'
```
```json
{
  "raw_name": "BCP VIII (USD)",
  "canonical_id": "ent_blackstone_cp_viii",
  "canonical_name": "Blackstone Capital Partners VIII, L.P.",
  "confidence": 1.0,
  "method": "exact",
  "needs_review": false
}
```

**Needs-review (planted: `Fund XI` is absent from the seed by design):**

```bash
$ curl -s -X POST http://localhost:8000/resolve \
    -H 'Content-Type: application/json' \
    -d '{"raw_name":"Hellman & Friedman Capital Partners XI"}'
```
```json
{
  "raw_name": "Hellman & Friedman Capital Partners XI",
  "canonical_id": null,
  "canonical_name": null,
  "confidence": 0.0,
  "method": "none",
  "needs_review": true
}
```

The seed has `Hellman & Friedman Capital Partners X` (vintage 10), not XI. The numeral guard in the fuzzy tier refuses to bridge across vintages — see "How it works" below.

**Batch resolve:**

```bash
$ curl -s -X POST http://localhost:8000/resolve/batch \
    -H 'Content-Type: application/json' \
    -d '{"raw_names":["BCP VIII (USD)","Apollo Inv. Fund IX","Hellman & Friedman Capital Partners XI"]}'
```
```json
{
  "results": [
    {"raw_name":"BCP VIII (USD)","canonical_id":"ent_blackstone_cp_viii","canonical_name":"Blackstone Capital Partners VIII, L.P.","confidence":1.0,"method":"exact","needs_review":false},
    {"raw_name":"Apollo Inv. Fund IX","canonical_id":"ent_apollo_investment_ix","canonical_name":"Apollo Investment Fund IX, L.P.","confidence":1.0,"method":"exact","needs_review":false},
    {"raw_name":"Hellman & Friedman Capital Partners XI","canonical_id":null,"canonical_name":null,"confidence":0.0,"method":"none","needs_review":true}
  ]
}
```

**Get an entity:**

```bash
$ curl -s http://localhost:8000/entities/ent_blackstone_cp_viii
```
```json
{
  "canonical_id": "ent_blackstone_cp_viii",
  "canonical_name": "Blackstone Capital Partners VIII, L.P.",
  "aliases": ["BCP VIII","Blackstone Cap. Partners 8","BX Cap Partners VIII","Blackstone Capital Partners 8","BCP 8"]
}
```

**Register a new entity, then resolve a name that hits it:**

```bash
$ curl -s -X POST http://localhost:8000/entities \
    -H 'Content-Type: application/json' \
    -d '{"canonical_name":"Acme Capital Partners II, L.P.","aliases":["Acme CP II","ACP II"]}'
```
```json
{
  "canonical_id": "ent_acme_capital_partners_ii_l_p",
  "canonical_name": "Acme Capital Partners II, L.P.",
  "aliases": ["Acme CP II","ACP II"]
}
```
```bash
$ curl -s -X POST http://localhost:8000/resolve \
    -H 'Content-Type: application/json' \
    -d '{"raw_name":"Acme CP II"}'
```
```json
{
  "raw_name": "Acme CP II",
  "canonical_id": "ent_acme_capital_partners_ii_l_p",
  "canonical_name": "Acme Capital Partners II, L.P.",
  "confidence": 1.0,
  "method": "exact",
  "needs_review": false
}
```

The newly-registered entity is immediately resolvable in the same process. Matcher index caches are invalidated on every `POST /entities` so this round-trip works without a restart.

**Health:**

```bash
$ curl -s http://localhost:8000/health
{"status":"ok","version":"0.1.0"}
```

## How it works

Resolution is a four-tier cascade with early exit at every stage:

1. **Normalise** — `unidecode` for diacritics, lowercase, vehicle / currency qualifier extraction (`(USD)`, `(Cayman Feeder)`, `- Parallel Vehicle`), legal-suffix strip-and-record (`L.P.`, `LLC`, `SCSp`, `GmbH & Co. KG`, `S.C.A.`), series-marker preservation (`No 1`), Roman → Arabic numeral conversion using a whole-token validator (so `Vista` is never mistaken for a Roman numeral), `&` → `and`, then punctuation and whitespace cleanup. Abbreviations like `BCP` and `KKR` are *not* expanded here — they collide across entities (`BCP` appears for Blackstone *and* in Brookfield's `BCP VI Brookfield`), so expansion happens later via catalogue context.
2. **Exact** — O(1) hash lookup against the normalised forms of every canonical name and every alias. Sub-millisecond, free.
3. **Fuzzy** — `rapidfuzz.token_set_ratio` against the same index, threshold 92. **Numeral guard:** before accepting a fuzzy match, the fund vintage is extracted from both query and candidate; if both have a numeral and they differ, the matcher refuses to bridge — `Fund XI` and `Fund X` are different entities regardless of how high their string similarity is. This is the cleverest single design decision in the service and the reason the brief's `Hellman & Friedman XI` distractor falls through correctly instead of false-matching to `Hellman & Friedman X`.
4. **LLM** — Anthropic `claude-haiku-4-5` is given the top-K fuzzy candidates plus a system prompt that codifies the same "vintages are identity" rule and asks for strict JSON output. Defensive parsing turns any malformed response into `None` so the cascade never crashes. The numeral guard re-runs against the LLM's chosen candidate (defence in depth — the prompt should already have prevented it, but the code makes it categorically impossible). Disabled by default.
5. **`needs_review`** — if every tier declines, the response carries `needs_review: true` and `canonical_id: null`. This is a normal 200 OK outcome, not a 4xx — declining is the cascade's deliberate answer for genuinely unknown inputs and is the on-ramp to catalogue maintenance (resolve → miss → review → register via `POST /entities` → resolvable).

See [`DECISIONS.md`](./DECISIONS.md) for the full Decision / Alternatives / Why-rejected breakdown of every choice.

## Architecture

Layered, each layer depending only downward:

```
src/canonical_naming/
├── api/             FastAPI routers — HTTP boundary only
│   ├── health.py
│   ├── resolve.py
│   ├── entities.py
│   └── deps.py      DI factory (lru_cache singleton for ResolverService)
├── services/
│   └── resolver.py  4-tier cascade + needs_review + register_entity
├── matching/
│   ├── normalizer.py    8-step pipeline -> NormalizedName
│   ├── base.py          Matcher protocol (runtime_checkable)
│   ├── exact.py         ExactMatcher
│   ├── fuzzy.py         FuzzyMatcher + numeral guard
│   └── llm.py           LLMMatcher (Anthropic Haiku 4.5)
├── repos/
│   └── entity_repo.py   EntityRepo protocol + InMemoryEntityRepo
├── models/
│   ├── entity.py        Entity (frozen)
│   ├── matching.py      MatchMethod, MatchResult, NormalizedName
│   └── api.py           Resolve/Create request and response models
├── config.py        pydantic-settings, env-driven
└── main.py          FastAPI app factory
```

## Testing

```bash
make test            # 135 tests, fully offline, no API key
make coverage        # 94% total coverage
make lint            # ruff
```

Live LLM tests (two of them) skip cleanly when `ANTHROPIC_API_KEY` is unset, so the suite stays green in CI without credentials. With the key present, they run end-to-end against real `claude-haiku-4-5`.

## Project structure

```
canonical-naming-service/
├── data/seed.json                  20 synthetic canonical entities + aliases
├── src/canonical_naming/           service code (layout above)
├── tests/                          135 tests: unit + integration + brief samples
├── DECISIONS.md                    design rationale & trade-offs
├── Makefile                        install / run / test / coverage / lint
├── pyproject.toml                  deps, ruff/pytest config
├── .env.example                    config knobs (LLM_ENABLED, threshold, ...)
└── README.md                       this file
```
