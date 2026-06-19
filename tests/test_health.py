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


def test_frontend_script_uses_existing_verify_contract() -> None:
    response = client.get("/static/app.js")

    assert response.status_code == 200
    assert 'fetch("/verify"' in response.text
    assert "new FormData(form)" in response.text
    assert 'approved ? "APPROVED" : "NEEDS REVIEW"' in response.text
    assert "Application says" in response.text
    assert "Label says" in response.text
