from __future__ import annotations

import argparse
import io
import json
import statistics
import time
from dataclasses import asdict, dataclass
from textwrap import wrap

import httpx
from PIL import Image, ImageDraw, ImageFilter, ImageFont


WARNING = (
    "GOVERNMENT WARNING: (1) According to the Surgeon General, women should not drink "
    "alcoholic beverages during pregnancy because of the risk of birth defects. "
    "(2) Consumption of alcoholic beverages impairs your ability to drive a car or "
    "operate machinery, and may cause health problems."
)

APPLICATION = {
    "brand_name": "Acme Reserve",
    "product_class": "Red Wine",
    "producer": "Acme Winery LLC",
    "country": "United States",
    "abv": "13.5%",
    "net_contents": "750 ml",
    "government_warning": WARNING,
}


@dataclass
class Check:
    name: str
    passed: bool
    status_code: int
    wall_ms: int
    server_ms: int | None = None
    detail: str = ""


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Phase 6 checklist against a deployed URL.")
    parser.add_argument("url", help="Deployment base URL, for example https://example.up.railway.app")
    parser.add_argument("--latency-runs", type=int, default=5)
    args = parser.parse_args()
    base_url = args.url.rstrip("/")
    checks: list[Check] = []

    with httpx.Client(base_url=base_url, timeout=12.0, follow_redirects=True) as client:
        response, wall_ms = timed_request(client, "GET", "/health")
        checks.append(Check("health", response.status_code == 200 and response.json().get("status") == "healthy", response.status_code, wall_ms))

        response, wall_ms = timed_request(client, "POST", "/verify")
        checks.append(Check("empty_submit", response.status_code == 422, response.status_code, wall_ms, detail=response.text[:200]))

        response, wall_ms = timed_request(
            client,
            "POST",
            "/verify",
            data=APPLICATION,
            files={"image": ("label.txt", b"not an image", "text/plain")},
        )
        checks.append(Check("wrong_file_type", response.status_code == 400, response.status_code, wall_ms, detail=response.text[:200]))

        clean_image = label_image(WARNING)
        valid_response, valid_wall_ms = verify(client, APPLICATION, clean_image)
        valid_payload = safe_json(valid_response)
        checks.append(result_check("valid_label_and_correct_warning", valid_response, valid_wall_ms, "PASS"))

        mismatch = {**APPLICATION, "country": "France"}
        response, wall_ms = verify(client, mismatch, clean_image)
        checks.append(result_check("mismatch", response, wall_ms, "NEEDS_REVIEW", "country"))

        case_only = {**APPLICATION, "brand_name": "ACME RESERVE"}
        response, wall_ms = verify(client, case_only, clean_image)
        checks.append(result_check("case_only", response, wall_ms, "PASS"))

        normalized = {**APPLICATION, "abv": "13.50 percent", "net_contents": "0.75 L"}
        response, wall_ms = verify(client, normalized, clean_image)
        checks.append(result_check("abv_and_units_normalization", response, wall_ms, "PASS"))

        response, wall_ms = verify(client, APPLICATION, label_image(None))
        checks.append(result_check("missing_warning", response, wall_ms, "NEEDS_REVIEW", "government_warning"))

        response, wall_ms = verify(client, APPLICATION, label_image(WARNING.lower()))
        checks.append(result_check("wrong_caps_warning", response, wall_ms, "NEEDS_REVIEW", "government_warning"))

        imperfect = imperfect_image(clean_image)
        response, wall_ms = verify(client, APPLICATION, imperfect)
        checks.append(Check(
            "imperfect_image_graceful",
            response.status_code == 200 and safe_json(response).get("verdict") in {"PASS", "NEEDS_REVIEW"},
            response.status_code,
            wall_ms,
            safe_json(response).get("latency_ms"),
            safe_json(response).get("verdict", response.text[:200]),
        ))

        batch_applications = [APPLICATION, mismatch]
        response, wall_ms = timed_request(
            client,
            "POST",
            "/verify/batch",
            data={"applications": json.dumps(batch_applications)},
            files=[
                ("images", ("valid.png", clean_image, "image/png")),
                ("images", ("mismatch.png", clean_image, "image/png")),
            ],
        )
        payload = safe_json(response)
        checks.append(Check(
            "batch_summary",
            response.status_code == 200 and payload.get("summary") == {"passed": 1, "needs_review": 1, "total": 2},
            response.status_code,
            wall_ms,
            payload.get("latency_ms"),
            json.dumps(payload.get("summary", payload))[:300],
        ))

        latency_walls = [valid_wall_ms]
        latency_servers = [valid_payload.get("latency_ms")] if isinstance(valid_payload.get("latency_ms"), int) else []
        for _ in range(max(0, args.latency_runs - 1)):
            response, wall_ms = verify(client, APPLICATION, clean_image)
            payload = safe_json(response)
            latency_walls.append(wall_ms)
            if isinstance(payload.get("latency_ms"), int):
                latency_servers.append(payload["latency_ms"])
        latency_passed = len(latency_servers) == args.latency_runs and max(latency_walls) < 5000 and max(latency_servers) < 5000
        checks.append(Check(
            "single_label_speed",
            latency_passed,
            valid_response.status_code,
            max(latency_walls),
            max(latency_servers) if latency_servers else None,
            f"runs={args.latency_runs} wall_ms={latency_walls} server_ms={latency_servers}",
        ))

    output = {
        "url": base_url,
        "passed": all(check.passed for check in checks),
        "checks": [asdict(check) for check in checks],
        "latency": {
            "wall_ms": summarize(latency_walls),
            "server_ms": summarize(latency_servers),
        },
    }
    print(json.dumps(output, indent=2))
    raise SystemExit(0 if output["passed"] else 1)


def verify(client: httpx.Client, application: dict[str, str], image: bytes) -> tuple[httpx.Response, int]:
    return timed_request(
        client,
        "POST",
        "/verify",
        data=application,
        files={"image": ("label.png", image, "image/png")},
    )


def timed_request(client: httpx.Client, method: str, path: str, **kwargs: object) -> tuple[httpx.Response, int]:
    started = time.perf_counter()
    response = client.request(method, path, **kwargs)
    return response, round((time.perf_counter() - started) * 1000)


def result_check(
    name: str,
    response: httpx.Response,
    wall_ms: int,
    verdict: str,
    failed_field: str | None = None,
) -> Check:
    payload = safe_json(response)
    field_failed = failed_field is None or any(
        item.get("field_name") == failed_field and item.get("status") == "FAIL"
        for item in payload.get("fields", [])
    )
    return Check(
        name,
        response.status_code == 200 and payload.get("verdict") == verdict and field_failed,
        response.status_code,
        wall_ms,
        payload.get("latency_ms"),
        payload.get("verdict", response.text[:200]),
    )


def label_image(warning: str | None) -> bytes:
    image = Image.new("RGB", (1400, 1100), "white")
    draw = ImageDraw.Draw(image)
    try:
        title_font = ImageFont.truetype("arialbd.ttf", 48)
        text_font = ImageFont.truetype("arial.ttf", 32)
        warning_font = ImageFont.truetype("arial.ttf", 25)
    except OSError:
        title_font = text_font = warning_font = ImageFont.load_default()
    lines = [
        ("ACME RESERVE", title_font),
        ("Red Wine", text_font),
        ("Produced by Acme Winery LLC", text_font),
        ("United States", text_font),
        ("13.5% Alc./Vol.", text_font),
        ("750 ml", text_font),
    ]
    y = 55
    for text, font in lines:
        draw.text((65, y), text, font=font, fill="black")
        y += 70
    if warning is not None:
        y += 30
        for line in wrap(warning, width=88):
            draw.text((65, y), line, font=warning_font, fill="black")
            y += 42
    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()


def imperfect_image(image_bytes: bytes) -> bytes:
    with Image.open(io.BytesIO(image_bytes)) as image:
        degraded = image.rotate(2, expand=False, fillcolor="white").filter(ImageFilter.GaussianBlur(1.1))
        output = io.BytesIO()
        degraded.save(output, format="PNG", optimize=True)
        return output.getvalue()


def safe_json(response: httpx.Response) -> dict[str, object]:
    try:
        payload = response.json()
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


def summarize(values: list[int]) -> dict[str, float | int | None]:
    if not values:
        return {"min": None, "median": None, "p95": None, "max": None}
    ordered = sorted(values)
    p95_index = min(len(ordered) - 1, max(0, int(0.95 * len(ordered) + 0.9999) - 1))
    return {
        "min": ordered[0],
        "median": round(statistics.median(ordered), 1),
        "p95": ordered[p95_index],
        "max": ordered[-1],
    }


if __name__ == "__main__":
    main()
