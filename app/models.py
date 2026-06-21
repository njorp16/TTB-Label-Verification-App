from typing import Literal

from pydantic import BaseModel


FieldStatus = Literal["PASS", "FAIL"]
VerificationVerdict = Literal["PASS", "NEEDS_REVIEW"]
BatchItemOutcome = Literal["PASS", "NEEDS_REVIEW", "ERROR"]


class ApplicationData(BaseModel):
    brand_name: str
    product_class: str
    producer: str
    country: str
    abv: str
    net_contents: str
    government_warning: str


class ExtractedLabel(BaseModel):
    brand_name: str | None = None
    product_class: str | None = None
    producer: str | None = None
    country: str | None = None
    abv: str | None = None
    net_contents: str | None = None
    government_warning: str | None = None


class FieldResult(BaseModel):
    field_name: str
    expected: str
    actual: str
    status: FieldStatus
    reason: str
    score: float | None = None


class VerificationResult(BaseModel):
    verdict: VerificationVerdict
    fields: list[FieldResult]
    latency_ms: int | None = None


class BatchSummary(BaseModel):
    passed: int
    needs_review: int
    total: int


class BatchItemResult(BaseModel):
    index: int
    filename: str
    outcome: BatchItemOutcome
    result: VerificationResult | None = None
    error: str | None = None


class BatchVerificationResult(BaseModel):
    summary: BatchSummary
    items: list[BatchItemResult]
    latency_ms: int
