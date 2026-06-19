from __future__ import annotations

import base64
import io
import json
import logging
import os
from typing import Any, Protocol

from openai import OpenAI
from PIL import Image, UnidentifiedImageError
from pydantic import ValidationError

from app.models import ExtractedLabel


logger = logging.getLogger(__name__)

DEFAULT_VISION_MODEL = "gpt-5.4-mini"
DEFAULT_VISION_TIMEOUT_SECONDS = 4.0
MAX_IMAGE_SIDE = 1600
JPEG_QUALITY = 82

EXTRACTED_LABEL_FIELDS = (
    "brand_name",
    "product_class",
    "producer",
    "country",
    "abv",
    "net_contents",
    "government_warning",
)

EXTRACTION_PROMPT = """Extract visible text from this alcohol beverage label.

Return exactly these seven fields:
- brand_name
- product_class
- producer
- country
- abv
- net_contents
- government_warning

Use null for any field that is unknown, unreadable, not visible, or ambiguous. Do not guess.
For the government_warning field, copy the warning verbatim from the image, preserving case,
punctuation, parentheses, spacing, and line-break-derived spaces as visible. Do not normalize the
government warning. For all other fields, return the most likely visible value as plain text.
If the image is blurry, angled, partially cropped, or has glare, return partial data for readable
fields and null for the rest. Do not fail just because some fields are unreadable.
"""


class VisionInputError(ValueError):
    """Raised when uploaded bytes are not a decodable image."""


class VisionService(Protocol):
    def extract_label(self, image_bytes: bytes, content_type: str) -> ExtractedLabel:
        ...


class OpenAIVisionService:
    def __init__(
        self,
        client: Any | None = None,
        model: str | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        self.model = model or os.getenv("VISION_MODEL", DEFAULT_VISION_MODEL)
        self.timeout_seconds = timeout_seconds or float(
            os.getenv("VISION_TIMEOUT_SECONDS", DEFAULT_VISION_TIMEOUT_SECONDS)
        )
        self._client = client or self._build_client()

    def extract_label(self, image_bytes: bytes, content_type: str) -> ExtractedLabel:
        preprocessed = preprocess_image_for_vision(image_bytes)
        image_data_url = _jpeg_data_url(preprocessed)

        try:
            response = self._client.responses.create(
                model=self.model,
                timeout=self.timeout_seconds,
                text={"format": _structured_output_format()},
                input=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": EXTRACTION_PROMPT},
                            {
                                "type": "input_image",
                                "image_url": image_data_url,
                                "detail": "high",
                            },
                        ],
                    }
                ],
            )
        except Exception as exc:
            logger.warning("Vision extraction request failed: %s", exc.__class__.__name__)
            return _empty_extracted_label()

        payload = _payload_from_response(response)
        if payload is None:
            logger.warning("Vision extraction returned no usable structured payload.")
            return _empty_extracted_label()

        try:
            return ExtractedLabel.model_validate(payload)
        except ValidationError:
            logger.warning("Vision extraction returned malformed structured payload.")
            return _empty_extracted_label()

    @staticmethod
    def _build_client() -> OpenAI:
        if not os.getenv("OPENAI_API_KEY"):
            raise RuntimeError("OPENAI_API_KEY is required to use OpenAIVisionService.")
        return OpenAI(api_key=os.environ["OPENAI_API_KEY"])


def preprocess_image_for_vision(image_bytes: bytes) -> bytes:
    try:
        with Image.open(io.BytesIO(image_bytes)) as image:
            image.load()
            image = image.convert("RGB")
            image.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE), Image.Resampling.LANCZOS)

            output = io.BytesIO()
            image.save(output, format="JPEG", quality=JPEG_QUALITY, optimize=True)
            return output.getvalue()
    except (UnidentifiedImageError, OSError) as exc:
        raise VisionInputError("Uploaded file is not a valid image.") from exc


def _structured_output_format() -> dict[str, Any]:
    return {
        "type": "json_schema",
        "name": "extracted_label",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                field: {"type": ["string", "null"]}
                for field in EXTRACTED_LABEL_FIELDS
            },
            "required": list(EXTRACTED_LABEL_FIELDS),
            "additionalProperties": False,
        },
    }


def _jpeg_data_url(image_bytes: bytes) -> str:
    encoded = base64.b64encode(image_bytes).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def _payload_from_response(response: Any) -> dict[str, Any] | None:
    parsed = _get_value(response, "output_parsed")
    if isinstance(parsed, dict):
        return parsed

    output = _get_value(response, "output")
    payload = _payload_from_output(output)
    if payload is not None:
        return payload

    output_text = _get_value(response, "output_text")
    if isinstance(output_text, str):
        try:
            loaded = json.loads(output_text)
        except json.JSONDecodeError:
            return None
        return loaded if isinstance(loaded, dict) else None

    return None


def _payload_from_output(output: Any) -> dict[str, Any] | None:
    if not isinstance(output, list):
        return None

    for item in output:
        content = _get_value(item, "content")
        if not isinstance(content, list):
            continue
        for content_item in content:
            parsed = _get_value(content_item, "parsed")
            if isinstance(parsed, dict):
                return parsed
    return None


def _get_value(value: Any, key: str) -> Any:
    if isinstance(value, dict):
        return value.get(key)
    return getattr(value, key, None)


def _empty_extracted_label() -> ExtractedLabel:
    return ExtractedLabel(**{field: None for field in EXTRACTED_LABEL_FIELDS})
