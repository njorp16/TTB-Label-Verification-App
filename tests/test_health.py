import asyncio

from fastapi.testclient import TestClient

import app.main as main_module
from app.main import app


client = TestClient(app)


def test_health_returns_healthy_status() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "healthy"
    assert isinstance(response.json()["vision_configured"], bool)


def test_startup_model_validation_skips_without_openai_key(
    monkeypatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    def fail_if_called():
        raise AssertionError("startup should not build a vision client without an API key")

    monkeypatch.setattr(main_module, "_get_openai_vision_service", fail_if_called)

    asyncio.run(main_module.validate_vision_model_on_startup())


def test_startup_model_validation_runs_with_openai_key(monkeypatch) -> None:
    class FakeService:
        validated = False

        async def validate_model_config(self) -> None:
            self.validated = True

    service = FakeService()
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(main_module, "_get_openai_vision_service", lambda: service)

    asyncio.run(main_module.validate_vision_model_on_startup())

    assert service.validated is True


def test_startup_model_validation_fails_loudly_with_openai_key(monkeypatch) -> None:
    class FakeService:
        async def validate_model_config(self) -> None:
            raise RuntimeError("VISION_MODEL='bad-model' is not present in the OpenAI model list.")

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(main_module, "_get_openai_vision_service", lambda: FakeService())

    try:
        asyncio.run(main_module.validate_vision_model_on_startup())
    except RuntimeError as exc:
        assert "VISION_MODEL='bad-model'" in str(exc)
    else:
        raise AssertionError("startup should fail for an unknown model")


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
    script = response.text
    assert 'fetch("/verify"' in script
    assert script.index("const body = new FormData(form)") < script.index("setLoading(true)")
    assert 'approved ? "APPROVED" : "NEEDS REVIEW"' in response.text
    assert "Application says" in response.text
    assert "Label says" in response.text
    assert "payload.results" in script
    assert "field.field" in script
    assert "field.found" in script


def test_frontend_constrains_numeric_and_unit_inputs() -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert 'id="abv" name="abv" type="number" min="0.1" max="100" step="0.1"' in response.text
    assert 'data-name="abv" type="number" min="0.1" max="100" step="0.1"' in response.text
    assert 'id="net_contents_amount" name="net_contents_amount" type="number"' in response.text
    assert 'data-name="net_contents_amount" type="number"' in response.text

    script_response = client.get("/static/app.js")
    assert script_response.status_code == 200
    assert "net_contents_unit" in response.text
    assert "fl oz" in response.text
    assert "mL, L, or fl oz" in script_response.text


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
