from __future__ import annotations

import io
import json
import os
import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
import uvicorn
from PIL import Image
from playwright.sync_api import expect, sync_playwright

from app.main import app


GOVERNMENT_WARNING = (
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
    "abv": "13.5",
    "net_contents": "750 ml",
    "government_warning": GOVERNMENT_WARNING,
}

FORM_FIELD_NAMES = (
    "image",
    "brand_name",
    "product_class",
    "producer",
    "country",
    "abv",
    "net_contents",
    "government_warning",
)


@pytest.fixture(scope="module")
def live_app_url() -> Iterator[str]:
    os.environ.pop("OPENAI_API_KEY", None)
    port = _unused_port()
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    if not server.started:
        server.should_exit = True
        thread.join(timeout=5)
        raise RuntimeError("Uvicorn test server did not start.")

    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_single_label_submit_posts_all_application_fields(
    tmp_path: Path,
    live_app_url: str,
) -> None:
    image_path = tmp_path / "label.png"
    _write_png(image_path)
    submitted: dict[str, str] = {}

    def capture_verify_request(route) -> None:
        body = route.request.post_data_buffer
        submitted["body"] = bytes(body or b"").decode("latin-1")
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(_verification_response()),
        )

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        try:
            page.route("**/verify", capture_verify_request)
            page.goto(live_app_url)

            page.set_input_files("#image", str(image_path))
            for field_name, value in APPLICATION.items():
                fill_application_field(page, field_name, value)
            page.get_by_role("button", name="Check This Label").click()

            expect(page.locator("#result-verdict")).to_have_text("APPROVED")
        finally:
            browser.close()

    body = submitted["body"]
    for field_name in FORM_FIELD_NAMES:
        assert f'name="{field_name}"' in body


def test_frontend_uses_image_picker_and_numeric_net_contents_controls(
    live_app_url: str,
) -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        try:
            page.goto(live_app_url)

            expect(page.locator("#image")).to_have_attribute("accept", "image/*")
            expect(page.locator("#abv")).to_have_attribute("type", "number")
            expect(page.locator("#net_contents_amount")).to_have_attribute("type", "number")

            page.get_by_role("button", name="Batch of Labels").click()
            expect(page.locator('[data-name="image"]').first).to_have_attribute("accept", "image/*")
            expect(page.locator('[data-name="abv"]').first).to_have_attribute("type", "number")
            expect(page.locator('[data-name="net_contents_amount"]').first).to_have_attribute("type", "number")
        finally:
            browser.close()


def test_batch_copy_warning_fills_only_empty_warning_fields(
    live_app_url: str,
) -> None:
    first_warning = GOVERNMENT_WARNING
    second_warning = "Already typed warning"

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        try:
            page.goto(live_app_url)
            page.get_by_role("button", name="Batch of Labels").click()
            warnings = page.locator('[data-name="government_warning"]')
            warnings.nth(0).fill(first_warning)
            warnings.nth(1).fill(second_warning)
            page.get_by_role("button", name="Add Another Label").click()

            page.get_by_role("button", name="Copy warning to all labels").click()

            expect(warnings.nth(1)).to_have_value(second_warning)
            expect(warnings.nth(2)).to_have_value(first_warning)
        finally:
            browser.close()


def _verification_response() -> dict[str, object]:
    return {
        "verdict": "APPROVED",
        "results": [
            {
                "field": field_name,
                "match_type": "exact",
                "expected": APPLICATION[field_name],
                "found": APPLICATION[field_name],
                "status": "PASS",
                "reason": "Values match.",
            }
            for field_name in APPLICATION
        ],
        "latency_ms": 123,
    }


def fill_application_field(page, field_name: str, value: str) -> None:
    if field_name == "net_contents":
        amount, unit = value.split(" ", 1)
        page.locator('[name="net_contents_amount"]').fill(amount)
        page.locator('[name="net_contents_unit"]').select_option(unit)
        return
    page.locator(f'[name="{field_name}"]').fill(value)


def _write_png(path: Path) -> None:
    image = Image.new("RGB", (32, 32), color=(240, 240, 240))
    output = io.BytesIO()
    image.save(output, format="PNG")
    path.write_bytes(output.getvalue())


def _unused_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
