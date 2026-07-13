from __future__ import annotations

import httpx

from scripts.live_checklist import result_check


def test_live_checklist_result_check_uses_current_verify_contract() -> None:
    response = _json_response(
        {
            "verdict": "NEEDS_REVIEW",
            "results": [
                {
                    "field": "country",
                    "status": "FAIL",
                }
            ],
            "latency_ms": 123,
        }
    )

    check = result_check("mismatch", response, 150, "NEEDS_REVIEW", "country")

    assert check.passed is True
    assert check.server_ms == 123


def test_live_checklist_result_check_fails_when_expected_field_did_not_fail() -> None:
    response = _json_response(
        {
            "verdict": "NEEDS_REVIEW",
            "results": [
                {
                    "field": "brand_name",
                    "status": "FAIL",
                }
            ],
        }
    )

    check = result_check("mismatch", response, 150, "NEEDS_REVIEW", "country")

    assert check.passed is False


def _json_response(payload: dict[str, object]) -> httpx.Response:
    return httpx.Response(
        200,
        json=payload,
        request=httpx.Request("POST", "https://example.test/verify"),
    )
