from __future__ import annotations

import asyncio
import io
import json
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

    async def extract_label(self, image_bytes: bytes, content_type: str) -> ExtractedLabel:
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
        "message": "Choose a label image to continue."
    }


def test_verify_rejects_missing_required_field(client: TestClient) -> None:
    _override_vision_service(FakeVisionService())
    data = _form_data()
    data.pop("brand_name")

    response = client.post("/verify", data=data, files=_image_file())

    assert response.status_code == 422
    assert response.json() == {
        "message": "Enter Brand Name."
    }


def test_verify_rejects_blank_required_field(client: TestClient) -> None:
    _override_vision_service(FakeVisionService())
    data = _form_data(brand_name="  ")

    response = client.post("/verify", data=data, files=_image_file())

    assert response.status_code == 400
    assert response.json() == {"message": "Enter Brand Name."}


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


def test_verify_rejects_mime_type_that_does_not_match_image(client: TestClient) -> None:
    _override_vision_service(FakeVisionService())
    output = io.BytesIO()
    Image.new("RGB", (32, 32), "white").save(output, format="JPEG")

    response = client.post(
        "/verify",
        data=_form_data(),
        files={"image": ("label.png", output.getvalue(), "image/png")},
    )

    assert response.status_code == 400
    assert response.json() == {"message": "The file type does not match the image contents."}


@pytest.mark.parametrize(
    ("field_name", "value", "message"),
    [
        ("abv", "not a percentage", "Enter an alcohol percentage between 0 and 100, such as 13.5%."),
        ("abv", "101%", "Enter an alcohol percentage between 0 and 100, such as 13.5%."),
        ("net_contents", "one bottle", "Enter a positive container size in mL or L, such as 750 mL."),
        ("net_contents", "0 ml", "Enter a positive container size in mL or L, such as 750 mL."),
    ],
)
def test_verify_rejects_invalid_application_formats(
    client: TestClient,
    field_name: str,
    value: str,
    message: str,
) -> None:
    _override_vision_service(FakeVisionService())

    response = client.post(
        "/verify",
        data=_form_data(**{field_name: value}),
        files=_image_file(),
    )

    assert response.status_code == 400
    assert response.json() == {"message": message}


def test_imperfect_image_partial_extraction_degrades_to_needs_review(client: TestClient) -> None:
    service = FakeVisionService(
        ExtractedLabel(brand_name="Acme Reserve", government_warning=None)
    )
    _override_vision_service(service)

    response = client.post("/verify", data=_form_data(), files=_image_file())

    assert response.status_code == 200
    assert response.json()["verdict"] == "NEEDS_REVIEW"
    assert _field(response.json(), "brand_name")["status"] == "PASS"
    assert _field(response.json(), "government_warning")["status"] == "FAIL"


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


class ConcurrentVisionService:
    def __init__(self, release_after: int = 3, fail_call: int | None = None) -> None:
        self.release_after = release_after
        self.fail_call = fail_call
        self.started = 0
        self.active = 0
        self.max_active = 0
        self.release = asyncio.Event()

    async def extract_label(self, image_bytes: bytes, content_type: str) -> ExtractedLabel:
        call_number = self.started
        self.started += 1
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        if self.started >= self.release_after:
            self.release.set()
        try:
            await asyncio.wait_for(self.release.wait(), timeout=1)
            await asyncio.sleep(0.01)
            if call_number == self.fail_call:
                raise RuntimeError("isolated secret")
            if call_number == 2:
                return _matching_extracted(country="France")
            return _matching_extracted()
        finally:
            self.active -= 1


def test_batch_processes_concurrently_with_bounded_limit_and_correct_summary(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ConcurrentVisionService(release_after=3)
    _override_vision_service(service)
    monkeypatch.setenv("BATCH_CONCURRENCY_LIMIT", "3")

    response = client.post(
        "/verify/batch",
        data={"applications": json.dumps([_form_data() for _ in range(6)])},
        files=_batch_image_files(6),
    )

    assert response.status_code == 200
    body = response.json()
    assert service.max_active == 3
    assert service.started == 6
    assert body["summary"] == {"passed": 5, "needs_review": 1, "total": 6}
    assert [item["index"] for item in body["items"]] == list(range(6))
    assert [item["filename"] for item in body["items"]] == [
        f"label-{index + 1}.png" for index in range(6)
    ]
    review_items = [item for item in body["items"] if item["outcome"] == "NEEDS_REVIEW"]
    assert len(review_items) == 1
    assert review_items[0]["result"]["verdict"] == "NEEDS_REVIEW"


def test_batch_isolates_item_error_and_counts_it_as_needs_review(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ConcurrentVisionService(release_after=3, fail_call=1)
    _override_vision_service(service)
    monkeypatch.setenv("BATCH_CONCURRENCY_LIMIT", "3")

    response = client.post(
        "/verify/batch",
        data={"applications": json.dumps([_form_data() for _ in range(3)])},
        files=_batch_image_files(3),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["summary"] == {"passed": 1, "needs_review": 2, "total": 3}
    assert [item["index"] for item in body["items"]] == [0, 1, 2]
    error_items = [item for item in body["items"] if item["outcome"] == "ERROR"]
    assert len(error_items) == 1
    assert error_items[0]["result"] is None
    assert error_items[0]["error"] == "We could not process this label. Please try this label again."
    assert "secret" not in response.text
    assert sum(item["outcome"] == "PASS" for item in body["items"]) == 1
    assert sum(item["outcome"] == "NEEDS_REVIEW" for item in body["items"]) == 1


def test_batch_returns_invalid_image_as_item_error_without_failing_sibling(
    client: TestClient,
) -> None:
    _override_vision_service(FakeVisionService())
    files = _batch_image_files(1)
    files.append(("images", ("broken.png", b"not an image", "image/png")))

    response = client.post(
        "/verify/batch",
        data={"applications": json.dumps([_form_data(), _form_data()])},
        files=files,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["summary"] == {"passed": 1, "needs_review": 1, "total": 2}
    assert body["items"][0]["outcome"] == "PASS"
    assert body["items"][1]["outcome"] == "ERROR"
    assert body["items"][1]["error"] == "Uploaded file is not a valid image."


@pytest.mark.parametrize(
    ("application_case", "file_count", "message"),
    [
        ("invalid", 1, "Batch application information must be valid JSON."),
        ("empty", 1, "Add at least one label to the batch."),
        (
            "two",
            1,
            "Each batch application must have one label image.",
        ),
        (
            "eleven",
            11,
            "A batch can contain no more than 10 labels.",
        ),
    ],
)
def test_batch_rejects_structural_errors(
    client: TestClient,
    application_case: str,
    file_count: int,
    message: str,
) -> None:
    _override_vision_service(FakeVisionService())
    application_values = {
        "invalid": "not-json",
        "empty": json.dumps([]),
        "two": json.dumps([_form_data() for _ in range(2)]),
        "eleven": json.dumps([_form_data() for _ in range(11)]),
    }

    response = client.post(
        "/verify/batch",
        data={"applications": application_values[application_case]},
        files=_batch_image_files(file_count),
    )

    assert response.status_code == 400
    assert response.json() == {"message": message}


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


def _batch_image_files(count: int) -> list[tuple[str, tuple[str, bytes, str]]]:
    return [
        ("images", (f"label-{index + 1}.png", _image_bytes(), "image/png"))
        for index in range(count)
    ]


def _field(body: dict[str, object], field_name: str) -> dict[str, object]:
    fields = body["fields"]
    assert isinstance(fields, list)
    for field in fields:
        assert isinstance(field, dict)
        if field["field_name"] == field_name:
            return field
    raise AssertionError(f"Missing field {field_name}")
