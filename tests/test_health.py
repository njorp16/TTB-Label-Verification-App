from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_health_returns_healthy_status() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "healthy"


def test_frontend_loads() -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert "Check a Label" in response.text


def test_frontend_contains_complete_verification_form() -> None:
    response = client.get("/")

    assert response.status_code == 200
    for field_name in (
        "image",
        "brand_name",
        "product_class",
        "producer",
        "country",
        "abv",
        "net_contents",
        "government_warning",
    ):
        assert f'name="{field_name}"' in response.text

    assert 'id="submit-button"' in response.text
    assert "Check This Label" in response.text


def test_frontend_contains_accessible_feedback_regions() -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert 'id="error-summary"' in response.text
    assert 'role="alert"' in response.text
    assert 'id="loading-status"' in response.text
    assert 'aria-live="polite"' in response.text
    assert 'id="result-verdict"' in response.text
    assert 'id="failed-results"' in response.text
    assert 'id="passed-results"' in response.text
    assert 'aria-atomic="true"' in response.text
    assert 'maxlength="4000"' in response.text


def test_frontend_script_uses_existing_verify_contract() -> None:
    response = client.get("/static/app.js")

    assert response.status_code == 200
    assert 'fetch("/verify"' in response.text
    assert "new FormData(form)" in response.text
    assert 'approved ? "APPROVED" : "NEEDS REVIEW"' in response.text
    assert "Application says" in response.text
    assert "Label says" in response.text


def test_frontend_contains_batch_workflow_and_accessible_progress() -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert 'id="batch-mode-button"' in response.text
    assert 'id="batch-form"' in response.text
    assert 'id="batch-item-template"' in response.text
    assert 'id="batch-progress"' in response.text
    assert 'role="status"' in response.text
    assert 'id="batch-summary"' in response.text
    assert 'id="batch-result-items"' in response.text


def test_frontend_script_submits_batch_and_renders_summary_and_drill_down() -> None:
    response = client.get("/static/app.js")

    assert response.status_code == 200
    assert 'fetch("/verify/batch"' in response.text
    assert 'body.append("applications", JSON.stringify(applications))' in response.text
    assert 'body.append("images"' in response.text
    assert 'createSummaryCount("Passed"' in response.text
    assert 'createSummaryCount("Needs Review"' in response.text
    assert 'document.createElement("details")' in response.text
    assert "batchProgressTimer" in response.text
    assert "optimizeImageForUpload" in response.text
    assert "Remove Label" in response.text


def test_frontend_styles_meet_hardening_accessibility_targets() -> None:
    response = client.get("/static/styles.css")

    assert response.status_code == 200
    assert "font-size: 20px" in response.text
    assert "min-height: 56px" in response.text
    assert "prefers-reduced-motion: reduce" in response.text
    assert "animation: none" in response.text
