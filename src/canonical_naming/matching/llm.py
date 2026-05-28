"""LLM disambiguation tier (cascade stage 4) — Anthropic Claude Haiku 4.5.

This tier is invoked only when the deterministic stages (exact, fuzzy)
don't produce a confident result. The LLM receives the top-K fuzzy
candidates as context and is asked to pick one or decline. It is
specifically the tier where abbreviation-and-context judgement happens
(BCP -> Blackstone, KKR -> Kohlberg Kravis Roberts) — judgement that
character-similarity scoring can't make safely on its own.

Design highlights:

- **Offline-first.** `llm_enabled=False` is the default. Tests, CI, and
  development setups never touch the network unless explicitly enabled.
  Returning `None` early at the top of `match()` means no client is
  constructed, no API key is read, no logs are emitted at WARNING.
- **Defensive parsing.** A malformed LLM response (non-JSON, missing
  fields, wrong types, code-fence wrapping) returns `None`, never raises.
  The cascade is allowed to fail; it is never allowed to crash on
  upstream malformed output.
- **Numeral guard, again.** The system prompt instructs the LLM that
  fund vintages are identity-bearing. We *also* enforce the rule in code
  after parsing: if the LLM's chosen candidate's fund numeral disagrees
  with the input's, we override to `None`. Defence in depth — the prompt
  reduces the chance the LLM gets it wrong; the code makes it impossible
  for that specific failure mode to slip through.
- **Single retry on transient errors.** Network blips happen. Two
  attempts is enough; further retries trade latency for unlikely
  recovery and the cascade can fall through to `needs_review` cheaply.
"""

import json
import logging
from typing import Final

from anthropic import Anthropic
from pydantic import BaseModel, Field, ValidationError
from rapidfuzz import fuzz

from canonical_naming.config import Settings, get_settings
from canonical_naming.matching._index import build_normalised_index
from canonical_naming.matching.fuzzy import _extract_fund_numeral
from canonical_naming.matching.normalizer import normalize
from canonical_naming.models import MatchMethod, MatchResult, NormalizedName
from canonical_naming.repos.entity_repo import EntityRepo

logger = logging.getLogger(__name__)

# How many fuzzy-scored candidates to show the LLM.
_TOP_K: Final[int] = 5
# Minimum fuzzy score for a candidate to be worth asking about. Anything
# below this is so dissimilar that no LLM judgement could bridge it.
_CANDIDATE_FLOOR: Final[int] = 50
# Minimum LLM-reported confidence to accept its chosen candidate.
_ACCEPT_FLOOR: Final[float] = 0.70
# Output is short structured JSON — a low ceiling discourages prose.
_MAX_TOKENS: Final[int] = 256


SYSTEM_PROMPT = (
    "You are resolving a raw private-equity fund / partnership name to a "
    "canonical entity from a small candidate list.\n"
    "\n"
    "CRITICAL RULES:\n"
    "\n"
    "1. Fund vintage numerals are IDENTITY, not noise. \"Fund VIII\" (= 8), "
    "\"Fund IX\" (= 9), \"Fund X\" (= 10), and \"Fund XI\" (= 11) are "
    "DIFFERENT entities — different vintages of the same GP are not the "
    "same fund. NEVER match across different fund numbers. If the raw "
    "name's fund vintage does not match a candidate's fund vintage "
    "exactly, that candidate is WRONG and you must not choose it.\n"
    "\n"
    "2. Abbreviations and word reorderings ARE expected and should be "
    "bridged. BCP = Blackstone Capital Partners. KKR = Kohlberg Kravis "
    "Roberts. AIF = Apollo Investment Fund. CVC, EQT, Carlyle, Apax, "
    "Permira, etc. are real PE houses. Bridging an abbreviation when the "
    "vintage matches is exactly the kind of judgement you are here to make.\n"
    "\n"
    "3. If no candidate is the same real-world entity as the raw name, "
    "return canonical_id: null. Declining is the correct answer for "
    "unknown entities. Never force a match because a candidate looks "
    "superficially similar.\n"
    "\n"
    "OUTPUT: respond with strict JSON only — no prose before or after, "
    "no markdown code fences. Schema:\n"
    "\n"
    "{\n"
    '  "canonical_id": "<one of the candidate ids, or null>",\n'
    '  "confidence": <float between 0.0 and 1.0>,\n'
    '  "reasoning": "<one short sentence justifying the choice or the refusal>"\n'
    "}"
)


class LLMMatcherResponse(BaseModel):
    """Structured shape we expect the LLM to return."""

    canonical_id: str | None
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str


class LLMMatcher:
    """LLM-based disambiguation tier."""

    def __init__(
        self,
        settings: Settings | None = None,
        client: Anthropic | None = None,
    ) -> None:
        self._settings = settings if settings is not None else get_settings()
        # Client is injected for tests; constructed lazily in production so
        # the disabled-path costs zero (no client, no API-key read).
        self._client = client
        self._index_cache: dict[int, list[tuple[str, str, str]]] = {}

    # --- Internal helpers --------------------------------------------------

    def _get_index(self, repo: EntityRepo) -> list[tuple[str, str, str]]:
        rid = id(repo)
        cached = self._index_cache.get(rid)
        if cached is not None:
            return cached
        index = build_normalised_index(repo)
        self._index_cache[rid] = index
        return index

    def _get_client(self) -> Anthropic:
        if self._client is None:
            self._client = Anthropic(
                api_key=self._settings.anthropic_api_key,
                timeout=float(self._settings.llm_timeout_seconds),
            )
        return self._client

    def _top_k_candidates(
        self, query: str, repo: EntityRepo
    ) -> list[tuple[str, str]]:
        """Top-K `(canonical_id, canonical_name)` by fuzzy score, deduped
        so each entity appears at most once even if multiple of its
        aliases score above the floor."""
        scored: list[tuple[float, str, str]] = []
        for key, cid, cname in self._get_index(repo):
            score = fuzz.token_set_ratio(query, key)
            if score < _CANDIDATE_FLOOR:
                continue
            scored.append((score, cid, cname))
        scored.sort(key=lambda x: x[0], reverse=True)

        seen: set[str] = set()
        deduped: list[tuple[str, str]] = []
        for _score, cid, cname in scored:
            if cid in seen:
                continue
            seen.add(cid)
            deduped.append((cid, cname))
            if len(deduped) >= _TOP_K:
                break
        return deduped

    def _call_with_retry(self, user_message: str) -> str | None:
        client = self._get_client()
        last_exc: Exception | None = None
        for attempt in range(2):
            try:
                response = client.messages.create(
                    model=self._settings.llm_model,
                    max_tokens=_MAX_TOKENS,
                    system=SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": user_message}],
                )
            except Exception as exc:
                last_exc = exc
                if attempt == 0:
                    logger.info("LLM call failed (attempt 1), retrying: %s", exc)
                    continue
                logger.warning("LLM call failed after retry: %s", exc)
                return None
            for block in response.content:
                if getattr(block, "type", None) == "text":
                    return block.text
            logger.warning("LLM response had no text block")
            return None
        logger.warning("LLM call failed after retry: %s", last_exc)
        return None

    @staticmethod
    def _parse_response(text: str) -> LLMMatcherResponse | None:
        # Strip a markdown code-fence wrapper if the LLM produced one despite
        # the instruction. We accept ```json ... ``` and ``` ... ```.
        stripped = text.strip()
        if stripped.startswith("```"):
            lines = stripped.split("\n")
            stripped = "\n".join(lines[1:-1]).strip()
        try:
            data = json.loads(stripped)
            return LLMMatcherResponse(**data)
        except (json.JSONDecodeError, ValidationError, TypeError) as exc:
            logger.warning(
                "LLM response failed to parse: %s; text=%r",
                exc, text[:200],
            )
            return None

    # --- Matcher protocol --------------------------------------------------

    def match(
        self,
        normalized: NormalizedName,
        repo: EntityRepo,
    ) -> MatchResult | None:
        if not self._settings.llm_enabled:
            logger.debug("LLM tier disabled; skipping")
            return None

        query = normalized.normalized
        if not query:
            return None

        candidates = self._top_k_candidates(query, repo)
        if not candidates:
            logger.debug(
                "no candidates above floor=%d for %r; LLM tier skipped",
                _CANDIDATE_FLOOR, query,
            )
            return None

        candidate_lines = "\n".join(
            f'- {cid}: "{cname}"' for cid, cname in candidates
        )
        user_message = (
            f'Raw name: "{normalized.original}"\n'
            f'Normalized form: "{query}"\n\n'
            f"Candidates:\n{candidate_lines}\n\n"
            "Which candidate (if any) is the same real-world entity as the raw name?"
        )

        response_text = self._call_with_retry(user_message)
        if response_text is None:
            return None

        parsed = self._parse_response(response_text)
        if parsed is None:
            return None

        if parsed.canonical_id is None or parsed.confidence < _ACCEPT_FLOOR:
            logger.debug(
                "LLM declined or below floor: id=%r conf=%.2f reasoning=%r",
                parsed.canonical_id, parsed.confidence, parsed.reasoning,
            )
            return None

        # Look up the entity the LLM chose so we can return its canonical name
        # and also run the numeral guard against its normalised form.
        entity = repo.get(parsed.canonical_id)
        if entity is None:
            logger.warning(
                "LLM chose canonical_id %r not in repo — refusing",
                parsed.canonical_id,
            )
            return None

        cand_key = normalize(entity.canonical_name).normalized
        query_num = _extract_fund_numeral(query)
        cand_num = _extract_fund_numeral(cand_key)
        if (
            query_num is not None
            and cand_num is not None
            and query_num != cand_num
        ):
            logger.info(
                "LLM numeral-guard override: input fund=%d, %s fund=%d "
                "(LLM said conf=%.2f) — refusing",
                query_num, parsed.canonical_id, cand_num, parsed.confidence,
            )
            return None

        logger.info(
            "LLM accepted: %s conf=%.2f reasoning=%r",
            parsed.canonical_id, parsed.confidence, parsed.reasoning,
        )
        return MatchResult(
            canonical_id=parsed.canonical_id,
            canonical_name=entity.canonical_name,
            confidence=parsed.confidence,
            method=MatchMethod.LLM,
            needs_review=False,
        )
