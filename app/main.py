import logging
import time
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.comparison import verify_label
from app.models import ApplicationData, VerificationResult
from app.vision import (
    OpenAIVisionService,
    VisionInputError,
    VisionService,
    preprocess_image_for_vision,
)

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIR = BASE_DIR / "frontend"
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
ACCEPTED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
LATENCY_BUDGET_MS = 5000

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
        _validate_application_fields(
            {
                "brand_name": brand_name,
                "product_class": product_class,
                "producer": producer,
                "country": country,
                "abv": abv,
                "net_contents": net_contents,
                "government_warning": government_warning,
            }
        )
        _validate_upload_type(image)
        image_bytes = await image.read()
        _validate_upload_size(image_bytes)

        processed_image = preprocess_image_for_vision(image_bytes)
        application = ApplicationData(
            brand_name=brand_name,
            product_class=product_class,
            producer=producer,
            country=country,
            abv=abv,
            net_contents=net_contents,
            government_warning=government_warning,
        )
        extracted = vision_service.extract_label(processed_image, "image/jpeg")
        result = verify_label(application, extracted)
        result.latency_ms = _elapsed_ms(started_at)
        _log_verification_latency(result)
        return result
    except HTTPException:
        raise
    except VisionInputError as exc:
        raise HTTPException(status_code=400, detail={"message": str(exc)}) from exc
    except Exception as exc:
        logger.exception("Verification request failed.")
        raise HTTPException(
            status_code=500,
            detail={"message": "We could not verify this label right now. Please try again."},
        ) from exc


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
