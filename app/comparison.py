import re
import string

from rapidfuzz import fuzz

from app.models import ApplicationData, ExtractedLabel, FieldResult, VerificationResult


FUZZY_THRESHOLD = 90.0
ABV_TOLERANCE = 0.1
NET_CONTENTS_ML_TOLERANCE = 1.0
US_FLUID_OUNCE_TO_ML = 29.5735295625

_COUNTRY_SYNONYMS = {
    "america": "united states",
    "usa": "united states",
    "us": "united states",
    "u s a": "united states",
    "u s": "united states",
    "united states": "united states",
    "united states of america": "united states",
    "uk": "united kingdom",
    "u k": "united kingdom",
    "great britain": "united kingdom",
    "britain": "united kingdom",
    "england": "united kingdom",
    "scotland": "united kingdom",
    "wales": "united kingdom",
    "united kingdom": "united kingdom",
    "france": "france",
    "french republic": "france",
    "italia": "italy",
    "italy": "italy",
    "spain": "spain",
    "espana": "spain",
    "germany": "germany",
    "deutschland": "germany",
    "portugal": "portugal",
    "australia": "australia",
    "new zealand": "new zealand",
    "canada": "canada",
    "mexico": "mexico",
    "chile": "chile",
    "argentina": "argentina",
    "south africa": "south africa",
    "ireland": "ireland",
    "japan": "japan",
}

_ABV_NUMBER_ONLY_PATTERN = re.compile(r"\d+(?:\.\d+)?")
_ABV_PROOF_PATTERN = re.compile(r"(?P<value>\d+(?:\.\d+)?)\s*proof\b", re.IGNORECASE)
_ABV_WITH_UNIT_PATTERN = re.compile(
    r"(?P<value>\d+(?:\.\d+)?)\s*(?:%|percent|abv|alc\.?\s*/?\s*vol\.?)",
    re.IGNORECASE,
)
_NET_CONTENTS_PATTERN = re.compile(
    r"(?P<value>\d+(?:\.\d+)?)\s*"
    r"(?P<unit>ml|milliliters?|millilitres?|l|liters?|litres?|fl\.?\s*oz\.?|fluid\s+ounces?|oz\.?)"
    r"(?=\W|$)",
    re.IGNORECASE,
)


def verify_label(application: ApplicationData, extracted: ExtractedLabel) -> VerificationResult:
    results = [
        compare_brand_name(application.brand_name, extracted.brand_name),
        compare_product_class(application.product_class, extracted.product_class),
        compare_producer(application.producer, extracted.producer),
        compare_country(application.country, extracted.country),
        compare_abv(application.abv, extracted.abv),
        compare_net_contents(application.net_contents, extracted.net_contents),
        compare_government_warning(
            application.government_warning,
            extracted.government_warning,
        ),
    ]
    verdict = "NEEDS_REVIEW" if any(field.status == "FAIL" for field in results) else "APPROVED"
    return VerificationResult(verdict=verdict, results=results)


def compare_brand_name(expected: str, actual: str | None) -> FieldResult:
    return _compare_fuzzy("brand_name", expected, actual)


def compare_product_class(expected: str, actual: str | None) -> FieldResult:
    return _compare_fuzzy("product_class", expected, actual)


def compare_producer(expected: str, actual: str | None) -> FieldResult:
    return _compare_fuzzy("producer", expected, actual)


def compare_country(expected: str, actual: str | None) -> FieldResult:
    missing = _missing_reason(expected, actual)
    if missing:
        return _fail("country", expected, _display_value(actual), missing, match_type="missing")

    normalized_expected = _normalize_country(expected)
    normalized_actual = _normalize_country(actual)
    if normalized_expected == normalized_actual:
        return FieldResult(
            field="country",
            match_type="synonym",
            expected=expected,
            found=actual,
            status="PASS",
            reason="Countries match after synonym normalization.",
        )

    return _fail(
        "country",
        expected,
        actual,
        "Countries do not match after synonym normalization.",
        match_type="synonym",
    )


def compare_abv(expected: str, actual: str | None) -> FieldResult:
    missing = _missing_reason(expected, actual)
    if missing:
        return _fail("abv", expected, _display_value(actual), missing, match_type="missing")

    expected_value = _parse_abv(expected)
    actual_value = _parse_abv(actual)
    if expected_value is None or actual_value is None:
        return _fail("abv", expected, actual, "Could not parse ABV value.", match_type="numeric")

    difference = abs(expected_value - actual_value)
    if difference <= ABV_TOLERANCE:
        return FieldResult(
            field="abv",
            match_type="numeric",
            expected=expected,
            found=actual,
            status="PASS",
            reason=f"ABV values match within {ABV_TOLERANCE:g} tolerance.",
            score=difference,
        )

    return _fail(
        "abv",
        expected,
        actual,
        f"ABV difference {difference:g} exceeds {ABV_TOLERANCE:g} tolerance.",
        match_type="numeric",
        score=difference,
    )


def compare_net_contents(expected: str, actual: str | None) -> FieldResult:
    missing = _missing_reason(expected, actual)
    if missing:
        return _fail("net_contents", expected, _display_value(actual), missing, match_type="missing")

    expected_ml = _parse_net_contents_ml(expected)
    actual_ml = _parse_net_contents_ml(actual)
    if expected_ml is None or actual_ml is None:
        return _fail("net_contents", expected, actual, "Could not parse net contents value.", match_type="unit_normalized")

    difference = abs(expected_ml - actual_ml)
    if difference <= NET_CONTENTS_ML_TOLERANCE:
        return FieldResult(
            field="net_contents",
            match_type="unit_normalized",
            expected=expected,
            found=actual,
            status="PASS",
            reason=f"Net contents match within {NET_CONTENTS_ML_TOLERANCE:g} ml tolerance.",
            score=difference,
        )

    return _fail(
        "net_contents",
        expected,
        actual,
        f"Net contents difference {difference:g} ml exceeds {NET_CONTENTS_ML_TOLERANCE:g} ml tolerance.",
        match_type="unit_normalized",
        score=difference,
    )


def compare_government_warning(expected: str, actual: str | None) -> FieldResult:
    missing = _missing_reason(expected, actual)
    if missing:
        return _fail("government_warning", expected, _display_value(actual), missing, match_type="missing")

    if expected == actual:
        return FieldResult(
            field="government_warning",
            match_type="exact",
            expected=expected,
            found=actual,
            status="PASS",
            reason="Government warning is an exact case-sensitive match.",
        )

    return _fail(
        "government_warning",
        expected,
        actual,
        "Government warning is not an exact case-sensitive match.",
        match_type="exact",
    )


def _compare_fuzzy(field_name: str, expected: str, actual: str | None) -> FieldResult:
    missing = _missing_reason(expected, actual)
    if missing:
        return _fail(field_name, expected, _display_value(actual), missing, match_type="missing")

    normalized_expected = _normalize_text(expected)
    normalized_actual = _normalize_text(actual)
    score = float(fuzz.token_set_ratio(normalized_expected, normalized_actual))

    if score >= FUZZY_THRESHOLD:
        return FieldResult(
            field=field_name,
            match_type="fuzzy",
            expected=expected,
            found=actual,
            status="PASS",
            reason=f"Fuzzy score {score:g} meets threshold {FUZZY_THRESHOLD:g}.",
            score=score,
        )

    return _fail(
        field_name,
        expected,
        actual,
        f"Fuzzy score {score:g} is below threshold {FUZZY_THRESHOLD:g}.",
        match_type="fuzzy",
        score=score,
    )


def _normalize_text(value: str) -> str:
    without_punctuation = value.translate(str.maketrans("", "", string.punctuation))
    return " ".join(without_punctuation.lower().split())


def _normalize_country(value: str) -> str:
    normalized = _normalize_text(value)
    return _COUNTRY_SYNONYMS.get(normalized, normalized)


def _parse_abv(value: str) -> float | None:
    stripped = value.strip()
    if _ABV_NUMBER_ONLY_PATTERN.fullmatch(stripped):
        return float(stripped)

    proof_match = _ABV_PROOF_PATTERN.search(stripped)
    if proof_match is not None:
        return float(proof_match.group("value")) / 2

    match = _ABV_WITH_UNIT_PATTERN.search(stripped)
    if match is None:
        return None
    return float(match.group("value"))


def _parse_net_contents_ml(value: str) -> float | None:
    match = _NET_CONTENTS_PATTERN.search(value.strip())
    if not match:
        return None

    amount = float(match.group("value"))
    unit = match.group("unit").lower()
    unit_key = re.sub(r"[\s.]+", "", unit)
    if unit.startswith("l"):
        return amount * 1000
    if unit_key in {"floz", "fluidounce", "fluidounces", "oz"}:
        return amount * US_FLUID_OUNCE_TO_ML
    return amount


def _missing_reason(expected: str, actual: str | None) -> str | None:
    if not expected.strip():
        return "Expected value is missing or blank."
    if actual is None or not actual.strip():
        return "Extracted value is missing or blank."
    return None


def _display_value(value: str | None) -> str:
    return "" if value is None else value


def _fail(
    field_name: str,
    expected: str,
    actual: str,
    reason: str,
    match_type: str,
    score: float | None = None,
) -> FieldResult:
    return FieldResult(
        field=field_name,
        match_type=match_type,
        expected=expected,
        found=actual,
        status="FAIL",
        reason=reason,
        score=score,
    )
