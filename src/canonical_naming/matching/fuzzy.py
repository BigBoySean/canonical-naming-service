"""Fuzzy-match tier — `rapidfuzz` `token_set_ratio` with a numeral guard.

The fuzzy tier handles inputs that survive normalisation but don't exact-key
into the catalogue: typos, word-order shuffles, partial token overlap.
`rapidfuzz.fuzz.token_set_ratio` is the scorer because the PE-naming domain
is dominated by token reordering and partial subset matches (e.g.
`BCP VI Brookfield` vs `Brookfield Capital Partners VI`) rather than
character-level transpositions where pure `ratio` would shine.

The numeral guard is the critical correctness feature. Without it, two
PE fund names that differ only in their vintage number score ~97 on
token_set_ratio — they share every token except the digit — and the
fuzzy tier would happily emit a false confident match. With the guard,
any fuzzy candidate whose fund numeral disagrees with the input's is
refused: the cascade falls through to the LLM tier (or `needs_review`),
which is the brief's intended behaviour for the planted
`Hellman & Friedman XI` case (XI is absent from the seed; the seed has
H&F X).

The guard is intentionally *categorical*, not soft. There is no
"close enough" version of fund vintage: VIII and IX are different
entities, full stop. Encoding that as a binary refusal at the matcher
level is cheaper and more legible than trying to bake it into a
similarity score.
"""

import logging

from rapidfuzz import fuzz

from canonical_naming.matching._index import build_normalised_index
from canonical_naming.models import MatchMethod, MatchResult, NormalizedName
from canonical_naming.repos.entity_repo import EntityRepo

logger = logging.getLogger(__name__)

# Token that introduces a sub-fund / series numeral (e.g. `EQT X No 1`).
# The numeral that follows it is *not* the fund numeral.
_SERIES_MARKER = "no"


def _extract_fund_numeral(normalized_str: str) -> int | None:
    """Return the first standalone Arabic integer that isn't part of a
    `no N` series marker, or `None` if no such integer exists.

    Examples:
        "blackstone capital partners 8" -> 8
        "eqt 10 no 1"                   -> 10   (`no 1` is series, skipped)
        "kkr americas fund 13"          -> 13
        "apollo"                        -> None
        "no 1"                          -> None (only a series marker)
    """
    tokens = normalized_str.split()
    i = 0
    while i < len(tokens):
        if tokens[i] == _SERIES_MARKER and i + 1 < len(tokens):
            # Skip the `no N` pair entirely so the series numeral is not
            # mistaken for the fund numeral.
            i += 2
            continue
        if tokens[i].isdigit():
            return int(tokens[i])
        i += 1
    return None


class FuzzyMatcher:
    """Fuzzy-match tier (cascade stage 3).

    Index strategy mirrors `ExactMatcher`: lazy-built per `id(repo)`,
    cached on the matcher instance. The index is a flat list of
    `(normalised_key, canonical_id, canonical_name)` tuples — one entry
    per canonical name and per alias — that we score against linearly.
    Linear scoring is fine at seed scale (~100 entries, O(string length)
    per `token_set_ratio` call). For catalogue scale, an embedding-based
    pre-filter would land in front of this step — see `04_UPGRADES.md`.
    """

    def __init__(self, threshold: int = 92) -> None:
        if not 0 <= threshold <= 100:
            raise ValueError(f"threshold must be in [0, 100]; got {threshold}")
        self._threshold = threshold
        self._index_cache: dict[int, list[tuple[str, str, str]]] = {}

    def _get_index(self, repo: EntityRepo) -> list[tuple[str, str, str]]:
        rid = id(repo)
        cached = self._index_cache.get(rid)
        if cached is not None:
            return cached
        index = build_normalised_index(repo)
        self._index_cache[rid] = index
        return index

    def invalidate_cache(self) -> None:
        self._index_cache.clear()

    def match(
        self,
        normalized: NormalizedName,
        repo: EntityRepo,
    ) -> MatchResult | None:
        query = normalized.normalized
        if not query:
            return None

        candidates = self._get_index(repo)
        best_score: float = 0.0
        best: tuple[str, str, str] | None = None
        for entry in candidates:
            score = fuzz.token_set_ratio(query, entry[0])
            if score > best_score:
                best_score = score
                best = entry

        if best is None or best_score < self._threshold:
            return None

        # --- Numeral guard ----------------------------------------------
        # If both query and best candidate have a detectable fund numeral
        # AND they disagree, refuse the match. Don't return a low-confidence
        # MatchResult — return None so the cascade falls through cleanly to
        # the LLM tier (or needs_review).
        query_num = _extract_fund_numeral(query)
        cand_num = _extract_fund_numeral(best[0])
        if (
            query_num is not None
            and cand_num is not None
            and query_num != cand_num
        ):
            logger.info(
                "numeral guard: %r (fund=%d) vs %r (fund=%d) "
                "score=%.1f — blocking fuzzy match, falling through",
                query, query_num, best[0], cand_num, best_score,
            )
            return None

        return MatchResult(
            canonical_id=best[1],
            canonical_name=best[2],
            confidence=best_score / 100.0,
            method=MatchMethod.FUZZY,
            needs_review=False,
        )
