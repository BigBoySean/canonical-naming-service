"""Tests for `LLMMatcher` + `MockLLMMatcher`.

Offline tests (most of this file) run with no key, no network: they
inject fake clients to exercise each branch of `match()`. Live tests
(at the bottom) are gated behind the presence of `ANTHROPIC_API_KEY`
in the environment and skip cleanly when absent, so CI stays green
without credentials.
"""

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any

import pytest

from canonical_naming.config import Settings
from canonical_naming.matching.base import Matcher
from canonical_naming.matching.llm import LLMMatcher
from canonical_naming.matching.normalizer import normalize
from canonical_naming.models import MatchMethod, MatchResult
from canonical_naming.repos.entity_repo import InMemoryEntityRepo, load_repo_from_seed
from tests.fixtures import MockLLMMatcher

# ============================================================================
# Test-double infrastructure for the Anthropic client
# ============================================================================


@dataclass
class _FakeBlock:
    type: str
    text: str


@dataclass
class _FakeMessage:
    content: list[_FakeBlock]


@dataclass
class _FakeMessages:
    """Stand-in for `client.messages` — records calls and returns canned text."""

    return_texts: list[str] = field(default_factory=list)
    raise_excs: list[Exception | None] = field(default_factory=list)
    call_count: int = 0

    def create(self, **kwargs: Any) -> _FakeMessage:
        idx = self.call_count
        self.call_count += 1
        if idx < len(self.raise_excs) and self.raise_excs[idx] is not None:
            exc = self.raise_excs[idx]
            assert exc is not None
            raise exc
        text = self.return_texts[idx] if idx < len(self.return_texts) else ""
        return _FakeMessage(content=[_FakeBlock(type="text", text=text)])


@dataclass
class _FakeClient:
    messages: _FakeMessages


class _NeverCalledClient:
    """Any access to `.messages` raises — used to prove the disabled path
    never touches the client."""

    @property
    def messages(self) -> Any:
        raise AssertionError(
            "LLMMatcher must not touch the client when llm_enabled=False"
        )


def _enabled_settings() -> Settings:
    return Settings(
        anthropic_api_key="test-not-real",
        llm_enabled=True,
        llm_model="claude-haiku-4-5-20251001",
        llm_timeout_seconds=5,
        fuzzy_threshold=92,
        _env_file=None,
    )


def _disabled_settings() -> Settings:
    return Settings(
        anthropic_api_key=None,
        llm_enabled=False,
        llm_model="claude-haiku-4-5-20251001",
        llm_timeout_seconds=5,
        fuzzy_threshold=92,
        _env_file=None,
    )


@pytest.fixture
def repo() -> InMemoryEntityRepo:
    return load_repo_from_seed()


# ============================================================================
# MockLLMMatcher — protocol conformance + dict-based behaviour
# ============================================================================


def test_mock_llm_matcher_implements_matcher_protocol(repo: InMemoryEntityRepo) -> None:
    mock = MockLLMMatcher({})
    assert isinstance(mock, Matcher), (
        "MockLLMMatcher must satisfy the Matcher protocol "
        "(Matcher is runtime_checkable)"
    )


def test_mock_returns_canned_result_for_known_input(repo: InMemoryEntityRepo) -> None:
    canned = MatchResult(
        canonical_id="ent_blackstone_cp_viii",
        canonical_name="Blackstone Capital Partners VIII, L.P.",
        confidence=0.9,
        method=MatchMethod.LLM,
        needs_review=False,
    )
    mock = MockLLMMatcher({"bcp 8": canned})
    result = mock.match(normalize("BCP VIII"), repo)
    assert result is canned


def test_mock_returns_none_for_unknown_input(repo: InMemoryEntityRepo) -> None:
    mock = MockLLMMatcher({})
    result = mock.match(normalize("BCP VIII"), repo)
    assert result is None


# ============================================================================
# llm_enabled=False — must NOT touch the network
# ============================================================================


def test_disabled_returns_none_without_touching_client(
    repo: InMemoryEntityRepo,
) -> None:
    matcher = LLMMatcher(
        settings=_disabled_settings(),
        client=_NeverCalledClient(),
    )
    result = matcher.match(normalize("BCP VIII"), repo)
    assert result is None


def test_disabled_path_does_not_construct_anthropic_client(
    monkeypatch: pytest.MonkeyPatch, repo: InMemoryEntityRepo,
) -> None:
    construction_count = [0]

    class CountingAnthropic:
        def __init__(self, **kwargs: Any) -> None:
            construction_count[0] += 1
            raise AssertionError(
                "Anthropic constructor must not be called when llm_enabled=False"
            )

    monkeypatch.setattr(
        "canonical_naming.matching.llm.Anthropic", CountingAnthropic
    )
    matcher = LLMMatcher(settings=_disabled_settings())
    result = matcher.match(normalize("BCP VIII"), repo)
    assert result is None
    assert construction_count[0] == 0


# ============================================================================
# Defensive parsing of LLM output
# ============================================================================


def test_malformed_json_returns_none(repo: InMemoryEntityRepo) -> None:
    client = _FakeClient(messages=_FakeMessages(return_texts=["not json at all"]))
    matcher = LLMMatcher(settings=_enabled_settings(), client=client)
    result = matcher.match(normalize("BCP VIII"), repo)
    assert result is None


def test_json_missing_required_field_returns_none(
    repo: InMemoryEntityRepo,
) -> None:
    # `confidence` missing — Pydantic ValidationError → caught → None.
    client = _FakeClient(messages=_FakeMessages(
        return_texts=['{"canonical_id": "ent_blackstone_cp_viii"}']
    ))
    matcher = LLMMatcher(settings=_enabled_settings(), client=client)
    result = matcher.match(normalize("BCP VIII"), repo)
    assert result is None


def test_json_with_markdown_codefence_still_parsed(
    repo: InMemoryEntityRepo,
) -> None:
    # LLM sometimes wraps in ```json ... ``` despite the instruction.
    fenced = (
        "```json\n"
        '{"canonical_id": "ent_blackstone_cp_viii", '
        '"confidence": 0.95, "reasoning": "ok"}\n'
        "```"
    )
    client = _FakeClient(messages=_FakeMessages(return_texts=[fenced]))
    matcher = LLMMatcher(settings=_enabled_settings(), client=client)
    result = matcher.match(normalize("BCP VIII"), repo)
    assert result is not None
    assert result.canonical_id == "ent_blackstone_cp_viii"


# ============================================================================
# LLM declines / below floor → None
# ============================================================================


def test_llm_declines_returns_none(repo: InMemoryEntityRepo) -> None:
    client = _FakeClient(messages=_FakeMessages(return_texts=[
        json.dumps({"canonical_id": None, "confidence": 0.0, "reasoning": "no match"})
    ]))
    matcher = LLMMatcher(settings=_enabled_settings(), client=client)
    result = matcher.match(normalize("BCP VIII"), repo)
    assert result is None


def test_llm_low_confidence_below_accept_floor_returns_none(
    repo: InMemoryEntityRepo,
) -> None:
    # 0.50 is below the 0.70 accept floor.
    client = _FakeClient(messages=_FakeMessages(return_texts=[
        json.dumps({
            "canonical_id": "ent_blackstone_cp_viii",
            "confidence": 0.50,
            "reasoning": "not sure",
        })
    ]))
    matcher = LLMMatcher(settings=_enabled_settings(), client=client)
    result = matcher.match(normalize("BCP VIII"), repo)
    assert result is None


# ============================================================================
# Numeral-guard override — belt-and-suspenders against a misbehaving LLM
# ============================================================================


def test_numeral_guard_overrides_llm_acceptance(
    repo: InMemoryEntityRepo,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Input fund=11; LLM (against its instructions) chose H&F X (fund=10).
    # The code-level guard MUST refuse, regardless of LLM confidence.
    caplog.set_level(logging.INFO, logger="canonical_naming.matching.llm")
    client = _FakeClient(messages=_FakeMessages(return_texts=[
        json.dumps({
            "canonical_id": "ent_hf_cp_x",
            "confidence": 0.95,
            "reasoning": "(misbehaving LLM)",
        })
    ]))
    matcher = LLMMatcher(settings=_enabled_settings(), client=client)
    result = matcher.match(
        normalize("Hellman & Friedman Capital Partners XI"), repo
    )
    assert result is None
    assert "numeral-guard" in caplog.text.lower() or "numeral guard" in caplog.text.lower()


def test_llm_chooses_nonexistent_id_returns_none(
    repo: InMemoryEntityRepo,
) -> None:
    # Defensive: LLM hallucinates a canonical_id that isn't in the repo.
    client = _FakeClient(messages=_FakeMessages(return_texts=[
        json.dumps({
            "canonical_id": "ent_does_not_exist",
            "confidence": 0.95,
            "reasoning": "(hallucination)",
        })
    ]))
    matcher = LLMMatcher(settings=_enabled_settings(), client=client)
    result = matcher.match(normalize("BCP VIII"), repo)
    assert result is None


# ============================================================================
# Happy path — LLM accepts and numerals agree
# ============================================================================


def test_llm_accepts_with_matching_numerals(repo: InMemoryEntityRepo) -> None:
    client = _FakeClient(messages=_FakeMessages(return_texts=[
        json.dumps({
            "canonical_id": "ent_blackstone_cp_viii",
            "confidence": 0.95,
            "reasoning": "BCP is the standard abbreviation; numerals agree (8)",
        })
    ]))
    matcher = LLMMatcher(settings=_enabled_settings(), client=client)
    result = matcher.match(normalize("BCP VIII"), repo)
    assert result is not None
    assert result.canonical_id == "ent_blackstone_cp_viii"
    assert result.canonical_name == "Blackstone Capital Partners VIII, L.P."
    assert result.method == MatchMethod.LLM
    assert result.confidence == 0.95


# ============================================================================
# Retry behaviour
# ============================================================================


def test_transient_error_then_success_succeeds(repo: InMemoryEntityRepo) -> None:
    client = _FakeClient(messages=_FakeMessages(
        return_texts=[
            "",  # ignored on first attempt (it raises)
            json.dumps({
                "canonical_id": "ent_blackstone_cp_viii",
                "confidence": 0.90,
                "reasoning": "ok on retry",
            }),
        ],
        raise_excs=[RuntimeError("transient"), None],
    ))
    matcher = LLMMatcher(settings=_enabled_settings(), client=client)
    result = matcher.match(normalize("BCP VIII"), repo)
    assert result is not None
    assert result.canonical_id == "ent_blackstone_cp_viii"
    assert client.messages.call_count == 2


def test_two_failures_returns_none(repo: InMemoryEntityRepo) -> None:
    client = _FakeClient(messages=_FakeMessages(
        return_texts=["", ""],
        raise_excs=[RuntimeError("first"), RuntimeError("second")],
    ))
    matcher = LLMMatcher(settings=_enabled_settings(), client=client)
    result = matcher.match(normalize("BCP VIII"), repo)
    assert result is None
    assert client.messages.call_count == 2


# ============================================================================
# Empty / no-candidate inputs — never crash
# ============================================================================


def test_no_fuzzy_candidates_skips_llm_call(repo: InMemoryEntityRepo) -> None:
    # Garbage input that won't score above the candidate floor.
    client = _FakeClient(messages=_FakeMessages(return_texts=[]))
    matcher = LLMMatcher(settings=_enabled_settings(), client=client)
    result = matcher.match(normalize("zzzzz qqqqq xxxxx"), repo)
    assert result is None
    assert client.messages.call_count == 0


def test_empty_normalized_returns_none(repo: InMemoryEntityRepo) -> None:
    client = _FakeClient(messages=_FakeMessages(return_texts=[]))
    matcher = LLMMatcher(settings=_enabled_settings(), client=client)
    result = matcher.match(normalize(""), repo)
    assert result is None
    assert client.messages.call_count == 0


# ============================================================================
# LIVE tests — gated on real ANTHROPIC_API_KEY. Skip cleanly when absent.
# ============================================================================

_KEY = os.environ.get("ANTHROPIC_API_KEY")


@pytest.mark.skipif(
    not _KEY,
    reason="ANTHROPIC_API_KEY not set; live LLM tests skipped",
)
def test_live_llm_resolves_abbreviation_to_blackstone() -> None:
    settings = Settings(
        anthropic_api_key=_KEY,
        llm_enabled=True,
        llm_model="claude-haiku-4-5-20251001",
        llm_timeout_seconds=15,
        fuzzy_threshold=92,
        _env_file=None,
    )
    matcher = LLMMatcher(settings=settings)
    repo = load_repo_from_seed()
    result = matcher.match(normalize("BX Cap Partners VIII"), repo)
    assert result is not None, "live LLM should resolve BX Cap Partners VIII"
    assert result.canonical_id == "ent_blackstone_cp_viii"
    assert result.method == MatchMethod.LLM


@pytest.mark.skipif(
    not _KEY,
    reason="ANTHROPIC_API_KEY not set; live LLM tests skipped",
)
def test_live_llm_declines_hf_xi() -> None:
    settings = Settings(
        anthropic_api_key=_KEY,
        llm_enabled=True,
        llm_model="claude-haiku-4-5-20251001",
        llm_timeout_seconds=15,
        fuzzy_threshold=92,
        _env_file=None,
    )
    matcher = LLMMatcher(settings=settings)
    repo = load_repo_from_seed()
    result = matcher.match(
        normalize("Hellman & Friedman Capital Partners XI"), repo
    )
    # Acceptable outcomes:
    # - LLM declines (canonical_id=null) -> match() returns None
    # - LLM picks X but numeral guard overrides -> match() returns None
    # Either way, None is the correct answer.
    assert result is None, (
        "H&F XI must not resolve — neither LLM judgement nor numeral guard "
        "should allow XI -> X"
    )
