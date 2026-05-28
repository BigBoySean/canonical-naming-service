from canonical_naming.matching.normalizer import normalize
from canonical_naming.models import NormalizedName

# --- Type & API surface ----------------------------------------------------


def test_returns_normalized_name_model() -> None:
    result = normalize("BCP VIII")
    assert isinstance(result, NormalizedName)


def test_original_field_preserved_untouched() -> None:
    raw = "BCP VIII (USD)"
    assert normalize(raw).original == raw


# --- Diacritics ------------------------------------------------------------


def test_societe_generale_diacritics_folded() -> None:
    result = normalize("Société Générale Capital Partners III, S.A.")
    assert result.normalized == "societe generale capital partners 3"
    assert result.legal_suffix == "sa"


# --- Roman → Arabic --------------------------------------------------------


def test_roman_viii_converted_in_trailing_position() -> None:
    result = normalize("Blackstone Capital Partners VIII L.P.")
    assert result.normalized == "blackstone capital partners 8"
    assert result.legal_suffix == "lp"


def test_roman_does_not_mangle_vista_token() -> None:
    # `vista` starts with v + i; the third char `s` breaks the Roman
    # whole-token lookahead, so the token must be preserved intact.
    result = normalize("Vista Equity Partners Fund VIII")
    assert "vista" in result.normalized.split()
    assert result.normalized.endswith(" 8")


def test_roman_does_not_mangle_cinven_token() -> None:
    result = normalize("Cinven Strategic Financials I")
    assert "cinven" in result.normalized.split()
    assert result.normalized.endswith(" 1")


def test_roman_x_standalone_converted_to_10() -> None:
    result = normalize("EQT X")
    assert result.normalized == "eqt 10"


def test_roman_xiv_converted() -> None:
    result = normalize("Warburg Pincus Global Growth XIV")
    assert result.normalized.endswith(" 14")


def test_roman_xiii_converted() -> None:
    result = normalize("KKR Americas Fund XIII")
    assert result.normalized.endswith(" 13")


def test_roman_ix_converted() -> None:
    result = normalize("Apollo Investment Fund IX")
    assert result.normalized.endswith(" 9")


# --- Series number (No N) preserved distinctly -----------------------------


def test_series_no_preserved_alongside_main_numeral() -> None:
    result = normalize("EQT X No 1 SCSp")
    tokens = result.normalized.split()
    # Main fund numeral (10 from X) and series numeral (1) must both survive
    # as distinct tokens, separated by the `no` marker.
    assert "10" in tokens
    assert "no" in tokens
    assert "1" in tokens
    assert result.legal_suffix == "scsp"


def test_series_no_with_dot_normalised() -> None:
    result = normalize("Foo Fund X No. 2")
    tokens = result.normalized.split()
    assert "no" in tokens
    assert "2" in tokens
    assert "10" in tokens


# --- Vehicle qualifiers ----------------------------------------------------


def test_paren_usd_extracted() -> None:
    result = normalize("BCP VIII (USD)")
    assert result.vehicle_qualifiers == ["usd"]
    assert result.normalized == "bcp 8"


def test_paren_cayman_feeder_split_into_two_qualifiers() -> None:
    result = normalize("AIF 9 (Cayman Feeder)")
    assert "cayman" in result.vehicle_qualifiers
    assert "feeder" in result.vehicle_qualifiers
    assert result.normalized == "aif 9"


def test_dash_parallel_vehicle_extracted_as_compound() -> None:
    result = normalize("KKR Americas Fund 13 - Parallel Vehicle")
    assert result.vehicle_qualifiers == ["parallel vehicle"]
    assert result.normalized == "kkr americas fund 13"


def test_paren_lux_extracted() -> None:
    result = normalize("Foo Fund VIII (Lux)")
    assert "lux" in result.vehicle_qualifiers
    assert result.normalized == "foo fund 8"


def test_legitimate_dash_not_stripped() -> None:
    # A trailing dash run that isn't qualifiers must NOT be stripped.
    result = normalize("Foo Capital - Bar Partners")
    assert result.vehicle_qualifiers == []
    assert "bar" in result.normalized.split()


# --- Ampersand fold --------------------------------------------------------


def test_ampersand_short_form_folds() -> None:
    assert normalize("H&F").normalized == "h and f"


def test_ampersand_spaced_form_folds() -> None:
    assert normalize("Hellman & Friedman").normalized == "hellman and friedman"


def test_already_spelled_and_unchanged() -> None:
    assert normalize("Hellman and Friedman").normalized == "hellman and friedman"


def test_three_ampersand_forms_collapse_to_same_key() -> None:
    a = normalize("Hellman & Friedman Capital Partners X").normalized
    b = normalize("Hellman and Friedman Capital Partners X").normalized
    assert a == b


# --- Legal suffix variety --------------------------------------------------


def test_legal_suffix_lp_variants() -> None:
    for raw in ["Foo, LP", "Foo LP", "Foo, L.P.", "Foo L.P."]:
        assert normalize(raw).legal_suffix == "lp", raw


def test_legal_suffix_llc_variants() -> None:
    assert normalize("Foo Fund, LLC").legal_suffix == "llc"
    assert normalize("Foo Fund, L.L.C.").legal_suffix == "llc"


def test_legal_suffix_scsp_detected() -> None:
    assert normalize("Foo Fund, SCSp").legal_suffix == "scsp"
    assert normalize("Foo Fund SCSp").legal_suffix == "scsp"


def test_legal_suffix_sca_detected() -> None:
    assert normalize("Foo Fund, S.C.A.").legal_suffix == "sca"
    assert normalize("Foo Fund SCA").legal_suffix == "sca"


def test_legal_suffix_sa_detected() -> None:
    assert normalize("Foo Fund, S.A.").legal_suffix == "sa"
    assert normalize("Foo Fund SA").legal_suffix == "sa"


def test_legal_suffix_gmbh_co_kg_detected_and_ampersand_folded() -> None:
    result = normalize("Triton Fund VI, GmbH & Co. KG")
    assert result.legal_suffix == "gmbh and co kg"
    assert result.normalized == "triton fund 6"


def test_usa_not_mistaken_for_sa_suffix() -> None:
    # `usa` at the end must NOT trigger the `sa` suffix match, because the
    # match requires a separator (comma or whitespace) before the suffix.
    result = normalize("Foo USA")
    assert result.legal_suffix is None
    assert result.normalized == "foo usa"


# --- Idempotence -----------------------------------------------------------


def test_idempotent_simple() -> None:
    once = normalize("Foo Bar VIII").normalized
    twice = normalize(once).normalized
    assert once == twice


def test_idempotent_bcp() -> None:
    once = normalize("BCP VIII").normalized
    twice = normalize(once).normalized
    assert once == twice


def test_idempotent_societe_generale() -> None:
    once = normalize("Société Générale Capital Partners III, S.A.").normalized
    twice = normalize(once).normalized
    assert once == twice


def test_idempotent_with_vehicle_qualifier() -> None:
    once = normalize("BCP VIII (USD)").normalized
    twice = normalize(once).normalized
    assert once == twice


# --- No-op safety ----------------------------------------------------------


def test_empty_string_does_not_crash() -> None:
    result = normalize("")
    assert result.normalized == ""
    assert result.legal_suffix is None
    assert result.vehicle_qualifiers == []
    assert result.original == ""


def test_single_word_does_not_crash() -> None:
    result = normalize("Apollo")
    assert result.normalized == "apollo"
    assert result.legal_suffix is None


# --- Abbreviation NON-expansion (v1 decision) ------------------------------


def test_bcp_not_expanded_to_blackstone() -> None:
    """BCP must NOT expand to Blackstone Capital Partners.

    The seed has BCP for Blackstone AND BCP as a token in
    `BCP VI Brookfield`; a flat global expansion would corrupt one of them.
    The normaliser leaves abbreviations alone — fuzzy + LLM tiers handle
    abbreviation bridging using catalogue context.
    """
    result = normalize("BCP VIII")
    assert result.normalized == "bcp 8"
    assert "blackstone" not in result.normalized


def test_kkr_not_expanded_to_kohlberg() -> None:
    result = normalize("KKR Americas XIII")
    assert result.normalized == "kkr americas 13"
    assert "kohlberg" not in result.normalized


def test_aif_not_expanded() -> None:
    result = normalize("AIF 9")
    assert result.normalized == "aif 9"
    assert "apollo" not in result.normalized
