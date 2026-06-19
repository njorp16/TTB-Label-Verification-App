from __future__ import annotations

import argparse
import io
import mimetypes
import sys
from pathlib import Path

from dotenv import load_dotenv
from PIL import Image, ImageDraw

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from app.vision import OpenAIVisionService


SAMPLE_WARNING = (
    "GOVERNMENT WARNING: (1) According to the Surgeon General, women should not drink "
    "alcoholic beverages during pregnancy because of the risk of birth defects. "
    "(2) Consumption of alcoholic beverages impairs your ability to drive a car or "
    "operate machinery, and may cause health problems."
)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(
        description="Run VisionService against one label image and print ExtractedLabel JSON."
    )
    parser.add_argument(
        "image_path",
        nargs="?",
        type=Path,
        help="Optional path to a sample label image. If omitted, a synthetic sample label is generated.",
    )
    args = parser.parse_args()

    load_dotenv()

    image_bytes, content_type = _load_image(args.image_path)
    result = OpenAIVisionService().extract_label(image_bytes, content_type)
    print(result.model_dump_json(indent=2))


def _load_image(image_path: Path | None) -> tuple[bytes, str]:
    if image_path is None:
        return _generated_sample_image(), "image/png"

    image_bytes = image_path.read_bytes()
    content_type = mimetypes.guess_type(image_path.name)[0] or "application/octet-stream"
    return image_bytes, content_type


def _generated_sample_image() -> bytes:
    image = Image.new("RGB", (1400, 950), color="white")
    draw = ImageDraw.Draw(image)

    lines = [
        "ACME RESERVE",
        "Red Wine",
        "Produced by Acme Winery LLC",
        "United States",
        "13.5% Alc./Vol.",
        "750 ml",
        "",
        SAMPLE_WARNING,
    ]

    y = 60
    for line in lines:
        for wrapped in _wrap_line(line, max_chars=86):
            draw.text((70, y), wrapped, fill="black")
            y += 38
        y += 18

    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _wrap_line(line: str, max_chars: int) -> list[str]:
    if not line:
        return [""]

    words = line.split()
    wrapped: list[str] = []
    current: list[str] = []
    current_len = 0

    for word in words:
        next_len = current_len + len(word) + (1 if current else 0)
        if current and next_len > max_chars:
            wrapped.append(" ".join(current))
            current = [word]
            current_len = len(word)
        else:
            current.append(word)
            current_len = next_len

    if current:
        wrapped.append(" ".join(current))
    return wrapped


if __name__ == "__main__":
    main()
