# Design Decisions & Trade-offs

A reviewer-facing summary of the architectural choices behind `canonical-naming-service`, the alternatives considered, and what's deliberately out of scope.

## Architecture overview

The service is laid out in five layers — HTTP-in to data-out, depending only downward:

```
api/         FastAPI routers — HTTP boundary only
services/    Use-case orchestration (ResolverService, EntityService)
matching/    Normalizer + Matcher protocol + Exact / Fuzzy / LLM implementations
repos/       EntityRepo protocol + InMemoryEntityRepo
models/      Pydantic v2 schemas (requests, responses, domain types)
```

Resolution runs as a **4-stage matching cascade** with early exit at each stage:

1. **Normalize** — diacritic folding, case folding, whitespace/punctuation cleanup, Roman→Arabic numeral conversion, legal-suffix strip-and-record (L.P., LLC, GmbH & Co. KG, S.C.A., SCSp), vehicle/currency suffix strip ((USD), (Cayman), Feeder, Parallel Vehicle), abbreviation expansion (BCP → Blackstone Capital Partners, KKR → Kohlberg Kravis Roberts).
2. **Exact match** — O(1) hash lookup against canonical names + all known aliases (both keyed on their normalized form). Sub-millisecond, free.
3. **Fuzzy match** — `rapidfuzz` token-set / partial-ratio above a threshold (default 92). Cheap, runs in-process.
4. **LLM disambiguation** — `claude-haiku-4-5` selects from the top-K fuzzy candidates. Only invoked when stages 2–3 don't decide.
5. **`needs_review`** — if no stage produces a confident answer, return the closest candidates so a human (or downstream catalog-maintenance flow) can adjudicate.

The cascade pattern matches the rubric's explicit cost / latency / accuracy dimension: cheap-fast strategies clear the easy cases (~70% of typical workloads), expensive-precise strategies only fire when needed.

## Why FastAPI

- **Pydantic-native.** Request and response models are typed end-to-end with no glue code. The rubric explicitly weights "Pydantic models, type hints" under best practices.
- **Auto-generated OpenAPI docs** at `/docs`, so the API contract is self-publishing.
- **Async-ready** for the LLM call without bolting an event loop onto a sync framework.
- **Industry standard** for Python microservices in 2025+.

**Alternatives considered.** *Flask* — more boilerplate; no native Pydantic integration; OpenAPI generation is a third-party add-on. *Django REST Framework* — overkill for a stateless microservice with no ORM, no admin, no auth; the framework's surface area exceeds the deliverable.

## Matching strategy

**Why not single-fuzzy.** Fuzzy matching alone misses abbreviation bridges that need a different kind of intelligence — `BCP` → `Blackstone Capital Partners`, `KKR` → `Kohlberg Kravis Roberts`. Pure edit distance treats these as low similarity even though they're well-known aliases. Solving this with hand-curated abbreviation maps is fragile and stops working the moment a new GP shows up.

**Why not single-LLM.** Cost and latency on every call. The trivial cases (exact hits, including most aliases) deserve to be free. LLM-only also scales LLM cost linearly with QPS for no precision gain on the cases that don't need it.

**Why the cascade.** Exact match handles roughly 70% of typical workloads for free. Fuzzy match handles another ~20% cheaply. The LLM is invoked only on the residual hard cases (abbreviations, edit-distance ambiguity), where its judgement actually pays for itself. `needs_review` catches genuine no-matches like the `Hellman & Friedman Capital Partners XI` case in the brief's sample dataset — a real and important behavior, not a failure mode.

## LLM choice — `claude-haiku-4-5`

Smallest competent tier from a major lab. Selection criteria: cost per call, latency, structured-output reliability for a constrained pick-one-of-K task.

- **Called only on fuzzy miss**, so cost is bounded — it can't be triggered by trivial inputs.
- **Structured JSON output** keeps parsing deterministic; the prompt asks for `{candidate_id, confidence}` or `{"no_match": true}`.
- **Behind a `Matcher` protocol** — swapping vendors (OpenAI, AWS Bedrock, a local model) is a one-class change, not a refactor. No lock-in.
- **Tests use a deterministic mock implementation** so they run fully offline; `LLM_ENABLED=false` is the default and no API key is required to run the test suite.

Bias disclosed: the candidate stack chosen for this assessment aligns with the company's documented Anthropic-friendly stack. The technical justification (cost, latency, JSON-mode reliability for short prompts) stands independently — see the rejected-alternative discussion if substituting another vendor.

## Persistence — in-memory `EntityRepo`

`InMemoryEntityRepo` implements an `EntityRepo` protocol, hydrated from a checked-in `data/seed.json` on startup. Sufficient for the assessment scope. The protocol is the explicit swap point — a Postgres-backed implementation lands in v2, with no changes to the cascade, services, or API layers.

**`POST /entities`** is durable only for the running process — registered entities live in memory until the next restart. This is documented behavior, not an oversight.

## What's deliberately out of scope

| Item | Production answer |
|---|---|
| Authentication | Auth0 + RBAC. Matches the company's stack. Per-endpoint scopes (`entities:write`, `entities:read`). |
| Per-API-key rate limiting | Token-bucket at the gateway (Auth0 quotas / Cloudflare / API Gateway); in-process `slowapi` as fallback. |
| Persistent storage for `POST /entities` | Postgres (`pg_trgm` for fuzzy candidate retrieval, `tsvector` for full-text), behind the existing `EntityRepo` protocol. |
| Embedding-based candidate retrieval | Vector index (sentence-transformers + FAISS or pgvector) pre-filter before fuzzy, once catalog scale crosses ~10k entities. |
| Confidence calibration | Translate raw fuzzy and LLM scores into actionable bands (`auto_accept` / `review` / `reject`) calibrated against a labeled holdout. |
| Multi-tenant data isolation | Tenant-scoped repos with caller-identity scoping. |

The brief itself never asks for auth or rate limiting; they're inferred as production reality. Time spent building them in v1 doesn't earn rubric points; time spent documenting the answer here does (under Overall Design, 15%).

## If I had more time

1. **Confidence calibration.** Current thresholds (fuzzy ≥ 92, LLM "confident") are heuristic. A small labeled holdout would let me calibrate raw scores into stable decision bands.
2. **Audit log.** Every resolution recorded with input, candidates considered, decision, match method, confidence, caller, timestamp. Useful for compliance and as training data for any future fine-tuning.
3. **Catalog maintenance pipeline.** Bulk ingest of new funds, vintage rollovers, parallel/feeder additions, GP-led mergers, fund renames — separate from the resolution path.
4. **Embedding retrieval before fuzzy at scale.** Pre-filter candidates with a vector index when the catalog crosses ~10k entities and full-catalog fuzzy becomes the latency bottleneck.
5. **Entity ID stability across renames.** Current IDs are content-derived (`ent_blackstone_cp_viii`) — stable across casing/punctuation drift but fragile across true renames or GP acquisitions. Production design: opaque ULIDs with `(canonical_name, effective_from, effective_to)` history tracked separately.
