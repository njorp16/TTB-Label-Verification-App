import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from app.comparison import verify_label
from app.models import (
    ApplicationData,
    BatchItemResult,
    BatchSummary,
    BatchVerificationResult,
    VerificationResult,
)
from app.vision import (
    OpenAIVisionService,
    VisionInputError,
    VisionService,
    VisionServiceError,
    preprocess_image_for_vision,
)

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIR = BASE_DIR / "frontend"
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
ACCEPTED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
LATENCY_BUDGET_MS = 5000
MAX_BATCH_ITEMS = 10
DEFAULT_BATCH_CONCURRENCY_LIMIT = 4

logger = logging.getLogger(__name__)

app = FastAPI(title="TTB Label Verification")


def get_vision_service() -> VisionService:
    return OpenAIVisionService()


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    _request: Request,
    _exc: RequestValidationError,
) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={"message": "Please provide an image and all required application fields."},
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(_request: Request, exc: HTTPException) -> JSONResponse:
    detail = exc.detail
    if isinstance(detail, dict) and isinstance(detail.get("message"), str):
        message = detail["message"]
    elif isinstance(detail, str):
        message = detail
    else:
        message = "Request could not be processed."
    return JSONResponse(status_code=exc.status_code, content={"message": message})


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "healthy"}


@app.post("/verify", response_model=VerificationResult)
async def verify(
    image: UploadFile = File(...),
    brand_name: str = Form(...),
    product_class: str = Form(...),
    producer: str = Form(...),
    country: str = Form(...),
    abv: str = Form(...),
    net_contents: str = Form(...),
    government_warning: str = Form(...),
    vision_service: VisionService = Depends(get_vision_service),
) -> VerificationResult:
    started_at = time.perf_counter()

    try:
        application = ApplicationData(
            brand_name=brand_name,
            product_class=product_class,
            producer=producer,
            country=country,
            abv=abv,
            net_contents=net_contents,
            government_warning=government_warning,
        )
        return await _verify_one(image, application, vision_service, started_at)
    except HTTPException:
        raise
    except VisionInputError as exc:
        raise HTTPException(status_code=400, detail={"message": str(exc)}) from exc
    except VisionServiceError as exc:
        raise HTTPException(
            status_code=500,
            detail={"message": "We could not verify this label right now. Please try again."},
        ) from exc
    except Exception as exc:
        logger.exception("Verification request failed.")
        raise HTTPException(
            status_code=500,
            detail={"message": "We could not verify this label right now. Please try again."},
        ) from exc


@app.post("/verify/batch", response_model=BatchVerificationResult)
async def verify_batch(
    applications: str = Form(...),
    images: list[UploadFile] = File(default=[]),
    vision_service: VisionService = Depends(get_vision_service),
) -> BatchVerificationResult:
    started_at = time.perf_counter()
    raw_applications = _parse_batch_applications(applications)
    _validate_batch_shape(raw_applications, images)

    semaphore = asyncio.Semaphore(_batch_concurrency_limit())
    tasks = [
        _process_batch_item(
            index=index,
            raw_application=raw_application,
            image=image,
            vision_service=vision_service,
            semaphore=semaphore,
        )
        for index, (raw_application, image) in enumerate(zip(raw_applications, images))
    ]
    items = await asyncio.gather(*tasks)
    passed = sum(item.outcome == "PASS" for item in items)
    total = len(items)
    result = BatchVerificationResult(
        summary=BatchSummary(
            passed=passed,
            needs_review=total - passed,
            total=total,
        ),
        items=items,
        latency_ms=_elapsed_ms(started_at),
    )
    logger.info(
        "Batch verification completed: latency_ms=%s passed=%s needs_review=%s total=%s",
        result.latency_ms,
        result.summary.passed,
        result.summary.needs_review,
        result.summary.total,
    )
    return result


app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "index.html")


def _validate_application_fields(fields: dict[str, str]) -> None:
    if any(not value.strip() for value in fields.values()):
        raise HTTPException(
            status_code=400,
            detail={"message": "Please provide all required application fields."},
        )


async def _verify_one(
    image: UploadFile,
    application: ApplicationData,
    vision_service: VisionService,
    started_at: float | None = None,
) -> VerificationResult:
    started_at = started_at if started_at is not None else time.perf_counter()
    _validate_application_fields(application.model_dump())
    _validate_upload_type(image)
    image_bytes = await image.read()
    _validate_upload_size(image_bytes)
    processed_image = await asyncio.to_thread(preprocess_image_for_vision, image_bytes)
    extracted = await vision_service.extract_label(processed_image, "image/jpeg")
    result = verify_label(application, extracted)
    result.latency_ms = _elapsed_ms(started_at)
    _log_verification_latency(result)
    return result


async def _process_batch_item(
    index: int,
    raw_application: Any,
    image: UploadFile,
    vision_service: VisionService,
    semaphore: asyncio.Semaphore,
) -> BatchItemResult:
    filename = _safe_filename(image.filename, index)
    async with semaphore:
        started_at = time.perf_counter()
        try:
            application = ApplicationData.model_validate(raw_application)
            result = await _verify_one(image, application, vision_service, started_at)
            return BatchItemResult(
                index=index,
                filename=filename,
                outcome=result.verdict,
                result=result,
            )
        except (ValidationError, HTTPException, VisionInputError) as exc:
            message = _batch_input_error_message(exc)
        except VisionServiceError:
            message = "We could not read this label right now. Please try this label again."
        except Exception:
            logger.exception("Batch item %s failed.", index)
            message = "We could not process this label. Please try this label again."

        return BatchItemResult(
            index=index,
            filename=filename,
            outcome="ERROR",
            error=message,
        )


def _parse_batch_applications(value: str) -> list[Any]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=400,
            detail={"message": "Batch application information must be valid JSON."},
        ) from exc
    if not isinstance(parsed, list):
        raise HTTPException(
            status_code=400,
            detail={"message": "Batch application information must be a list."},
        )
    return parsed


def _validate_batch_shape(applications: list[Any], images: list[UploadFile]) -> None:
    if not applications:
        raise HTTPException(
            status_code=400,
            detail={"message": "Add at least one label to the batch."},
        )
    if len(applications) > MAX_BATCH_ITEMS:
        raise HTTPException(
            status_code=400,
            detail={"message": f"A batch can contain no more than {MAX_BATCH_ITEMS} labels."},
        )
    if len(applications) != len(images):
        raise HTTPException(
            status_code=400,
            detail={"message": "Each batch application must have one label image."},
        )


def _batch_concurrency_limit() -> int:
    raw_value = os.getenv("BATCH_CONCURRENCY_LIMIT", str(DEFAULT_BATCH_CONCURRENCY_LIMIT))
    try:
        configured = int(raw_value)
    except ValueError:
        configured = DEFAULT_BATCH_CONCURRENCY_LIMIT
    return max(1, min(configured, MAX_BATCH_ITEMS))


def _batch_input_error_message(exc: Exception) -> str:
    if isinstance(exc, HTTPException):
        detail = exc.detail
        if isinstance(detail, dict) and isinstance(detail.get("message"), str):
            return detail["message"]
    if isinstance(exc, VisionInputError):
        return str(exc)
    return "Please provide all required application fields for this label."


def _safe_filename(filename: str | None, index: int) -> str:
    if not filename:
        return f"Label {index + 1}"
    return filename.replace("\\", "/").rsplit("/", 1)[-1]


def _validate_upload_type(image: UploadFile) -> None:
    if image.content_type not in ACCEPTED_IMAGE_TYPES:
        raise HTTPException(
            status_code=400,
            detail={"message": "Upload must be a JPEG, PNG, or WEBP image."},
        )


def _validate_upload_size(image_bytes: bytes) -> None:
    if not image_bytes:
        raise HTTPException(
            status_code=400,
            detail={"message": "Uploaded file is empty."},
        )
    if len(image_bytes) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail={"message": "Uploaded image must be 10 MB or smaller."},
        )


def _elapsed_ms(started_at: float) -> int:
    return max(0, round((time.perf_counter() - started_at) * 1000))


def _log_verification_latency(result: VerificationResult) -> None:
    latency_ms = result.latency_ms
    if latency_ms is None:
        return

    extra = {
        "latency_ms": latency_ms,
        "latency_budget_ms": LATENCY_BUDGET_MS,
        "verdict": result.verdict,
        "over_budget": latency_ms > LATENCY_BUDGET_MS,
    }
    if latency_ms > LATENCY_BUDGET_MS:
        logger.warning(
            "Verification exceeded latency budget: latency_ms=%s verdict=%s",
            latency_ms,
            result.verdict,
            extra=extra,
        )
        return

    logger.info(
        "Verification completed: latency_ms=%s verdict=%s",
        latency_ms,
        result.verdict,
        extra=extra,
    )
