from __future__ import annotations

import base64
import io
import json
import logging
import os
from typing import Any, Protocol

from openai import AsyncOpenAI
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import ValidationError

from app.models import ExtractedLabel


logger = logging.getLogger(__name__)

DEFAULT_VISION_MODEL = "gpt-4.1-mini"
LEGACY_VISION_MODEL = "gpt-5.4-mini"
DEFAULT_VISION_TIMEOUT_SECONDS = 4.5
DEFAULT_MAX_IMAGE_SIDE = 1024
DEFAULT_JPEG_QUALITY = 80
DEFAULT_IMAGE_DETAIL = "high"
MAX_IMAGE_PIXELS = 50_000_000
MAX_IMAGE_SIDE = DEFAULT_MAX_IMAGE_SIDE
JPEG_QUALITY = DEFAULT_JPEG_QUALITY

CONTENT_TYPE_BY_FORMAT = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
}

EXTRACTED_LABEL_FIELDS = (
    "brand_name",
    "product_class",
    "producer",
    "country",
    "abv",
    "net_contents",
    "government_warning",
)

EXTRACTION_PROMPT = """Read this alcohol label and return the seven schema fields. Use null when a
field is absent, unreadable, or ambiguous; never guess. For government_warning, copy the warning verbatim, preserving
case, punctuation, parentheses, and visible spacing. For blurry, angled, cropped, or glare-obscured
images, return every readable field and null for the rest."""


class VisionInputError(ValueError):
    """Raised when uploaded bytes are not a decodable image."""


class VisionServiceError(RuntimeError):
    """Raised when the remote vision service cannot return a usable result."""


class VisionService(Protocol):
    async def extract_label(self, image_bytes: bytes, content_type: str) -> ExtractedLabel:
        ...


class OpenAIVisionService:
    def __init__(
        self,
        client: Any | None = None,
        model: str | None = None,
        timeout_seconds: float | None = None,
        image_detail: str | None = None,
    ) -> None:
        configured_model = os.getenv("VISION_MODEL")
        if configured_model == LEGACY_VISION_MODEL:
            logger.info("Upgrading legacy VISION_MODEL to the Phase 6 default.")
            configured_model = DEFAULT_VISION_MODEL
        self.model = model or configured_model or DEFAULT_VISION_MODEL
        self.timeout_seconds = timeout_seconds or _env_float(
            "VISION_TIMEOUT_SECONDS", DEFAULT_VISION_TIMEOUT_SECONDS, minimum=1.0, maximum=4.5
        )
        configured_detail = image_detail or os.getenv("VISION_IMAGE_DETAIL", DEFAULT_IMAGE_DETAIL)
        self.image_detail = configured_detail if configured_detail in {"low", "high", "auto"} else DEFAULT_IMAGE_DETAIL
        self._client = client or self._build_client()

    async def extract_label(self, image_bytes: bytes, content_type: str) -> ExtractedLabel:
        image_data_url = _jpeg_data_url(image_bytes)

        try:
            response = await self._client.responses.create(
                model=self.model,
                timeout=self.timeout_seconds,
                max_output_tokens=600,
                text={"format": _structured_output_format()},
                input=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": EXTRACTION_PROMPT},
                            {
                                "type": "input_image",
                                "image_url": image_data_url,
                                "detail": self.image_detail,
                            },
                        ],
                    }
                ],
            )
        except Exception as exc:
            logger.warning("Vision extraction request failed: %s", exc.__class__.__name__)
            raise VisionServiceError("The label-reading service did not respond.") from exc

        payload = _payload_from_response(response)
        if payload is None:
            logger.warning("Vision extraction returned no usable structured payload.")
            raise VisionServiceError("The label-reading service returned an unusable response.")

        try:
            return ExtractedLabel.model_validate(payload)
        except ValidationError:
            logger.warning("Vision extraction returned malformed structured payload.")
            raise VisionServiceError("The label-reading service returned an unusable response.")

    @staticmethod
    def _build_client() -> AsyncOpenAI:
        if not os.getenv("OPENAI_API_KEY"):
            raise RuntimeError("OPENAI_API_KEY is required to use OpenAIVisionService.")
        return AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"], max_retries=0)


def preprocess_image_for_vision(
    image_bytes: bytes,
    expected_content_type: str | None = None,
) -> bytes:
    try:
        with Image.open(io.BytesIO(image_bytes)) as image:
            decoded_type = CONTENT_TYPE_BY_FORMAT.get(image.format or "")
            if decoded_type is None:
                raise VisionInputError("Upload must decode as a JPEG, PNG, or WEBP image.")
            if expected_content_type is not None and decoded_type != expected_content_type:
                raise VisionInputError("The file type does not match the image contents.")
            if image.width * image.height > MAX_IMAGE_PIXELS:
                raise VisionInputError("Uploaded image is too large. Use an image under 50 megapixels.")
            image.load()
            image = ImageOps.exif_transpose(image).convert("RGB")
            max_side = _env_int("VISION_MAX_IMAGE_SIDE", DEFAULT_MAX_IMAGE_SIDE, 640, 2000)
            quality = _env_int("VISION_JPEG_QUALITY", DEFAULT_JPEG_QUALITY, 60, 95)
            image.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)

            output = io.BytesIO()
            image.save(output, format="JPEG", quality=quality, optimize=True)
            return output.getvalue()
    except VisionInputError:
        raise
    except (Image.DecompressionBombError, UnidentifiedImageError, OSError) as exc:
        raise VisionInputError("Uploaded file is not a valid image.") from exc


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        logger.warning("Invalid %s; using default %s.", name, default)
        return default
    if not minimum <= value <= maximum:
        logger.warning("Out-of-range %s; using default %s.", name, default)
        return default
    return value


def _env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        logger.warning("Invalid %s; using default %s.", name, default)
        return default
    if not minimum <= value <= maximum:
        logger.warning("Out-of-range %s; using default %s.", name, default)
        return default
    return value


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
