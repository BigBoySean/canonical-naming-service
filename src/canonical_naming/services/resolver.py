"""ResolverService — orchestrates the 4-tier matching cascade.

The service owns the order in which matchers run, the early-exit
behaviour ("first non-None result wins"), the `needs_review` fallback,
the API-facing `ResolveResponse` mapping, and the `register_entity`
flow (which mutates the repo and must invalidate every matcher's
cached index so the new entity is immediately resolvable).

Matchers are injected as an ordered list — the list order *is* the
cascade order. Tests can substitute spy/mock matchers without touching
the service.
"""

import logging
import re
from collections.abc import Sequence

from unidecode import unidecode

from canonical_naming.matching.base import Matcher
from canonical_naming.matching.normalizer import normalize
from canonical_naming.models import (
    Entity,
    MatchMethod,
    MatchResult,
    ResolveResponse,
)
from canonical_naming.repos.entity_repo import EntityRepo

logger = logging.getLogger(__name__)

_SLUG_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _slugify(canonical_name: str) -> str:
    """Generate an `ent_<slug>` id from a canonical name.

    Folds diacritics via `unidecode`, lowercases, replaces runs of
    non-alphanumeric characters with a single underscore, trims leading
    and trailing underscores, and prefixes with `ent_`. Matches the seed
    convention (e.g. `ent_blackstone_cp_viii`, `ent_apollo_investment_ix`)
    and the pattern `^ent_[a-z0-9_]+$`.
    """
    s = unidecode(canonical_name).lower()
    s = _SLUG_NON_ALNUM.sub("_", s).strip("_")
    if not s:
        raise ValueError(f"cannot derive slug from canonical_name {canonical_name!r}")
    return f"ent_{s}"


class ResolverService:
    """Orchestrates the matching cascade and owns the repo for writes."""

    def __init__(
        self,
        matchers: Sequence[Matcher],
        repo: EntityRepo,
    ) -> None:
        """Construct with matchers in cascade order — `[exact, fuzzy, llm]` in
        production, or whatever order tests want. The first matcher returning
        a non-None `MatchResult` wins; if every matcher returns None, the
        service emits a `needs_review` response.
        """
        self._matchers = list(matchers)
        self._repo = repo

    # --- Public API --------------------------------------------------------

    def resolve(self, raw_name: str) -> ResolveResponse:
        normalized = normalize(raw_name)
        for matcher in self._matchers:
            result = matcher.match(normalized, self._repo)
            if result is not None:
                logger.info(
                    "resolved %r via %s -> %s conf=%.2f",
                    raw_name, result.method.value,
                    result.canonical_id, result.confidence,
                )
                return self._to_response(raw_name, result)

        logger.info("resolved %r -> needs_review (no tier matched)", raw_name)
        return self._needs_review_response(raw_name)

    def resolve_batch(self, raw_names: list[str]) -> list[ResolveResponse]:
        """Sequential v1 — see `02_EXPLANATION.md` for the async deferral
        rationale. Reuses the single-resolve path so behaviour is identical
        per element."""
        return [self.resolve(name) for name in raw_names]

    def register_entity(
        self,
        canonical_name: str,
        aliases: list[str] | None = None,
    ) -> Entity:
        """Generate a stable `ent_<slug>` id, construct the entity, register
        it in the repo, and invalidate every matcher's cached index so the
        new entity is immediately resolvable.

        Raises `ValueError` if the derived id collides with an existing
        entity — registration is intentionally strict in v1; the caller is
        expected to have already established (via `/resolve`) that the
        entity is not already in the catalogue.
        """
        canonical_id = _slugify(canonical_name)
        if self._repo.get(canonical_id) is not None:
            raise ValueError(
                f"canonical_id collision: {canonical_id!r} already registered"
            )
        entity = Entity(
            canonical_id=canonical_id,
            canonical_name=canonical_name,
            aliases=aliases if aliases is not None else [],
        )
        self._repo.add(entity)
        # Critical: every matcher caches a normalised index keyed on
        # id(repo). Without invalidation, a freshly-registered entity is
        # invisible to subsequent resolve() calls in the same process.
        for matcher in self._matchers:
            matcher.invalidate_cache()
        logger.info(
            "registered entity %s (%r) with %d aliases",
            canonical_id, canonical_name, len(entity.aliases),
        )
        return entity

    # --- Mapping helpers ---------------------------------------------------

    @staticmethod
    def _to_response(raw_name: str, result: MatchResult) -> ResolveResponse:
        return ResolveResponse(
            raw_name=raw_name,
            canonical_id=result.canonical_id,
            canonical_name=result.canonical_name,
            confidence=result.confidence,
            method=result.method,
            needs_review=False,
        )

    @staticmethod
    def _needs_review_response(raw_name: str) -> ResolveResponse:
        return ResolveResponse(
            raw_name=raw_name,
            canonical_id=None,
            canonical_name=None,
            confidence=0.0,
            method=MatchMethod.NONE,
            needs_review=True,
        )
