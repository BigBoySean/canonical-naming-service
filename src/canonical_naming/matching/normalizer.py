"""Name normalisation pipeline.

The cascade's accuracy rests on this step. The pipeline is deliberately
*structural* — case, diacritics, whitespace, punctuation, numerals,
suffixes, vehicle qualifiers — and deliberately does **not** expand
abbreviations. Abbreviation bridging (BCP → Blackstone Capital Partners)
is left to the fuzzy and LLM tiers, which can use catalogue context to
disambiguate. A flat global expansion table would corrupt collisions
(e.g. `BCP` appears for Blackstone *and* as a token in `BCP VI Brookfield`).
See `02_EXPLANATION.md` for the full rationale.
"""

import re
from typing import Final

from unidecode import unidecode

from canonical_naming.models import NormalizedName

# --- Vehicle qualifier tables ----------------------------------------------

# Multi-token qualifiers treated as a single semantic unit. Sorted by token
# count descending so longer matches win at any given position.
_COMPOUND_QUALIFIERS: Final[list[str]] = sorted(
    [
        "co investment vehicle",
        "co-investment vehicle",
        "parallel vehicle",
        "parallel fund",
        "co-investment",
        "co investment",
    ],
    key=lambda phrase: len(phrase.split()),
    reverse=True,
)

# Tokens recognised individually inside parentheses. More permissive than the
# bare-trailing set because a parenthetical is an explicit qualifier slot.
_PAREN_SINGLE_QUALIFIERS: Final[frozenset[str]] = frozenset(
    {"feeder", "cayman", "lux", "luxembourg", "usd", "eur", "gbp", "chf"}
)

# Tokens recognised when they appear bare (no parens, no dash) at the end of
# the string. Restricted to vehicle-type tokens that wouldn't plausibly be
# part of a fund's real name, to avoid mis-stripping legitimate name fragments
# like "Cayman Holdings".
_SAFE_BARE_SINGLE_QUALIFIERS: Final[frozenset[str]] = frozenset({"feeder"})


# --- Legal suffix table ----------------------------------------------------

# (regex pattern, normalised form). Order matters: more specific patterns
# come first so e.g. `gmbh & co. kg` wins over a partial `kg` match.
_LEGAL_SUFFIXES: Final[list[tuple[str, str]]] = [
    (r"gmbh\s*&\s*co\.?\s*kg", "gmbh and co kg"),
    (r"l\.?l\.?c\.?", "llc"),
    (r"s\.?c\.?a\.?", "sca"),
    (r"l\.?p\.?", "lp"),
    (r"ltd\.?", "ltd"),
    (r"scsp", "scsp"),
    (r"s\.?a\.?", "sa"),
]


# --- Roman numerals --------------------------------------------------------

# Whole-token validation: the entire token must be in [ivxlcdm] (lookahead),
# *and* must form a structurally valid Roman numeral. This prevents `vista`,
# `cinven`, `mix`, etc. from being mis-parsed.
_ROMAN_VALIDATION_RE: Final[re.Pattern[str]] = re.compile(
    r"^(?=[ivxlcdm]+$)m{0,4}(cm|cd|d?c{0,3})(xc|xl|l?x{0,3})(ix|iv|v?i{0,3})$"
)
_ROMAN_VALUES: Final[dict[str, int]] = {
    "i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000,
}


def _is_valid_roman(token: str) -> bool:
    return _ROMAN_VALIDATION_RE.match(token) is not None


def _roman_to_int(token: str) -> int:
    total = 0
    prev = 0
    for char in reversed(token):
        value = _ROMAN_VALUES[char]
        if value < prev:
            total -= value
        else:
            total += value
            prev = value
    return total


# --- Step 3: Vehicle qualifier extraction ----------------------------------

_PAREN_RE: Final[re.Pattern[str]] = re.compile(r"\(([^)]*)\)")


def _walk_qualifiers_in_paren(content: str) -> list[str]:
    """Walk tokens inside a parenthetical, emitting qualifiers in order."""
    tokens = content.split()
    found: list[str] = []
    i = 0
    while i < len(tokens):
        matched = False
        # Compound (longest first).
        for compound in _COMPOUND_QUALIFIERS:
            parts = compound.split()
            if tokens[i : i + len(parts)] == parts:
                found.append(compound)
                i += len(parts)
                matched = True
                break
        if matched:
            continue
        if tokens[i] in _PAREN_SINGLE_QUALIFIERS:
            found.append(tokens[i])
        i += 1
    return found


def _consume_qualifier_tail(tail_tokens: list[str]) -> list[str] | None:
    """If `tail_tokens` is entirely composed of qualifiers, return them;
    else return None to signal that the tail should not be stripped.
    """
    consumed: list[str] = []
    i = 0
    while i < len(tail_tokens):
        matched = False
        for compound in _COMPOUND_QUALIFIERS:
            parts = compound.split()
            if tail_tokens[i : i + len(parts)] == parts:
                consumed.append(compound)
                i += len(parts)
                matched = True
                break
        if matched:
            continue
        if tail_tokens[i] in _SAFE_BARE_SINGLE_QUALIFIERS:
            consumed.append(tail_tokens[i])
            i += 1
            continue
        return None
    return consumed if consumed else None


def _extract_vehicle_qualifiers(s: str) -> tuple[str, list[str]]:
    """Extract vehicle / currency markers; return (cleaned_string, qualifiers).

    Three sources are checked in order:
      1. Parenthetical groups `(...)`. All recognised qualifiers inside are
         emitted; the whole group is stripped.
      2. Dash-delimited trailing run: `... - parallel vehicle`. Stripped only
         if the entire tail after the dash is qualifiers.
      3. Bare trailing run (no dash, no parens). Restricted to the safer
         qualifier set so legitimate name fragments don't get mistaken.
    """
    found: list[str] = []

    # 1) Parenthetical groups.
    def _replace_paren(match: re.Match[str]) -> str:
        found.extend(_walk_qualifiers_in_paren(match.group(1)))
        return " "

    s = _PAREN_RE.sub(_replace_paren, s)

    # 2) Dash-delimited trailing run.
    while True:
        match = re.search(r"\s+-\s+(.+)$", s)
        if not match:
            break
        tail_tokens = match.group(1).split()
        consumed = _consume_qualifier_tail(tail_tokens)
        if consumed is None:
            break
        found.extend(consumed)
        s = s[: match.start()].rstrip()

    # 3) Bare trailing run (no leading dash).
    while True:
        tokens = s.split()
        if not tokens:
            break
        matched_phrase: str | None = None
        for compound in _COMPOUND_QUALIFIERS:
            parts = compound.split()
            if len(tokens) >= len(parts) and tokens[-len(parts):] == parts:
                matched_phrase = compound
                break
        if matched_phrase is None and tokens[-1] in _SAFE_BARE_SINGLE_QUALIFIERS:
            matched_phrase = tokens[-1]
        if matched_phrase is None:
            break
        parts = matched_phrase.split()
        s = " ".join(tokens[: -len(parts)])
        found.append(matched_phrase)

    return s, found


# --- Step 4: Legal suffix extraction ---------------------------------------


def _extract_legal_suffix(s: str) -> tuple[str, str | None]:
    """Detect a trailing legal suffix; return (cleaned_string, normalised_form).

    The leading separator (`,` or whitespace) is required so we don't match
    against legitimate trailing tokens — e.g. `usa` must not match `sa`.
    """
    for pattern, normalised in _LEGAL_SUFFIXES:
        match = re.search(rf"(?:,|\s)+(?:{pattern})\s*$", s)
        if match:
            return s[: match.start()].rstrip(" ,"), normalised
    return s, None


# --- Step 5: Series-number markers -----------------------------------------


def _normalise_series_marker(s: str) -> str:
    """Normalise `No.` / `Nº` / `N°` series markers to a stable `no N` form.

    `unidecode` already lowercases `Nº` to `no` (via `º` → `o`), so by the
    time we reach this step we mostly need to strip the trailing dot on
    `no.`. The `n deg` defensive case covers `unidecode` versions that emit
    `deg` for U+00B0 (degree sign) instead of dropping it.
    """
    s = re.sub(r"\bno\.\s+(\d+)", r"no \1", s)
    s = re.sub(r"\bn\s*deg\s*(\d+)", r"no \1", s)
    return s


# --- Step 6: Roman → Arabic ------------------------------------------------


def _convert_roman_numerals(s: str) -> str:
    """Convert whole-token valid Roman numerals to their Arabic equivalents.

    `vista`, `cinven`, `mix` and similar are left alone — the lookahead in
    the validation regex requires the *entire* token to be Roman-only.
    """
    tokens = s.split()
    converted = [
        str(_roman_to_int(token)) if _is_valid_roman(token) else token
        for token in tokens
    ]
    return " ".join(converted)


# --- Step 7: Punctuation ---------------------------------------------------


def _normalise_punctuation(s: str) -> str:
    """`&` → `and` (so `H&F` collapses with `Hellman and Friedman`), then
    strip remaining non-word/non-space characters to whitespace.
    """
    s = s.replace("&", " and ")
    s = re.sub(r"[^\w\s]", " ", s)
    return s


# --- Step 8: Whitespace collapse -------------------------------------------


def _collapse_whitespace(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


# --- Public API ------------------------------------------------------------


def normalize(raw_name: str) -> NormalizedName:
    """Normalise a raw partnership/fund name for cascade matching.

    Pipeline order matters; see module docstring and `02_EXPLANATION.md` for
    rationale per step.
    """
    s = unidecode(raw_name)                       # 1. ASCII fold
    s = s.lower()                                 # 2. lowercase
    s, qualifiers = _extract_vehicle_qualifiers(s)  # 3. vehicle qualifiers
    s, legal_suffix = _extract_legal_suffix(s)    # 4. legal suffix
    s = _normalise_series_marker(s)               # 5. series markers
    s = _convert_roman_numerals(s)                # 6. Roman → Arabic
    s = _normalise_punctuation(s)                 # 7. punctuation
    s = _collapse_whitespace(s)                   # 8. whitespace

    return NormalizedName(
        normalized=s,
        legal_suffix=legal_suffix,
        vehicle_qualifiers=qualifiers,
        original=raw_name,
    )
