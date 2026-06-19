from __future__ import annotations

import io
from typing import Any

import pytest
from PIL import Image

from app.models import ExtractedLabel
from app.vision import (
    DEFAULT_VISION_MODEL,
    EXTRACTED_LABEL_FIELDS,
    EXTRACTION_PROMPT,
    MAX_IMAGE_SIDE,
    OpenAIVisionService,
    VisionInputError,
    VisionService,
    preprocess_image_for_vision,
)


def _image_bytes(size: tuple[int, int], image_format: str = "PNG") -> bytes:
    image = Image.new("RGB", size, color=(240, 240, 240))
    output = io.BytesIO()
    image.save(output, format=image_format)
    return output.getvalue()


def _decoded_image(image_bytes: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(image_bytes))
    image.load()
    return image


class FakeResponses:
    def __init__(
        self,
        payload: dict[str, Any] | None = None,
        output_text: str | None = None,
        exception: Exception | None = None,
    ) -> None:
        self.payload = payload
        self.output_text = output_text
        self.exception = exception
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.exception is not None:
            raise self.exception
        if self.payload is not None:
            return {"output": [{"content": [{"parsed": self.payload}]}]}
        return {"output_text": self.output_text}


class FakeOpenAIClient:
    def __init__(
        self,
        payload: dict[str, Any] | None = None,
        output_text: str | None = None,
        exception: Exception | None = None,
    ) -> None:
        self.responses = FakeResponses(
            payload=payload,
            output_text=output_text,
            exception=exception,
        )


class FakeVisionService:
    def __init__(self, extracted: ExtractedLabel) -> None:
        self.extracted = extracted

    def extract_label(self, image_bytes: bytes, content_type: str) -> ExtractedLabel:
        return self.extracted


def test_preprocess_downscales_large_image_and_outputs_rgb_jpeg() -> None:
    processed = preprocess_image_for_vision(_image_bytes((3200, 1800)))

    with _decoded_image(processed) as image:
        assert image.format == "JPEG"
        assert image.mode == "RGB"
        assert max(image.size) == MAX_IMAGE_SIDE
        assert image.size == (1600, 900)


def test_preprocess_does_not_upscale_small_image() -> None:
    processed = preprocess_image_for_vision(_image_bytes((640, 480)))

    with _decoded_image(processed) as image:
        assert image.size == (640, 480)


def test_preprocess_rejects_invalid_image_bytes() -> None:
    with pytest.raises(VisionInputError, match="valid image"):
        preprocess_image_for_vision(b"not an image")


def test_structured_request_uses_model_image_input_schema_and_prompt() -> None:
    payload = {field: None for field in EXTRACTED_LABEL_FIELDS}
    payload["brand_name"] = "Acme Reserve"
    client = FakeOpenAIClient(payload=payload)
    service = OpenAIVisionService(
        client=client,
        model=DEFAULT_VISION_MODEL,
        timeout_seconds=4.0,
    )

    result = service.extract_label(_image_bytes((640, 480)), "image/png")

    assert result.brand_name == "Acme Reserve"
    assert len(client.responses.calls) == 1
    call = client.responses.calls[0]
    assert call["model"] == DEFAULT_VISION_MODEL
    assert call["timeout"] == 4.0

    content = call["input"][0]["content"]
    assert content[0] == {"type": "input_text", "text": EXTRACTION_PROMPT}
    assert content[1]["type"] == "input_image"
    assert content[1]["image_url"].startswith("data:image/jpeg;base64,")
    assert content[1]["detail"] == "high"

    schema_format = call["text"]["format"]
    assert schema_format["type"] == "json_schema"
    assert schema_format["strict"] is True
    assert schema_format["schema"]["required"] == list(EXTRACTED_LABEL_FIELDS)
    assert schema_format["schema"]["additionalProperties"] is False
    assert set(schema_format["schema"]["properties"]) == set(EXTRACTED_LABEL_FIELDS)
    assert schema_format["schema"]["properties"]["government_warning"]["type"] == ["string", "null"]
    assert "Use null" in EXTRACTION_PROMPT
    assert "copy the warning verbatim" in EXTRACTION_PROMPT
    assert "blurry, angled, partially cropped, or has glare" in EXTRACTION_PROMPT


def test_extract_label_preserves_partial_null_data() -> None:
    payload = {field: None for field in EXTRACTED_LABEL_FIELDS}
    payload.update(
        {
            "brand_name": "Acme Reserve",
            "abv": "13.5%",
            "government_warning": None,
        }
    )
    service = OpenAIVisionService(client=FakeOpenAIClient(payload=payload))

    result = service.extract_label(_image_bytes((640, 480)), "image/png")

    assert result == ExtractedLabel(
        brand_name="Acme Reserve",
        product_class=None,
        producer=None,
        country=None,
        abv="13.5%",
        net_contents=None,
        government_warning=None,
    )


def test_extract_label_returns_empty_label_for_malformed_output() -> None:
    service = OpenAIVisionService(client=FakeOpenAIClient(output_text="not json"))

    result = service.extract_label(_image_bytes((640, 480)), "image/png")

    assert result == ExtractedLabel()


def test_extract_label_returns_empty_label_for_malformed_structured_payload() -> None:
    payload = {field: None for field in EXTRACTED_LABEL_FIELDS}
    payload["brand_name"] = ["not", "a", "string"]
    service = OpenAIVisionService(client=FakeOpenAIClient(payload=payload))

    result = service.extract_label(_image_bytes((640, 480)), "image/png")

    assert result == ExtractedLabel()


def test_extract_label_returns_empty_label_for_timeout() -> None:
    service = OpenAIVisionService(client=FakeOpenAIClient(exception=TimeoutError()))

    result = service.extract_label(_image_bytes((640, 480)), "image/png")

    assert result == ExtractedLabel()


def test_vision_service_can_be_mocked_with_protocol() -> None:
    service: VisionService = FakeVisionService(
        ExtractedLabel(brand_name="Fixture Brand", government_warning=None)
    )

    result = service.extract_label(b"ignored", "image/jpeg")

    assert result.brand_name == "Fixture Brand"
    assert result.government_warning is None
