from __future__ import annotations

import io
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import app.main as main_module
from app.main import app, get_vision_service
from app.models import ExtractedLabel
from app.vision import VisionService


GOVERNMENT_WARNING = (
    "GOVERNMENT WARNING: (1) According to the Surgeon General, women should not drink "
    "alcoholic beverages during pregnancy because of the risk of birth defects. "
    "(2) Consumption of alcoholic beverages impairs your ability to drive a car or "
    "operate machinery, and may cause health problems."
)


class FakeVisionService:
    def __init__(
        self,
        extracted: ExtractedLabel | None = None,
        exception: Exception | None = None,
    ) -> None:
        self.extracted = extracted or _matching_extracted()
        self.exception = exception
        self.calls: list[tuple[bytes, str]] = []

    def extract_label(self, image_bytes: bytes, content_type: str) -> ExtractedLabel:
        self.calls.append((image_bytes, content_type))
        if self.exception is not None:
            raise self.exception
        return self.extracted


@pytest.fixture
def client() -> Iterator[TestClient]:
    app.dependency_overrides.clear()
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_verify_returns_result_with_latency_logs_and_uses_preprocessed_image(
    caplog: pytest.LogCaptureFixture,
    client: TestClient,
) -> None:
    service = FakeVisionService()
    _override_vision_service(service)
    caplog.set_level("INFO", logger="app.main")

    response = client.post("/verify", data=_form_data(), files=_image_file())

    assert response.status_code == 200
    body = response.json()
    assert body["verdict"] == "PASS"
    assert len(body["fields"]) == 7
    assert isinstance(body["latency_ms"], int)

    assert len(service.calls) == 1
    image_bytes, content_type = service.calls[0]
    assert content_type == "image/jpeg"
    with Image.open(io.BytesIO(image_bytes)) as image:
        assert image.format == "JPEG"
    assert "Verification completed" in caplog.text
    assert "verdict=PASS" in caplog.text


def test_verify_logs_warning_when_latency_exceeds_budget(
    caplog: pytest.LogCaptureFixture,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = FakeVisionService()
    _override_vision_service(service)
    caplog.set_level("WARNING", logger="app.main")
    monkeypatch.setattr(main_module, "_elapsed_ms", lambda started_at: 5001)

    response = client.post("/verify", data=_form_data(), files=_image_file())

    assert response.status_code == 200
    assert response.json()["latency_ms"] == 5001
    assert "Verification exceeded latency budget" in caplog.text
    assert "latency_ms=5001" in caplog.text


def test_verify_returns_needs_review_for_mismatched_field(client: TestClient) -> None:
    service = FakeVisionService(_matching_extracted(country="France"))
    _override_vision_service(service)

    response = client.post("/verify", data=_form_data(), files=_image_file())

    assert response.status_code == 200
    body = response.json()
    assert body["verdict"] == "NEEDS_REVIEW"
    assert any(
        field["field_name"] == "country" and field["status"] == "FAIL"
        for field in body["fields"]
    )


def test_verify_government_warning_must_match_exactly(client: TestClient) -> None:
    extracted_warning = GOVERNMENT_WARNING.lower()
    service = FakeVisionService(_matching_extracted(government_warning=extracted_warning))
    _override_vision_service(service)

    response = client.post("/verify", data=_form_data(), files=_image_file())

    assert response.status_code == 200
    government_warning = _field(response.json(), "government_warning")
    assert government_warning["status"] == "FAIL"
    assert government_warning["expected"] == GOVERNMENT_WARNING
    assert government_warning["actual"] == extracted_warning
    assert "exact case-sensitive match" in government_warning["reason"]


def test_verify_rejects_missing_image_with_human_readable_message(client: TestClient) -> None:
    _override_vision_service(FakeVisionService())

    response = client.post("/verify", data=_form_data())

    assert response.status_code == 422
    assert response.json() == {
        "message": "Please provide an image and all required application fields."
    }


def test_verify_rejects_missing_required_field(client: TestClient) -> None:
    _override_vision_service(FakeVisionService())
    data = _form_data()
    data.pop("brand_name")

    response = client.post("/verify", data=data, files=_image_file())

    assert response.status_code == 422
    assert response.json() == {
        "message": "Please provide an image and all required application fields."
    }


def test_verify_rejects_blank_required_field(client: TestClient) -> None:
    _override_vision_service(FakeVisionService())
    data = _form_data(brand_name="  ")

    response = client.post("/verify", data=data, files=_image_file())

    assert response.status_code == 400
    assert response.json() == {"message": "Please provide all required application fields."}


def test_verify_rejects_unsupported_content_type(client: TestClient) -> None:
    _override_vision_service(FakeVisionService())

    response = client.post(
        "/verify",
        data=_form_data(),
        files={"image": ("label.txt", b"not an image", "text/plain")},
    )

    assert response.status_code == 400
    assert response.json() == {"message": "Upload must be a JPEG, PNG, or WEBP image."}


def test_verify_rejects_empty_image_upload(client: TestClient) -> None:
    _override_vision_service(FakeVisionService())

    response = client.post(
        "/verify",
        data=_form_data(),
        files={"image": ("label.png", b"", "image/png")},
    )

    assert response.status_code == 400
    assert response.json() == {"message": "Uploaded file is empty."}


def test_verify_rejects_image_larger_than_limit(client: TestClient) -> None:
    _override_vision_service(FakeVisionService())

    response = client.post(
        "/verify",
        data=_form_data(),
        files={"image": ("label.png", b"0" * (10 * 1024 * 1024 + 1), "image/png")},
    )

    assert response.status_code == 413
    assert response.json() == {"message": "Uploaded image must be 10 MB or smaller."}


def test_verify_rejects_invalid_image_bytes(client: TestClient) -> None:
    _override_vision_service(FakeVisionService())

    response = client.post(
        "/verify",
        data=_form_data(),
        files={"image": ("label.png", b"not an image", "image/png")},
    )

    assert response.status_code == 400
    assert response.json() == {"message": "Uploaded file is not a valid image."}


def test_verify_shapes_unexpected_vision_errors_without_internal_details(
    client: TestClient,
) -> None:
    _override_vision_service(FakeVisionService(exception=RuntimeError("secret traceback detail")))

    response = client.post("/verify", data=_form_data(), files=_image_file())

    assert response.status_code == 500
    body = response.json()
    assert body == {"message": "We could not verify this label right now. Please try again."}
    response_text = response.text.lower()
    assert "traceback" not in response_text
    assert "runtimeerror" not in response_text
    assert "secret" not in response_text


def _override_vision_service(service: VisionService) -> None:
    app.dependency_overrides[get_vision_service] = lambda: service


def _form_data(**overrides: str) -> dict[str, str]:
    data = {
        "brand_name": "Acme Reserve",
        "product_class": "Red Wine",
        "producer": "Acme Winery LLC",
        "country": "United States",
        "abv": "13.5%",
        "net_contents": "750 ml",
        "government_warning": GOVERNMENT_WARNING,
    }
    data.update(overrides)
    return data


def _matching_extracted(**overrides: str) -> ExtractedLabel:
    data = _form_data()
    data.update(overrides)
    return ExtractedLabel(**data)


def _image_file() -> dict[str, tuple[str, bytes, str]]:
    return {"image": ("label.png", _image_bytes(), "image/png")}


def _image_bytes() -> bytes:
    image = Image.new("RGB", (32, 32), color=(240, 240, 240))
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _field(body: dict[str, object], field_name: str) -> dict[str, object]:
    fields = body["fields"]
    assert isinstance(fields, list)
    for field in fields:
        assert isinstance(field, dict)
        if field["field_name"] == field_name:
            return field
    raise AssertionError(f"Missing field {field_name}")
