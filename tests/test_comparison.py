import pytest

from app.comparison import (
    compare_abv,
    compare_brand_name,
    compare_country,
    compare_government_warning,
    compare_net_contents,
    compare_producer,
    compare_product_class,
    verify_label,
)
from app.models import ApplicationData, ExtractedLabel


GOVERNMENT_WARNING = (
    "GOVERNMENT WARNING: (1) According to the Surgeon General, women should not drink "
    "alcoholic beverages during pregnancy because of the risk of birth defects. "
    "(2) Consumption of alcoholic beverages impairs your ability to drive a car or "
    "operate machinery, and may cause health problems."
)


def _application(**overrides: str) -> ApplicationData:
    values = {
        "brand_name": "Acme Reserve",
        "product_class": "Red Wine",
        "producer": "Acme Winery LLC",
        "country": "United States",
        "abv": "13.5%",
        "net_contents": "750 ml",
        "government_warning": GOVERNMENT_WARNING,
    }
    values.update(overrides)
    return ApplicationData(**values)


def _extracted(**overrides: str) -> ExtractedLabel:
    values = {
        "brand_name": "Acme Reserve",
        "product_class": "Red Wine",
        "producer": "Acme Winery LLC",
        "country": "United States",
        "abv": "13.5%",
        "net_contents": "750 ml",
        "government_warning": GOVERNMENT_WARNING,
    }
    values.update(overrides)
    return ExtractedLabel(**values)


def test_exact_matches_produce_all_pass_and_overall_pass() -> None:
    result = verify_label(_application(), _extracted())

    assert result.verdict == "APPROVED"
    assert [field.status for field in result.results] == ["PASS"] * 7
    assert {field.field for field in result.results} == set(ApplicationData.model_fields)


def test_one_failing_field_produces_needs_review() -> None:
    result = verify_label(_application(), _extracted(country="France"))

    assert result.verdict == "NEEDS_REVIEW"
    assert any(field.field == "country" and field.status == "FAIL" for field in result.results)


def test_brand_fuzzy_match_passes_for_minor_punctuation_case_and_spacing() -> None:
    result = compare_brand_name("Acme Reserve", "  ACME, reserve  ")

    assert result.status == "PASS"
    assert result.score is not None
    assert result.score >= 90


def test_case_only_brand_difference_passes() -> None:
    result = compare_brand_name("Acme Reserve", "ACME RESERVE")

    assert result.status == "PASS"


def test_brand_fuzzy_match_fails_below_threshold() -> None:
    result = compare_brand_name("Acme Reserve", "Completely Different")

    assert result.status == "FAIL"
    assert result.score is not None
    assert result.score < 90


def test_product_class_fuzzy_match_passes_for_small_wording_differences() -> None:
    result = compare_product_class("Red Table Wine", "table red wine")

    assert result.status == "PASS"


def test_producer_fuzzy_match_passes_for_small_formatting_differences() -> None:
    result = compare_producer("Acme Winery LLC", "ACME Winery, L.L.C.")

    assert result.status == "PASS"


@pytest.mark.parametrize(
    ("expected", "actual"),
    [
        ("United States", "USA"),
        ("United States", "U.S.A."),
        ("USA", "United States"),
        ("United States of America", "United States"),
        ("United Kingdom", "Great Britain"),
        ("Italy", "Italia"),
        ("Germany", "Deutschland"),
        ("Spain", "Espana"),
    ],
)
def test_country_synonyms_pass(expected: str, actual: str) -> None:
    result = compare_country(expected, actual)

    assert result.status == "PASS"
    assert result.match_type == "synonym"


def test_different_countries_fail() -> None:
    result = compare_country("United States", "France")

    assert result.status == "FAIL"


@pytest.mark.parametrize("actual", ["13.5%", "13.50", "13.5 percent"])
def test_abv_passes_for_equivalent_formats(actual: str) -> None:
    result = compare_abv("13.5%", actual)

    assert result.status == "PASS"


def test_abv_passes_with_alc_vol_and_proof_text() -> None:
    result = compare_abv("45%", "45% Alc./Vol. (90 Proof)")

    assert result.status == "PASS"


def test_abv_passes_with_bare_proof() -> None:
    result = compare_abv("45%", "90 Proof")

    assert result.status == "PASS"
    assert result.match_type == "numeric"


def test_abv_fails_when_outside_tolerance() -> None:
    result = compare_abv("13.5%", "13.7%")

    assert result.status == "FAIL"


def test_net_contents_passes_for_ml_and_liter_equivalence() -> None:
    result = compare_net_contents("750 ml", "0.75 L")

    assert result.status == "PASS"


def test_net_contents_passes_with_case_only_unit_and_no_space() -> None:
    result = compare_net_contents("750 mL", "750ml")

    assert result.status == "PASS"


@pytest.mark.parametrize("actual", ["Contents: 750 mL", "750 mL (25 FL OZ)"])
def test_net_contents_searches_inside_extracted_text(actual: str) -> None:
    result = compare_net_contents("750 mL", actual)

    assert result.status == "PASS"
    assert result.match_type == "unit_normalized"


def test_net_contents_passes_for_fluid_ounce_equivalence() -> None:
    result = compare_net_contents("750 mL", "25.36 fl oz")

    assert result.status == "PASS"


def test_net_contents_fails_when_outside_tolerance() -> None:
    result = compare_net_contents("750 ml", "752 ml")

    assert result.status == "FAIL"


def test_government_warning_passes_only_for_exact_case_sensitive_match() -> None:
    result = compare_government_warning(GOVERNMENT_WARNING, GOVERNMENT_WARNING)

    assert result.status == "PASS"


def test_correct_all_caps_government_warning_passes() -> None:
    result = compare_government_warning(GOVERNMENT_WARNING, GOVERNMENT_WARNING)

    assert result.status == "PASS"


@pytest.mark.parametrize(
    "actual",
    [
        GOVERNMENT_WARNING.lower(),
        GOVERNMENT_WARNING.replace(".", "", 1),
        f"{GOVERNMENT_WARNING} ",
        GOVERNMENT_WARNING.replace("health problems", "serious health problems"),
    ],
)
def test_government_warning_fails_for_non_exact_match(actual: str) -> None:
    result = compare_government_warning(GOVERNMENT_WARNING, actual)

    assert result.status == "FAIL"


def test_government_warning_in_title_case_fails() -> None:
    title_case_warning = GOVERNMENT_WARNING.title()

    result = compare_government_warning(GOVERNMENT_WARNING, title_case_warning)

    assert result.status == "FAIL"


def test_government_warning_missing_colon_fails() -> None:
    missing_colon_warning = GOVERNMENT_WARNING.replace("GOVERNMENT WARNING:", "GOVERNMENT WARNING")

    result = compare_government_warning(GOVERNMENT_WARNING, missing_colon_warning)

    assert result.status == "FAIL"


def test_misread_government_warning_returns_extracted_text() -> None:
    misread_warning = GOVERNMENT_WARNING.replace("Surgeon", "Sargeon")

    result = compare_government_warning(GOVERNMENT_WARNING, misread_warning)

    assert result.status == "FAIL"
    assert result.found == misread_warning


@pytest.mark.parametrize(
    ("field_name", "comparison"),
    [
        ("brand_name", compare_brand_name),
        ("product_class", compare_product_class),
        ("producer", compare_producer),
        ("country", compare_country),
        ("abv", compare_abv),
        ("net_contents", compare_net_contents),
        ("government_warning", compare_government_warning),
    ],
)
def test_blank_extracted_fields_fail_with_clear_reason(field_name: str, comparison) -> None:
    result = comparison("Expected value", "  ")

    assert result.field == field_name
    assert result.match_type == "missing"
    assert result.status == "FAIL"
    assert result.reason == "Extracted value is missing or blank."


@pytest.mark.parametrize(
    ("field_name", "comparison"),
    [
        ("brand_name", compare_brand_name),
        ("product_class", compare_product_class),
        ("producer", compare_producer),
        ("country", compare_country),
        ("abv", compare_abv),
        ("net_contents", compare_net_contents),
        ("government_warning", compare_government_warning),
    ],
)
def test_none_extracted_fields_fail_with_clear_reason(field_name: str, comparison) -> None:
    result = comparison("Expected value", None)

    assert result.field == field_name
    assert result.match_type == "missing"
    assert result.status == "FAIL"
    assert result.found == ""
    assert result.reason == "Extracted value is missing or blank."


def test_verify_label_treats_null_extracted_fields_as_needing_review() -> None:
    result = verify_label(_application(), ExtractedLabel())

    assert result.verdict == "NEEDS_REVIEW"
    assert [field.status for field in result.results] == ["FAIL"] * 7
