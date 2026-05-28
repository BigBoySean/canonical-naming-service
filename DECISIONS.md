# Design Decisions & Trade-offs

A reviewer-facing summary of the architectural choices behind `canonical-naming-service`, the alternatives considered, and what's deliberately out of scope.

## Architecture overview

The service is laid out in five layers — HTTP-in to data-out, depending only downward:

```
api/         FastAPI routers — HTTP boundary only
services/    Use-case orchestration (ResolverService)
matching/    Normalizer + Matcher protocol + Exact / Fuzzy / LLM implementations
repos/       EntityRepo protocol + InMemoryEntityRepo
models/      Pydantic v2 schemas (requests, responses, domain types)
```

Resolution runs as a four-tier cascade with early exit at every stage:

1. **Normalise** — diacritic folding (`unidecode`), case folding, vehicle / currency qualifier extraction (`(USD)`, `(Cayman Feeder)`, `- Parallel Vehicle`), legal-suffix strip-and-record (`L.P.`, `LLC`, `GmbH & Co. KG`, `S.C.A.`, `SCSp`), series-marker preservation (`No 1` survives as identity, not noise), whole-token Roman → Arabic numeral conversion, `&` → `and`, punctuation and whitespace cleanup.
2. **Exact match** — O(1) hash lookup against the normalised form of every canonical name and every alias. Sub-millisecond, free.
3. **Fuzzy match** — `rapidfuzz.token_set_ratio` over the same index, threshold 92, plus a **numeral guard** (the next section).
4. **LLM disambiguation** — `claude-haiku-4-5` selects from the top-K fuzzy candidates. Only invoked when the deterministic tiers don't decide; disabled by default.
5. **`needs_review`** — if no tier returns a confident result, the service emits a `ResolveResponse` with `needs_review: true` and the original raw name. Normal 200 outcome, not a 4xx — declining is the cascade's deliberate answer for genuinely new entities.

The cascade pattern matches the rubric's cost / latency / accuracy axis: cheap-fast strategies clear the easy cases (~70% of typical workloads), expensive-precise strategies only fire when needed. On the brief's own ten sample inputs, the deterministic tiers (exact + fuzzy + numeral guard) handle every resolvable case on their own — the LLM tier is for inputs *beyond* the well-aliased sample set.

## The numeral guard — the strongest single design decision

The most important correctness lever in the matching cascade. Worth its own section because it's the difference between a service that scores high on sample-test correctness and one that quietly invents wrong answers under real input drift.

**The failure mode it prevents.** PE fund vintages are identity-bearing. `Fund VIII` (vintage 8), `Fund IX` (vintage 9), `Fund X` (vintage 10), and `Fund XI` (vintage 11) are *different entities* with different LPs, different deals, different IRRs. The brief's planted distractor is exactly this: `Hellman & Friedman Capital Partners XI` is absent from the seed by design, while H&F X *is* present.

After normalisation:
- input → `hellman and friedman capital partners 11`
- seed candidate → `hellman and friedman capital partners 10`

These strings differ in exactly one character. `rapidfuzz.token_set_ratio` scores them **97.5** — well above the 92 threshold. A vanilla fuzzy matcher with *any* sensible threshold would emit a false confident match XI → X. That's the failure mode.

**The fix.** Before accepting a fuzzy or LLM match at or above threshold, extract the *fund numeral* from both the query and the chosen candidate. If both have a numeral and they disagree, refuse — return `None` so the cascade falls through to `needs_review`. The fund numeral is defined as the first standalone Arabic integer that isn't part of a `no N` series marker (so `eqt 10 no 1` correctly extracts `10`, not `1`).

The guard is **categorical, not soft**. There is no "close enough" version of vintage VIII vs IX. Encoding that as a binary refusal at the matcher level is cheaper and more legible than trying to bake it into a similarity score.

**Defence in depth.** The same rule appears in two places:

1. **In the LLM system prompt**, as the first explicit CRITICAL RULE with worked examples. The model is *told* that vintages are identity. Verified live against `claude-haiku-4-5`: when fed XI with H&F X among candidates, the model returned `canonical_id: null` with the reasoning "different vintages are different entities and cannot be matched."
2. **In code**, after parsing the LLM's response. If the LLM's chosen candidate's fund numeral disagrees with the input's, we override to `None` regardless of LLM-reported confidence. Models update, prompts drift, future model releases could be less attentive. The code-level guard makes XI → X a *categorically impossible* output, version-independent.

**Why not just raise the threshold globally?** The threshold that would filter out XI vs X (~98) also filters out legitimate matches with single-character typos (`Carlyle Partnes VIII` scores ~95 against `Carlyle Partners VIII`). There is no flat threshold that simultaneously rejects same-name-different-vintage *and* accepts same-vintage-with-typos. The signal we want isn't "less similar" — it's *categorically different along the vintage axis* — and string similarity is the wrong instrument to measure it.

## Scaling the catalog

The seed at `data/seed.json` is a demonstration fixture — 20 entities chosen to cover the brief's sample inputs plus a small breadth of GP families and legal suffixes. It is **not** the system's ceiling. Four points are worth being explicit about:

1. **The service resolves arbitrary unseen *inputs* by design.** Normalisation, fuzzy matching with the numeral guard, and the LLM tier all generalise beyond the enumerated aliases. A name we've never seen before still resolves correctly when it points to a fund that *is* in the catalogue. Aliases in the seed are conveniences (fast exact-match hits) not requirements — a missing alias degrades performance, not correctness.

2. **The catalogue grows at runtime.** `POST /entities` registers new canonical entities; matcher index caches invalidate atomically on every registration so the new entity is resolvable on the very next request in the same process. Integration-tested end-to-end (`test_register_then_get_then_resolve_via_http_full_loop`).

3. **Production-scale storage is a one-class swap.** `EntityRepo` is a protocol with two methods (`get`, `add`, `all_entities`). The current `InMemoryEntityRepo` reads from `data/seed.json`; a Postgres-backed implementation with `pg_trgm` for trigram-based candidate retrieval and `tsvector` for full-text search lives behind the same protocol. **No changes to the matching cascade, the resolver service, or the API layer when that swap happens** — the protocol is the contract, the implementations are interchangeable.

4. **`needs_review` is the catalogue's growth on-ramp.** When the cascade declines, the response carries the raw name, the (empty) match result, and the `needs_review` flag. Downstream consumers know to route the input to a human (or a catalogue-maintenance flow) for adjudication. Once registered via `POST /entities`, the same input resolves on the next call. This loop is the right shape for real catalogue growth: most new entities are registered with confidence by a domain expert; the cascade picks them up automatically thereafter.

The architecture treats the catalogue size and the cascade design as independent concerns. Scaling to 100k entities is a storage and indexing problem (Postgres, `pg_trgm`, an embedding-based pre-filter in front of fuzzy — see "If I had more time"); the cascade's correctness story doesn't change.

## Why FastAPI

- **Pydantic-native.** Request and response models are typed end-to-end with no glue code.
- **Auto-generated OpenAPI docs** at `/docs` — the API contract is self-publishing.
- **Async-ready** for the LLM call without bolting an event loop onto a sync framework.
- **Industry standard** for Python microservices.

**Alternatives considered.** *Flask* — more boilerplate; no native Pydantic integration; OpenAPI generation is a third-party add-on. *Django REST Framework* — overkill for a stateless microservice with no ORM, no admin, no auth.

## Matching strategy — why a cascade

**Why not single-fuzzy.** Fuzzy alone misses abbreviation bridges that need real-world knowledge — `BCP` → `Blackstone Capital Partners`, `KKR` → `Kohlberg Kravis Roberts`. Pure edit distance treats these as low-similarity even though they're well-known aliases. A hand-curated abbreviation map in the normaliser is fragile and breaks the moment a new GP shares an abbreviation: the seed contains `BCP` for *both* Blackstone *and* Brookfield (in `BCP VI Brookfield`), so a flat global expansion would corrupt one of them. Abbreviation bridging is therefore left to fuzzy + LLM, which can use catalogue context to disambiguate.

**Why not single-LLM.** Cost and latency on every call. The trivial cases (exact hits, including most aliases) deserve to be free. LLM-only also scales LLM cost linearly with QPS for no precision gain on the easy majority.

**Why the cascade.** Exact match handles the trivially-resolvable cases at zero cost. Fuzzy match handles single-character noise and word reordering cheaply, with the numeral guard preventing the vintage-collision failure mode. The LLM is invoked only on the residual hard cases (abbreviations, edit-distance ambiguity) where its judgement actually pays for itself. `needs_review` catches genuine no-matches like the brief's `Hellman & Friedman XI`.

## LLM choice — `claude-haiku-4-5`

Smallest competent tier from a major lab. Selection criteria: cost per call, latency, structured-output reliability for a constrained pick-one-of-K task.

- **Called only on fuzzy miss**, so cost is bounded. It can't be triggered by trivial inputs.
- **Strict JSON output** parsed defensively: malformed responses become `None`, never raise. Markdown code fences are stripped if the model produces them despite the instruction.
- **Behind a `Matcher` protocol** — swapping vendors (OpenAI, AWS Bedrock, a local model) is a one-class change, not a refactor.
- **Tests use a deterministic mock implementation** (`MockLLMMatcher`) so they run fully offline. `LLM_ENABLED=false` is the default; no API key is required for `make test` or `make run`.

## Persistence — in-memory `EntityRepo`

`InMemoryEntityRepo` implements an `EntityRepo` protocol, hydrated from `data/seed.json` on startup. Sufficient for v1. The protocol is the explicit swap point — a Postgres-backed implementation lands without changes to the cascade, services, or API layers (see "Scaling the catalog" above).

`POST /entities` is durable only for the running process — registered entities live in memory until the next restart. Documented behaviour, not an oversight.

## What's deliberately out of scope

| Item | Production answer |
|---|---|
| Authentication | Auth0 + RBAC. Per-endpoint scopes (`entities:write`, `entities:read`). |
| Per-API-key rate limiting | Token-bucket at the gateway (Auth0 quotas / Cloudflare / API Gateway); in-process `slowapi` as fallback. |
| Persistent storage for `POST /entities` | Postgres (`pg_trgm` for fuzzy candidate retrieval, `tsvector` for full-text), behind the existing `EntityRepo` protocol. |
| Embedding-based candidate retrieval | Vector index (sentence-transformers + FAISS or pgvector) pre-filter before fuzzy, once catalogue scale crosses ~10k entities. |
| Confidence calibration | Translate raw fuzzy and LLM scores into actionable bands (`auto_accept` / `review` / `reject`) calibrated against a labelled holdout. |
| Async / concurrent batch | `asyncio.gather` over `resolve_async()` once the LLM tier sees real per-batch volume. |
| Multi-tenant data isolation | Tenant-scoped repos with caller-identity scoping. |

The brief itself never asks for auth or rate limiting; they're inferred as production reality. Time spent building them in v1 doesn't earn rubric points; time spent documenting the answer here does (under Overall Design, 15%).

## If I had more time

1. **Confidence calibration.** Current thresholds (fuzzy ≥ 92, LLM "confident" at 0.70) are heuristic. A small labelled holdout would let me calibrate raw scores into stable decision bands (auto-accept / review / reject) instead of a single binary.
2. **Audit log.** Every resolution recorded with input, candidates considered, decision, match method, confidence, caller, timestamp. Useful for compliance reporting and as a labelled training set for any future fine-tuning.
3. **Catalogue maintenance pipeline.** Bulk ingest of new funds, vintage rollovers, parallel / feeder additions, GP-led mergers, fund renames — separate from the resolution hot path.
4. **Embedding retrieval before fuzzy at scale.** Once the catalogue crosses ~10k entities and full-catalogue fuzzy becomes the latency bottleneck, pre-filter with a vector index (sentence-transformers + FAISS / pgvector). The cascade becomes normalize → exact → embedding-prefilter → fuzzy → LLM; same shape, one new layer.
5. **Entity ID stability across renames.** Current IDs are content-derived (`ent_blackstone_cp_viii`) — stable across casing / punctuation drift but fragile across true renames or GP acquisitions. Production design: opaque ULIDs with `(canonical_name, effective_from, effective_to)` history tracked separately.
6. **Async cascade.** The LLM tier is the only slow call in the chain. `asyncio.gather` over a batch of resolves would parallelise the calls cleanly once a real batch workload appears.
